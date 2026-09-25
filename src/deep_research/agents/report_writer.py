"""The Report Writer (spec §6): prose from verified findings only, cited by label.

The model writes the executive summary and a few short sections, citing
findings by the labels one registry stamps. Code builds everything else --
the key facts table, duplicates and revisions, the Not found list, the
labels and the sources. Code keeps only the two mechanical checks spec §6.2
leaves it: a point cites at least one known label, and the length limit.
Every other question about a sentence's wording -- its numbers, dates,
scope, organisation, forecast or actual -- is the Statement Check's job
(§5.4, decision D8), judged once for every drafted sentence together.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from pydantic import Field, JsonValue, ValidationError

from deep_research.agents.base import (
    OUTPUT_LIMIT_RETRY_EFFORT,
    AgentCompleter,
    AgentRun,
    BaseAgent,
)
from deep_research.agents.errors import AgentConfigurationError, agent_error
from deep_research.agents.events import agent_event
from deep_research.agents.identity import finding_fingerprint
from deep_research.agents.planner import Clock, utc_now
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
from deep_research.agents.sources import publisher_identity
from deep_research.agents.steps import ReActRun, summarize_text
from deep_research.agents.verified_facts import (
    answered_target_ids,
    citable_findings,
    claimed_organisation,
    fact_rows,
    not_found_targets,
)
from deep_research.agents.wording import stated_role
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import Tracker
from deep_research.providers import ChatMessage, ProviderError, ProviderOutputLimitError, StructuredOutputError
from deep_research.tools.base import BaseTool, ToolResult
from deep_research.utils.config import AgentRuntimeConfig, EffectiveModelConfig
from deep_research.utils.types import (
    ContractModel,
    EvidenceTarget,
    FactRow,
    Finding,
    NotFoundTarget,
    ReadRecord,
    RejectedDraftPoint,
    ReportComposition,
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
    SubTopic,
)

REPORT_WRITER_NAME = "report_writer"
DEFAULT_MAX_SECTIONS = 4
MAX_POINT_CHARS = 600
_SECTION_TITLE_CHARS = 120
# One defect's own sentence as the re-draft's request carries it: enough to
# state what is wrong, bounded because the packet spends characters on it and
# the defect's id, kind and scope ids are what identify it.
_DEFECT_PROBLEM_CHARS = 400
# F10: the first attempt runs at the resolved profile's effort (config.yaml
# model_overrides.report_writer, the one effort source); a truncated draft is
# asked once more at high, the retry this writer's own call makes.
_WRITER_ATTEMPT_EFFORTS: tuple[str | None, ...] = (None, OUTPUT_LIMIT_RETRY_EFFORT)

REPORT_WRITER_SYSTEM_PROMPT = (
    "You write the reader-facing prose of a research report from verified findings. "
    "Every finding you may cite is listed with a label (F01, F02, ...), its verbatim "
    "snippet, and each of its figures with the figure's verified period, kind (actual "
    "or forecast), organisation and reader label. Code builds the key facts table, the "
    "Not found list and the sources; you write the executive summary and a few short "
    "explanatory sections. A finding with no figure is listed with its snippet and the "
    "body it is attributed to; cite it for what the snippet states, attributed as the "
    "line says."
)

REPORT_WRITER_INSTRUCTION = (
    "Rules:\n"
    "- Cite by label only: every point lists in finding_labels the one to three labels it "
    "rests on. Never write a URL.\n"
    "- State a forecast with a forecast verb (\"projects\", \"expects\", \"forecasts\"), never "
    "as a completed outcome.\n"
    "- Use only the numbers and dates of the cited findings.\n"
    "- Use the scope words the finding states (\"households only\", \"all segments\"), never "
    "the question's.\n"
    "- Name only organisations and publications the cited findings name.\n"
    "- A figure belongs to the subject its finding names (a product, a place, a version): "
    "never move a figure from one subject to another, and name the subject as the finding "
    "names it.\n"
    "- For a figure or a statement one site relays from another organisation, name the "
    "organisation and the site (\"according to the Example Institute, as reported by the "
    "Example Gazette\").\n"
    "- Credit a figure the way its label does: a figure labelled as the organisation's own "
    "is that organisation's; a relay is \"according to <organisation>, as reported by "
    "<site>\"; a figure whose label says the source does not attribute it is stated as the "
    "page's figure (\"<site> reports ...\") and is never credited to a body the label does "
    "not name.\n"
    "- The executive summary answers the question's parts in the order the "
    "question asks them, one point per fact, each part in the form its evidence "
    "takes: a figure with its period and its organisation; a forecast with its "
    "issuer and release; items with their attributes (an option with its price "
    "and its rating per criterion, a change with its date) grouped or ordered on "
    "a basis the question or the findings give — price, date, size — and stated "
    "as the findings state them; reasons, mechanisms or provisions as the cited "
    "findings state them. Every judgement, ranking or recommendation is "
    "attributed to the finding's organisation as its finding names it; where "
    "findings disagree, give each. The report makes no pick, ranking or verdict "
    "of its own and adds no criterion the question did not name. When the "
    "findings hold more items than the summary can carry, give the ones the "
    "findings themselves rank or emphasise most and say the list is partial. "
    "Never state the same figure twice.\n"
    "- Every required target the packet's list of required targets names with a label must be "
    "stated: its question is answered by a point in the executive summary that cites that "
    "label. A target the list names as not found is not stated, and its absence is disclosed "
    "there. An answered required question left unstated is a missing answer, not a detail.\n"
    f"- Keep every point under {MAX_POINT_CHARS} characters. A longer point is split "
    "at a sentence boundary and every piece kept with the same citations, so a "
    "sentence that long on its own is refused: write one fact per point.\n"
    "- A section title names its subject in the cited findings' own words and never "
    "states the report's own judgement or status (\"pending\", \"confirmed\", "
    "\"verified\"): a title claims nothing the findings do not.\n"
    "- Never print a page's own metadata or disclaimer as a point: a copyright, "
    "revision or legal line, or the date the document or page itself takes effect, "
    "belongs to the evidence log and answers no question.\n"
    "- Never state a judgement while dropping the criterion it is measured by: a "
    "judgement the finding measures by a criterion the snippet does not name is "
    "not an answer, so state the criterion with it or leave the judgement out.\n"
    "- Every section adds something the executive summary does not carry: "
    "never write a section that only lists, restates or re-attributes the "
    "findings.\n"
    "- At most four sections, each explaining what the findings state and the "
    "summary needs — basis or scope, revisions, definitions, mechanisms, "
    "disagreements, caveats — and nothing the findings do not state.\n"
    "- Never write verdict or corroboration words: verified, confirmed, corroborated, "
    "independently, insufficient evidence, contested."
)

_WRITER_REPLY_EXAMPLES = (
    (
        "Example input: F01 | figure 1: 12 percent | period 2024 | kind actual | organisation "
        "Example Statistical Agency | label: Example Statistical Agency's own figure; actual; "
        "released 2025-02-01",
        '{"executive_summary":[{"text":"The Example Statistical Agency reports a 12 percent '
        'reduction in 2024.","finding_labels":["F01"]}],"sections":[]}',
    ),
)


class WriterPointDraft(ContractModel):
    text: str
    finding_labels: list[str] = Field(default_factory=list)


class WriterSectionDraft(ContractModel):
    title: str
    points: list[WriterPointDraft] = Field(default_factory=list)


class ReportWriterDraft(ContractModel):
    """The provider-facing reply: prose and labels, nothing else."""

    executive_summary: list[WriterPointDraft] = Field(default_factory=list)
    sections: list[WriterSectionDraft] = Field(default_factory=list)


class ReportWriterTask(AgentTask):
    session_id: str
    iteration: int = 0
    max_extra_passes: int = 0
    question: str
    as_of: str = ""
    scope: str = ""
    generated_on: str = ""
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
    defects: list[ReviewDefect] = Field(default_factory=list)
    """The material defects this draft must answer, in the review's own order.

    Empty for a first draft. Non-empty means the graph bought this writer
    re-run *for* these defects: the run already composed a report, the terminal
    review scored it and named what is materially wrong, and no research pass
    can fix it from the same evidence — so the defect list is what the request
    adds, and a re-draft that ignored it could only repeat the draft it
    replaces.
    """


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


# --- Task 4.1: the publication names ----------------------------------------
#
# The writer owns the names of the three artifacts one pass publishes, because
# it is the pass that composes them: the reader report, its evidence ledger,
# and the quality record are one name family, derived from one slug, so no
# caller has to remember a second one.

# Characters kept verbatim in a report filename. Narrow on purpose:
# WriteDocumentTool rejects absolute paths and traversal segments, and a
# rejected write would lose the artifact.
_FILENAME_SAFE = frozenset("abcdefghijklmnopqrstuvwxyz0123456789-")


def report_filename(*, session_id: str, iteration: int) -> str:
    """Return a traversal-free ``.md`` filename for one reader report.

    ``session_id`` reaches this from state and may hold anything, so it is
    slugged rather than trusted.
    """
    if iteration < 0:
        raise ValueError("iteration must not be negative")
    slug = "".join(
        character if character in _FILENAME_SAFE else "-"
        for character in session_id.strip().casefold()
    ).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return f"report-{slug or 'session'}-{iteration}.md"


def evidence_report_filename(*, session_id: str, iteration: int) -> str:
    """Return the evidence ledger's filename for the same pass.

    Deliberately derived from the reader report's name rather than slugged a
    second time, so the two artifacts of one pass can never disagree about
    which session and iteration they belong to.
    """
    stem = report_filename(session_id=session_id, iteration=iteration)
    return f"{stem.removesuffix('.md')}-evidence.md"


def quality_report_filename(*, session_id: str, iteration: int) -> str:
    """Return the quality record's filename for the same pass.

    Derived the same way the ledger's name is, so the three artifacts of one
    publication are one name family: nothing about which set a file belongs to
    depends on a caller remembering a second slug.
    """
    stem = report_filename(session_id=session_id, iteration=iteration)
    return f"{stem.removesuffix('.md')}-quality.json"


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


def registry_lines(label: str, finding: Finding) -> list[str]:
    lines = [f"## {label}: {finding.source_title} ({publisher_identity(finding.source_url)})",
             f"snippet: {finding.snippet or finding.content}"]
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
        name = finding.attributed_issuer or publisher_identity(finding.source_url)
        date = finding.statement_date or finding.release_date or finding.data_period
        lines.append(
            f"{label} | statement | attributed to {name} | "
            f"{stated_role(finding.snippet or finding.content)}"
            + (f" | dated {date}" if date else "")
        )
    return lines


def _answering_labels(task: ReportWriterTask, target_id: str) -> str:
    """The labels of the registry findings that answer ``target_id``.

    R2: paired against the registry's own ``(label, Finding)`` rows rather
    than through a ``finding_fingerprint``-keyed lookup. Two distinct
    revision editions of one page -- an unchanged URL, sub-topic and content,
    with only the structured figure or its release differing -- share a
    fingerprint; a fingerprint-keyed map collapses them to whichever edition
    was inserted last and silently drops the other edition's label.
    """
    wanted = set(task.answered.get(target_id, ()))
    labels = [label for label, finding in task.registry if finding_fingerprint(finding) in wanted]
    return ", ".join(labels) or "not found"


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

    The sentence is the reviewer's own text and is clamped here rather than
    passed through whole: it is model output, and the packet spends characters
    on everything it carries. The id, the kind and the scope ids are enumerated
    or plan-minted, so the writer can act on a defect whose sentence was cut.
    """
    lines: list[str] = []
    for defect in defects:
        scope = ", ".join([*defect.target_ids, *defect.statement_ids]) or "the whole report"
        lines.append(
            f"- {defect.defect_id} ({defect.kind}; {scope}): "
            + summarize_text(defect.problem, limit=_DEFECT_PROBLEM_CHARS)
        )
    return "\n".join(lines)


def writer_messages(task: ReportWriterTask) -> list[ChatMessage]:
    targets = "\n".join(
        f"- {t.target_id}: {t.question} (" + _answering_labels(task, t.target_id) + ")"
        for t in task.targets if t.required
    ) or "(none)"
    registry = "\n\n".join("\n".join(registry_lines(label, f)) for label, f in task.registry) or "(none)"
    static = [
        f"# Rules\n{REPORT_WRITER_INSTRUCTION}",
        "# Reply format\n" + render_structured_reply_format(_WRITER_REPLY_EXAMPLES),
    ]
    material = [
        f"# Question\n{task.question}",
        f"# Required targets and the findings that answer them\n{targets}",
    ]
    if task.defects:
        material.append(f"# Defects to fix\n{_defect_lines(task.defects)}")
    material.append(f"# Verified findings\n{registry}")
    return [ChatMessage(role="developer", content=REPORT_WRITER_SYSTEM_PROMPT),
            ChatMessage(role="user", content=render_structured_request(static, material))]


#: Where a drafted point too long for ``MAX_POINT_CHARS`` may be cut: after a
#: sentence or clause end, and only where what follows starts a word. The cut is
#: verbatim -- the pieces are the drafted text's own words -- so a split never
#: invents, drops or reorders a word and every piece keeps the point's citations.
_CLAUSE_BOUNDARY = re.compile(r"(?<=[.;!?])\s+(?=[\"“(\[]?\S)")


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

    Improvement 2 (the run-2 audit): a drafted point longer than the bound used
    to be refused whole, and the reader lost a verified obligation list that way.
    A piece that begins mid-sentence -- after a ';' inside a list -- is printed
    with the point's own introduction in front of it (F3), so no piece stands
    without its subject or its conditions; that introduction is the same point's
    verbatim text, repeated, and no piece is paraphrased. A point whose own
    clause is longer than the bound, or whose introduction plus one clause is,
    cannot be cut into pieces that stand alone: it returns ``None`` and the
    caller refuses it as before, rather than print a fragment.
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
        # A clause that followed a ';' starts mid-sentence: print it with the
        # introduction, so it never stands without its subject.
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
    """One drafted point that cleared the two mechanical rules (§6.2) and
    is waiting on the Statement Check's verdict."""

    key: str                     # "S001"; also the final ReportStatement.statement_id
    where: str                   # "summary[0]" or "sections[2].points[1]"
    text: str                    # drafted, whitespace-collapsed
    finding_labels: list[str]
    findings: list[Finding]


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


async def compose_written_report(
    task: ReportWriterTask,
    draft: ReportWriterDraft | None,
    *,
    provider: AgentCompleter,
    fingerprint: Callable[[str], object] | None = None,
    batch_size: int | None = None,
    concurrency: int | None = None,
) -> ReportComposition:
    """Build every candidate point, judge its wording with one Statement
    Check call, and compose (spec §6.1-6.2, §5.4, decision D8).

    The Context Check (§5.2) has already verified each figure's
    organisation, kind, period and scope, and code attaches those to every
    figure's reader label, so the label carries the verified provenance
    whatever the prose says. Code keeps only the two mechanical rules §6.2
    leaves it: a point cites at least one known label, and the length
    limit. Everything else about a sentence's wording -- its numbers,
    dates, scope, organisation, forecast or actual -- is judged once for
    every candidate sentence together by the Statement Check (§5.4), never
    by code pattern.

    ``batch_size`` and ``concurrency`` are the bounds the Statement Check
    runs under; ``None`` means the check's own module defaults. The writer
    passes its configured ``agents.verifier_batch_size`` and
    ``agents.verifier_concurrency`` (PD-12).
    """
    by_label = dict(task.registry)
    ids = {label: finding_fingerprint(f) for label, f in task.registry}
    numbers = iter(range(1, 10_000))
    rejected: list[RejectedDraftPoint] = []

    def consider(point: WriterPointDraft, where: str) -> list[_Candidate]:
        """Every candidate one drafted point yields: itself, or its split pieces.

        Improvement 2: a drafted point over ``MAX_POINT_CHARS`` is split at a
        sentence boundary into pieces that keep its citations, rather than
        dropped -- the run-2 audit lost a whole verified obligation list that
        way. A point whose own clause is over the bound is refused as before,
        since the only cut left would fall inside a sentence.
        """
        drafted = " ".join(point.text.split())
        wanted = [label.strip() for label in point.finding_labels]

        def refuse(reason: str) -> None:
            rejected.append(RejectedDraftPoint(
                where=where, text=drafted, finding_labels=list(point.finding_labels), reason=reason,
            ))

        if not drafted:
            refuse("empty text")
            return []
        unknown = [label for label in wanted if label not in by_label]
        if unknown:
            refuse("unknown labels: " + ", ".join(unknown))
            return []
        if not wanted:
            refuse("cites no checked finding")
            return []
        pieces = [drafted] if len(drafted) <= MAX_POINT_CHARS else _split_oversize_point(drafted)
        if pieces is None:
            refuse(f"longer than {MAX_POINT_CHARS} characters")
            return []
        return [
            _Candidate(key=f"S{next(numbers):03d}",
                       where=where if len(pieces) == 1 else f"{where} part {n}",
                       text=piece, finding_labels=wanted,
                       findings=[by_label[label] for label in wanted])
            for n, piece in enumerate(pieces, start=1)
        ]

    summary_candidates: list[_Candidate] = []
    for n, drafted_point in enumerate(draft.executive_summary if draft else []):
        summary_candidates.extend(consider(drafted_point, f"summary[{n}]"))
    section_candidates: list[tuple[str, list[_Candidate]]] = []
    for s, section in enumerate((draft.sections if draft else [])[:DEFAULT_MAX_SECTIONS]):
        points: list[_Candidate] = []
        for n, drafted_point in enumerate(section.points):
            points.extend(consider(drafted_point, f"sections[{s}].points[{n}]"))
        section_candidates.append((" ".join(section.title.split())[:_SECTION_TITLE_CHARS], points))

    all_candidates = summary_candidates + [c for _, points in section_candidates for c in points]
    verdicts: Mapping[str, _Verdict | None] = {}
    check_errors: list[ResearchError] = []
    if all_candidates:
        # Imported at call time, not at module scope: the unit tests and the
        # offline audit harness both substitute the checker by assigning
        # ``evidence_verifier.check_statements``, and a module-level ``from``
        # would bind the real function before that assignment could be seen.
        # There is no import cycle either way -- ``evidence_verifier`` imports
        # this module's siblings, never this module.
        from deep_research.agents.evidence_verifier import (
            CONTEXT_CHECK_BATCH_SIZE,
            CONTEXT_CHECK_CONCURRENCY,
            StatementCheckItem,
            check_statements,
        )
        items = [
            StatementCheckItem(label=c.key, text=c.text, findings=c.findings,
                               labels=[_finding_label(f) for f in c.findings],
                               passages=statement_passages(c.findings, task.reads))
            for c in all_candidates
        ]
        try:
            verdicts, check_errors = await check_statements(
                provider, items, question=task.question, fingerprint=fingerprint,
                # PD-12: the Statement Check runs under the writer's own
                # configured bounds, the same two values the Evidence Verifier
                # bounds its Context Check with. The module constants are only
                # the fallback for a caller that has no config to offer.
                batch_size=(
                    CONTEXT_CHECK_BATCH_SIZE if batch_size is None else batch_size
                ),
                concurrency=(
                    CONTEXT_CHECK_CONCURRENCY
                    if concurrency is None
                    else concurrency
                ),
            )
        except (ProviderError, StructuredOutputError, ValidationError) as error:
            # §5.4: a failed batch keeps its sentences; the Statement Check
            # must never stop the run. check_statements's own retry ladder
            # already absorbs one batch's provider failure -- this is the
            # outer guard against the call itself failing before it can
            # return that per-batch accounting.
            check_errors = [agent_error(
                agent_name=REPORT_WRITER_NAME,
                error_type="report_writer_statement_check_failed",
                message="The report writer's statement check failed; every drafted point was kept unchanged.",
                details={"exception_type": type(error).__name__},
            )]

    stated_rows: set[str] = set()

    def finalize(candidate: _Candidate, *, dedup: bool) -> ReportPoint | None:
        def reject(reason: str) -> None:
            """Refuse the point, publishing the drafted text and the reason:
            a refused point never shows the reader text this run did not
            clear the mechanical rules on."""
            rejected.append(RejectedDraftPoint(
                where=candidate.where, text=candidate.text,
                finding_labels=list(candidate.finding_labels), reason=reason,
            ))

        verdict = verdicts.get(candidate.key)
        text = candidate.text
        if verdict is not None:
            correction = " ".join(verdict.corrected_text.split())
            if verdict.verdict == "corrected":
                # §6.2's length limit is one of the two mechanical rules code
                # keeps, and the correction is the text that would reach the
                # reader, so it is bounded exactly as a drafted point is.
                if len(correction) > MAX_POINT_CHARS:
                    reject(f"corrected text longer than {MAX_POINT_CHARS} characters")
                    return None
                if not correction:
                    reject(verdict.reason)
                    return None
                text = correction
            elif verdict.verdict == "inconsistent":
                reject(verdict.reason)
                return None
        cited_ids = {ids[label] for label in candidate.finding_labels}
        rows = {row.row_id for row in _carried_rows(text, cited_ids, task.facts, task.targets)}
        if dedup and rows and rows <= stated_rows:
            reject("restates " + ", ".join(sorted(rows)))
            return None
        stated_rows.update(rows)
        own_first = sorted(candidate.findings, key=lambda f: 0 if any(
            r.context is not None and r.context.attribution == "own"
            for r in (f.verification.figure_results if f.verification else [])) else 1)
        statement = ReportStatement(
            statement_id=candidate.key, text=text,
            finding_ids=[ids[label] for label in candidate.finding_labels],
            target_ids=sorted({t for t, fids in task.answered.items() if cited_ids & set(fids)}),
        )
        return ReportPoint(text=text, source_urls=list(dict.fromkeys(f.source_url for f in own_first)),
                           statement=statement)

    summary = [p for c in summary_candidates if (p := finalize(c, dedup=True)) is not None]
    sections: list[ReportSection] = []
    for title, points in section_candidates:
        built = [p for c in points if (p := finalize(c, dedup=False)) is not None]
        if built and title:
            sections.append(ReportSection(title=title, points=built))

    # §6.4 (Task 4.3): the Statement Check's own outcome for every sentence the
    # reader report prints, keyed by its statement id -- "unchecked" when its
    # batch failed or its label was omitted (D8). Derived from the printed
    # points rather than recorded as they are finalized: a section with no
    # title is dropped whole, and its points are no part of the report.
    printed = [*summary, *(point for section in sections for point in section.points)]
    statement_verdicts = {
        point.statement_id: (
            "unchecked" if (verdict := verdicts.get(point.statement_id)) is None
            else verdict.verdict
        )
        for point in printed
    }

    return ReportComposition(
        question=task.question, session_id=task.session_id, iteration=task.iteration,
        max_extra_passes=task.max_extra_passes, as_of=task.as_of, scope=task.scope,
        sub_topics=list(task.sub_topics), sources=list(task.sources), findings=list(task.findings),
        summary=summary, sections=sections, rejected=[r.reason for r in rejected],
        rejected_points=rejected, fact_rows=list(task.facts), not_found=list(task.not_found),
        finding_labels={label: finding_id for label, finding_id in ids.items()},
        statement_verdicts=statement_verdicts,
        generated_on=task.generated_on, errors=check_errors,
    )


def finding_memory_payload(finding: Finding, *, session_id: str) -> tuple[str, dict[str, JsonValue]]:
    """What long-term memory keeps of one cited finding of an accepted report."""
    figures = [
        f"{r.figure.value} {r.figure.unit} ({r.context.kind}, {r.context.period or 'period not stated'}, {r.context.organisation})"
        for r in (finding.verification.figure_results if finding.verification else [])
        if r.kept and r.context is not None
    ]
    metadata: dict[str, JsonValue] = {
        "session_id": session_id, "finding_id": finding_fingerprint(finding),
        "source_url": finding.source_url, "source_title": finding.source_title,
        "figures": "; ".join(figures),
        "verification": finding.verification.status if finding.verification else "unchecked",
        "context_unchecked": bool(finding.verification and finding.verification.context_unchecked),
    }
    return finding.snippet or finding.content, metadata


def report_written_event(result: WrittenReport) -> ResearchEvent:
    composition = result.composition
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
        },
    )


class ReportWriterAgent(BaseAgent[WrittenReport]):
    """Write the reader-facing prose of a research report from verified findings.

    Runs no ReAct loop: the model drafts prose citing findings by label, and
    everything structural -- the key facts table, duplicates and revisions,
    the Not found list, the labels and the sources -- is rendered locally
    from the verified snapshot, so the required sections exist and every fact
    is cited even when the model call fails.
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
        return REPORT_WRITER_SYSTEM_PROMPT

    def build_task(self, state: ResearchState) -> ReportWriterTask:
        """Bind this run to the verified findings recorded so far."""
        targets = [target for topic in state.sub_topics for target in topic.evidence_targets]
        findings = list(state.verified_findings)
        answered = answered_target_ids(citable_findings(findings), targets,
                                       sub_topics=state.sub_topics)
        return ReportWriterTask(
            instruction=state.original_question,
            session_id=state.session_id,
            iteration=state.iteration,
            max_extra_passes=state.max_extra_passes,
            question=state.original_question,
            as_of=report_as_of(findings=findings, reads=list(state.read_records.values())),
            scope=report_scope(state.sub_topics),
            generated_on=self._clock().date().isoformat(),
            sub_topics=list(state.sub_topics),
            targets=targets,
            findings=findings,
            sources=list(state.evaluated_sources),
            registry=finding_registry(findings, targets, state.sub_topics),
            facts=fact_rows(findings, targets, state.sub_topics),
            not_found=not_found_targets(state.sub_topics, answered, state.acquisition_state_by_target),
            answered=answered,
            reads=dict(state.read_records),
            defects=material_defects(state.report_review),
        )

    async def draft(self, task: ReportWriterTask) -> tuple[ReportWriterDraft | None, list[ResearchError]]:
        """Ask the model for the report's prose, at most twice (F10).

        No provider call when nothing is citable: a report with no verified
        finding has nothing a model could write about, and asking anyway
        would only invite it to invent a citation no finding backs.
        """
        if not task.registry:
            return None, [agent_error(
                agent_name=REPORT_WRITER_NAME,
                error_type="report_writer_no_verified_findings",
                message="No verified finding could be cited; the report is the recorded facts alone.",
            )]
        messages = writer_messages(task)
        errors: list[ResearchError] = []
        for attempt, effort in enumerate(_WRITER_ATTEMPT_EFFORTS, start=1):
            self.fingerprint_call(ReportWriterDraft.__name__, reasoning_effort=effort)
            try:
                if effort is None:
                    draft = await self.provider.complete_structured(
                        messages, ReportWriterDraft, agent_name=self.name,
                    )
                else:
                    draft = await self.provider.complete_structured(
                        messages, ReportWriterDraft, agent_name=self.name, reasoning_effort=effort,
                    )
            except ProviderOutputLimitError as error:
                errors.append(agent_error(
                    agent_name=REPORT_WRITER_NAME,
                    error_type="report_writer_output_limit",
                    message="The report writer's draft reached the provider's output limit.",
                    details={"exception_type": type(error).__name__, "attempt": attempt},
                ))
                continue
            except (ProviderError, StructuredOutputError, ValidationError) as error:
                errors.append(agent_error(
                    agent_name=REPORT_WRITER_NAME,
                    error_type="report_writer_provider_error",
                    message="The report writer's draft failed.",
                    recoverable=False,
                    details={"exception_type": type(error).__name__, "attempt": attempt},
                ))
                return None, errors
            return draft, errors
        errors.append(agent_error(
            agent_name=REPORT_WRITER_NAME,
            error_type="report_writer_provider_error",
            message="The report writer's draft was truncated on every attempt.",
            recoverable=False,
            details={"attempt": len(_WRITER_ATTEMPT_EFFORTS)},
        ))
        return None, errors

    async def _compose_result(self, task: ReportWriterTask, draft: ReportWriterDraft | None) -> WrittenReport:
        composition = await compose_written_report(
            task, draft, provider=self.provider, fingerprint=self.fingerprint_call,
            batch_size=self.config.verifier_batch_size,
            concurrency=self.config.verifier_concurrency,
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
        """Adapt drafting and composition to the ``BaseAgent`` hook.

        ``run`` calls the pieces directly so it can keep the errors this hook
        signature has nowhere to return.
        """
        del run
        if not isinstance(task, ReportWriterTask):
            raise AgentConfigurationError(
                "ReportWriterAgent.finalize requires a ReportWriterTask"
            )
        draft, _ = await self.draft(task)
        return await self._compose_result(task, draft)

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
        metadata: Mapping[str, JsonValue],
    ) -> ToolResult:
        """Save one verified claim through ``save_to_memory``.

        Called only by the terminal finalizer, and only for a report whose
        terminal quality status is ``accepted``.
        """
        tool = self._require_tool("save_to_memory")
        return await tool.execute(content=content, metadata=dict(metadata))

    async def run(self, state: ResearchState) -> AgentRun[WrittenReport]:
        """Draft and compose the report, recording every count.

        No ReAct loop runs, so the returned ``ReActRun`` is synthetic with
        zero iterations and zero tool calls. ``stop_reason`` is
        ``"provider_error"`` only when there was something to cite and the
        draft call itself failed: an empty verified snapshot is not a
        failure, it is an honest report of no evidence.
        """
        task = self.build_task(state)
        async with self.tracker.agent_span(self.name) as span:
            draft, errors = await self.draft(task)
            result = await self._compose_result(task, draft)
            errors = [*errors, *result.composition.errors]
            span.set_outputs({
                "agent_name": self.name,
                "statement_count": result.statement_count,
                "citation_count": result.citation_count,
                "refused_count": result.refused_count,
            })
        react = ReActRun(
            agent_name=self.name,
            stop_reason="provider_error" if draft is None and task.registry else "finished",
            errors=errors,
        )
        return AgentRun(
            agent_name=self.name,
            result=result,
            react=react,
            errors=errors,
            state_update={
                "report": result.markdown,
                "report_evidence": result.evidence_markdown,
                "composition": result.composition,
                "unique_source_count": result.citation_count,
                "errors": list(errors),
                "events": [report_written_event(result)],
            },
            call_fingerprints=dict(self._call_fingerprints),
        )
