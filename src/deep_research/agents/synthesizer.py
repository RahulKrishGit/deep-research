"""The Synthesizer: turn checked evidence into the two final artifacts.

Like every other agent here, the provider is asked for a constraint-free
draft (``ReportDraft``) and never for a domain type. What it returns is a list
of *claim-linked points*: short statements, each naming the checked claims it
rests on. Everything structural — the as-of and scope declaration, the
constraint table's evidence columns, the inline citation markers, the
reference list, the uncertainty grouping, the limitations block, and the whole
evidence ledger — is rendered locally by ``agents.report`` from recorded
evidence, so the required sections exist and every settled statement is cited
even when the model call fails.

Two consequences are deliberate:

* a point is validated against the canonical checked-claim registry before it
  is rendered. A point with no known checked claim, or one citing a URL the
  claims it names do not carry, is refused and named in the ledger rather than
  printed. Text with no source behind it survives only in the explicitly
  labeled gap and methodology fields;
* synthesis writes nothing. It composes both Markdown strings into state; the
  terminal finalizer publishes them and owns long-term memory.
"""

from __future__ import annotations

import calendar
import re
from collections.abc import Mapping, Sequence

from pydantic import Field, JsonValue

from deep_research.agents.base import (
    OUTPUT_LIMIT_RETRY_READINGS,
    OUTPUT_LIMIT_RETRY_EFFORT,
    OUTPUT_LIMIT_RETRY_OUTCOMES,
    AgentCompleter,
    AgentRun,
    BaseAgent,
)
from deep_research.agents.claim_clusters import resolved_verdict_and_status
from deep_research.agents.errors import (
    AgentConfigurationError,
    agent_error,
    agent_provider_failure_details,
)
from deep_research.agents.events import agent_event
from deep_research.agents.planner import Clock, utc_now
from deep_research.agents.prompts import (
    REPORT_INSTRUCTION,
    SYNTHESIZER_SYSTEM_PROMPT,
    AgentTask,
    render_finding_digest,
    render_report_claim_packet,
    render_source_quality,
    render_structured_reply_format,
)
from deep_research.agents.report import (
    ANSWER_TABLE_COLUMNS,
    QUALITY_STATUS_NOT_GATED,
    ReportComposition,
    ReportConstraint,
    ReportPoint,
    ReportSection,
    _YEAR,
    _display_clamp,
    canonical_claims,
    fit_report_composition,
    reader_citations,
    render_evidence_ledger,
    render_limitations,
    render_reader_report,
    report_as_of,
    report_scope,
    validate_report_statements,
)
from deep_research.agents.sources import normalize_source_url
from deep_research.agents.steps import ReActRun, summarize_text
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import Tracker
from deep_research.providers import (
    ChatMessage,
    ProviderError,
    ProviderOutputLimitError,
)
from deep_research.tools.base import BaseTool, ToolResult
from deep_research.utils.config import AgentRuntimeConfig, EffectiveModelConfig
from deep_research.utils.types import (
    ANSWERING_STATEMENT_MODES,
    EVIDENCE_BADGE_LABELS,
    AcquisitionState,
    AnswerContract,
    Claim,
    ClaimCluster,
    ContractModel,
    EvidenceDisposition,
    EvidenceTarget,
    EvidenceUnit,
    Finding,
    ReportAnswerRow,
    RejectedDraftPoint,
    ReportStatement,
    ResearchError,
    ResearchEvent,
    ResearchState,
    ResearchStateUpdate,
    ScoredSource,
    StatementMode,
    SubTopic,
    clusters_for_claims,
    derive_statement,
    dimensions_by_target,
)

SYNTHESIZER_NAME = "synthesizer"

DEFAULT_MAX_SECTIONS = 8
SYNTHESIS_FINDING_DIGEST = 40
SYNTHESIS_CLAIM_DIGEST = 40
# The checked-claim packet is bounded by characters, not by a position in the
# state: the first 40 findings of a run are not the 40 most load-bearing
# claims. Every claim that does not fit is counted in the prompt rather than
# silently dropped from it. The full canonical snapshot remains in the
# evidence ledger, while validation resolves only labels shown in this packet.
SYNTHESIS_CLAIM_PACKET_CHARS = 12000
SYNTHESIS_OPEN_QUESTIONS_CHARS = 2000

# The draft call's own effort ladder: opened at "high", not the provider's
# default. Both attempts at the default effort spent their whole output
# budget on reasoning tokens and were truncated before a single point was
# written on a measured live pass; the "high" retry that followed is what
# produced every draft. The retry ladder stays — a truncated first attempt
# is still re-asked once, under the same output budget.
_DRAFT_ATTEMPT_EFFORTS: tuple[str | None, ...] = (
    OUTPUT_LIMIT_RETRY_EFFORT,
    OUTPUT_LIMIT_RETRY_EFFORT,
)

# A claim must be verified *and* at least this confident before the terminal
# finalizer may keep it for future sessions. The spec says "high-confidence
# final claims" without defining either bound; this is the only definition
# this codebase can compute.
DEFAULT_MEMORY_CONFIDENCE = 0.7
DEFAULT_MAX_MEMORY_FINDINGS = 10

# Render bounds: each clamps one rendered cell, bullet, or audit record, so a
# long model-written sentence cannot push a rendered table or a ledger row off
# the page. They are bounds for *display* and never for a model's input: a
# fragment shown to a writer is a fragment the writer may restate, so the
# packet that reaches a model carries text whole and bounds how much of it
# fits by count instead.
#
# A value that reaches an artifact is clamped by ``report._display_clamp``,
# which cuts between words and marks the cut. The audited report published
# "…at the end of 2026, c..." and "…with Texas and California expect...": a
# fragment of a word reads as the source's own wording, and nothing in the
# bullet said the sentence stopped there. The bounds whose text goes to a
# *prompt* keep ``summarize_text``, whose ellipsis marks its cut the way the
# packet omission markers do.
_POINT_CHARS = 600
_SECTION_TITLE_CHARS = 120
_GUIDANCE_CHARS = 200
_CLAIM_TEXT_CHARS = 240
_CELL_CHARS = 120

# What a factual assertion introduces that a paraphrase does not. A statement
# may reword its evidence freely — that is what prose is for — but a figure or
# a named entity the evidence does not carry is a new fact, and a new fact is
# not something a report may assert on its own authority.
#
# The figure pattern captures the number alone; ``_significant_figures``
# attaches a following word only when that word is a recognised unit, so a
# figure token can never swallow the next ordinary word ("1,200 in total" is
# the figure 1,200, not the token "1,200 in").
_FIGURE_PATTERN = re.compile(r"\d[\d,.'\u2019]*")
# A capitalised word that is not an acronym is a name candidate only when it
# is not the first word of a sentence: a sentence opener is capitalised by
# position, and "Charge" is indistinguishable from "California" without an
# English lexicon. The alternative — a stopword list that has to contain every
# word a sentence may open with — is not a list anyone can audit. The accepted
# cost is that a sentence-initial MIXED-case place name in prose ("California
# added capacity") stays undetected; Task 10 carries that residual.
_PROPER_NOUN_PATTERN = re.compile(r"\b[A-Z][A-Za-z][\w'-]*\b")
# An acronym is the exception to the position exemption, because no ordinary
# sentence opener is all-caps: "EU" is a name in the middle of a sentence and
# at the start of one, and the four-character floor that used to hide it was a
# length rule standing in for a case rule.
_ACRONYM_PATTERN = re.compile(r"[A-Z]{2,}")
# What opens a sentence, for the position exemption: the start of the text, a
# terminator, a line-initial bullet or blockquote marker, or an opening quote
# or bracket. A dash *inside* a line is not an opener — an ordinary word after
# an em-dash is lowercase, so a capitalised word there is a name — and neither
# is a closing bracket or the apostrophe inside a word.
_SENTENCE_INITIAL = re.compile(
    r"(?:^|[.!?:]\s+"
    r"|(?:^|\n)\s*[-*>\u2013\u2014]\s+"
    r"|(?:^|\s)[\u201c\u2018\"'(\[]\s*)$"
)
# The operation words a derivation has to name to count as one: a recorded
# basis states premises *and* what was done with them.
_DERIVATION_MARKERS = (
    "=",
    "convert",
    "conversion",
    "sum",
    "total",
    "average",
    "mean",
    "ratio",
    "share of",
    "scaled",
    "multiplied",
    "divided",
    "per ",
    " x ",
    "*",
    "×",
    "/",
)


def _is_significant_figure(token: str) -> bool:
    """True when a numeric token asserts a quantity rather than numbers a word.

    "890 GW", "12", "2025" and "0.76" are measurements: each carries a unit, a
    separator, or at least two digits. A bare single digit is a label — a list
    position, an ordinal, the full stop after a sentence — and treating one as
    an unsupported figure would refuse prose for its numbering rather than for
    its content.
    """
    stripped = token.strip().strip(".,'")
    if not stripped:
        return False
    if re.search(r"[A-Za-z%]", stripped):
        return True
    digits = re.sub(r"[^\d]", "", stripped)
    if len(digits) >= 2:
        return True
    return bool(re.search(r"[.,']", stripped))


def _figure_number(token: str) -> str:
    """The numeric value of a figure token, without its unit."""
    match = re.match(r"[\d,.']+", token)
    return (match.group(0) if match else token).strip(".,'")


def _significant_figures(text: str) -> list[str]:
    """Every figure token that asserts a quantity, with its unit attached.

    A unit is attached only when the next word is one this contract knows, so
    "890 GW" is one token while "1,200 in total" is the figure 1,200 followed
    by prose — which is what keeps a malformed token out of a disposition.
    """
    figures: list[str] = []
    for match in _FIGURE_PATTERN.finditer(text):
        number = match.group(0).strip(".,'\u2019")
        if not number:
            continue
        unit = re.match(r"\s+([A-Za-z%][A-Za-z%/-]*)", text[match.end() :])
        token = number
        if unit and unit.group(1).casefold() in _UNIT_WORDS:
            token = f"{number} {unit.group(1)}"
        if _is_significant_figure(token) and token not in figures:
            figures.append(token)
    return figures


def _derivation_premises(basis: str, tokens: set[str]) -> list[str]:
    """The figures a stated derivation rests on that the corpus attests."""
    return [
        _figure_number(figure)
        for figure in _significant_figures(basis)
        if _figure_number(figure) in tokens
    ]


def _names_an_operation(basis: str) -> bool:
    lowered = basis.casefold()
    return any(marker in lowered for marker in _DERIVATION_MARKERS)


# The words a capitalised token may be without naming anything: function words
# and connectives that a report's own argument is carried by. Kept to that
# role on purpose — the position exemption in ``unattested_atoms`` is what
# keeps ordinary sentence openers safe, and a list grown to cover every word a
# sentence may open with would make each of those words freely fabricable.
_ATTESTATION_STOPWORDS = frozenset(
    {
        "the", "and", "for", "with", "that", "this", "from", "into", "than",
        "then", "they", "their", "there", "these", "those", "have", "has",
        "had", "was", "were", "are", "is", "be", "been", "being", "not",
        "but", "its", "it's", "also", "such", "when", "which", "while",
        "would", "could", "should", "must", "may", "might", "can", "will",
        "about", "after", "before", "between", "during", "each", "every",
        "more", "most", "other", "some", "only", "over", "under", "very",
    }
)

# A recommendation is an action for someone else to take. It may be published
# in the answer section the question asked for — a constraint, a comparison
# conclusion, a decision — and nowhere else: a findings bullet that tells a
# reader what to do is a recommendation the research never evaluated.
_PRESCRIPTIVE_MARKERS = (
    "should ",
    "should,",
    "ought to",
    "must ",
    "recommend ",
    "we advise",
    "policymakers ",
    "needs to ",
    "have to ",
)

# A limitation note may only describe an evidence state this pass recorded.
# Each entry is the word a reader would use, the vocabulary the note may
# contain, and the recorded facts that license it; "never assert unavailable
# or truncated evidence without a recorded disposition" is this table.
_UNSUPPORTED_STATE_TOKENS: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    (
        "truncated",
        ("truncat", "incomplete", "cut off", "partial extraction"),
        ("truncat", "incomplete", "partial"),
    ),
    (
        "denied",
        ("denied", "blocked", "refused", "paywall"),
        ("denied", "refused", "blocked", "paywall"),
    ),
    (
        "missing",
        ("not acquired", "was not retrieved", "no read was", "not read"),
        ("not_acquired", "denied", "no_read", "missing", "unavailable"),
    ),
)

#: The enumerated dispositions one composition records for the statements it
#: refused, repaired, or could not check. Task 9 routes the returned ones.
STATEMENT_DISPOSITIONS = (
    "unsupported_cell",
    "unsupported_figure",
    "unsupported_recommendation",
    "unsupported_limitation",
    "unsupported_extrapolation",
    "unsupported_modality",
    "unsupported_qualifier",
    "unsupported_scope_fact",
    "duplicate_statement",
    "unlinked_statement",
    "returned_to_fact_checker",
    "answer_rows_derived",
    "citation_narrowed_to_claims",
    "omitted_bound_claim_attached",
)

# The modality markers a claim may carry and a statement may not drop. A
# statement may reword its evidence; it may not out-assert it. The audited
# report turned "capacity growth from battery storage could set a record" into
# "would set a record" and published the source's own uncertainty as a fact.
#
# Verb and adverb forms only. The noun uses of the same words hedge nothing —
# "the EIA battery storage forecast identifies its data vintage" is a title,
# not an uncertainty — and reading them as hedges refused correct statements
# whose point also cited a forecast for its citation. "preliminary" is absent
# for the same reason: it qualifies a document's title ("Preliminary Monthly
# Electric Generator Inventory"), not an assertion.
_HEDGE_PATTERN = re.compile(
    r"\b(?:could|might|possibly|potentially|perhaps|likely|unlikely|expects?|"
    r"expected|anticipates?|anticipated|projected|planned|intends?|"
    r"intended|suggests?|suggested|implies|implied|appears?|seems?|reportedly|"
    r"allegedly|estimates?|estimated|approximately|roughly)\b",
    re.IGNORECASE,
)
# The reporting verbs whose *verb* use hedges a statement and whose noun use
# names a document. "EIA forecasts 18.2 GW will be added" hedges; "EIA's
# February 24, 2025 forecast of 18.2 GW" is a citation label, and reading it as
# a hedge let the audited "would set a record" through the short-circuit. The
# lookahead is the discriminator: a reporting verb introduces a clause or a
# figure, a label is followed by a noun.
_HEDGE_REPORTING_PATTERN = re.compile(
    r"\b(?:forecasts?|projects?|predicts?)\b(?=\s+(?:that\b|\d))",
    re.IGNORECASE,
)
# ``may`` is the one hedge that collides with a month name, so it is matched
# on its own and refused when a date follows it.
_HEDGE_MAY_PATTERN = re.compile(r"\bmay\b(?!\s+\d)", re.IGNORECASE)

# The modal verbs a statement asserts with when it states as settled what its
# evidence hedged. Compared against the cited evidence, because a claim can
# have hardened its source already: the audited claim said "would" where the
# page said "could", so the claim alone cannot witness the loss.
_STRONG_MODALS = ("would", "will")

# What separates one assertion from the next inside a sentence. The modality
# and scope checks both decide per *clause*: a statement that hardens one
# clause is not excused by a hedge in another, and a note that asserts a
# source's boundary is not excused by a later clause about this pass. Without
# the conjunctions and dashes a single comma carried the whole decision.
# A terminator only separates when a space or the end follows it: "18.2" is a
# figure, not a sentence, and splitting on that full stop cut a reporting verb
# away from the clause it governs.
_CLAUSE_SPLIT = re.compile(
    r"[,;]|[:!?](?=\s|$)|\.(?=\s|$|[A-Z])"
    r'|[\u2014\u2013()\[\]\u201c\u201d"|/]'
    r"|(?<=\s)-(?=\s)"
    r"|\b(?:and|but|while|which|so|thus|therefore|though|although)\b"
)


def _clause_around(text: str, position: int) -> str:
    """The clause of ``text`` containing the character at ``position``."""
    start = 0
    for match in _CLAUSE_SPLIT.finditer(text):
        if match.start() > position:
            return text[start : match.start()]
        start = match.end()
    return text[start:]


# What a *source's* totals include or exclude. A note is refused only when it
# names one of these subjects *and* an inclusion or exclusion verb: the
# question's own plan carries a behind-the-meter topic, so every honest note
# about that gap contains the word, and "state-level breakdowns are not
# included in this report" is this pass describing itself, not a claim about
# what EIA counted.
_REPORTED_TOTALS = "reported totals"
_SCOPE_SUBJECTS = (
    _REPORTED_TOTALS,
    "behind-the-meter",
    "behind the meter",
    "front-of-meter",
    "front of meter",
)
_SCOPE_VERBS = (
    "exclud",
    "includ",
    "outside",
    "omit",
    "leave out",
    "leaves out",
)
# What marks a note as this pass describing *itself* rather than a source's
# boundary. A note about what was or was not researched is the framing a
# source-free note exists to carry, whatever words it borrows from the totals.
# The phrases that exempt an *introducing* clause. Narrower than
# ``_PASS_PHRASES``: only a clause about what this pass, this report or the
# plan did is the pass describing itself. "The checked evidence is thin" is a
# judgement about the evidence, and exempting the clause after it published
# "behind-the-meter storage is excluded from EIA's totals" — a source's
# boundary, which is the assertion this check exists to refuse.
_SCOPE_SELF_PHRASES = (
    "this pass",
    "this report",
    "the plan",
)
_PASS_PHRASES = (
    "this pass",
    "this report",
    "the plan",
    "not acquired",
    "checked evidence",
    "no read",
    "not retrieved",
)

# The capacity qualifiers a figure may or may not carry, and the units that
# make a token a capacity figure. A bare year is not one: "in 2024, developers
# installed 10.4 GW" and "developers installed 10.4 GW … in 2024" state the
# same fact, and a check that attached the qualifier to the year refused the
# second.
_QUALIFIER_WORDS = (
    "nameplate",
    "operational",
    "installed",
    "cumulative",
    "planned",
    "proposed",
    "existing",
)
_CAPACITY_UNITS = (
    "gw",
    "gws",
    "mw",
    "mws",
    "kw",
    "kws",
    "tw",
    "tws",
    "gwh",
    "mwh",
    "kwh",
    "twh",
    "gigawatt",
    "gigawatts",
    "megawatt",
    "megawatts",
    "kilowatt",
    "kilowatts",
    "terawatt",
    "terawatts",
    "gigawatt-hour",
    "gigawatt-hours",
    "megawatt-hour",
    "megawatt-hours",
)
_CAPACITY_FIGURE = re.compile(
    r"(\d[\d,.'\u2019]*)\s*(" + "|".join(_CAPACITY_UNITS) + r")\b",
    re.IGNORECASE,
)
# The selected evidence the writer's packet may carry. A *model-input* bound,
# deliberately its own constant and never a display bound: every claim with a
# selected passage keeps a share for its first one, the rest of the budget is
# spent on the passages behind it, and what does not fit is either cut between
# sentences with its withheld count stated or named — so the writer always
# knows what it was not shown and never restates a fragment. The rendered
# artifacts keep their own cell bounds. Without this, one claim whose read is a
# whole page adds roughly 22k input tokens by itself.
PACKET_SUPPORT_CHARS = 16000
# What a passage that cannot fit the budget at all is cut with. The cut lands
# on a sentence boundary — a partial sentence is exactly the fragment a
# writer must not restate.
_PACKET_OMISSION = " [… the passage continues; {count} further character(s) were not shown]"
# When the budget lands inside a sentence with no terminator before it, the cut
# is from the end of a *word* and the marker says it is not a sentence end.
_PACKET_OMISSION_UNSENTENCED = (
    " [… cut mid-sentence here; {count} further character(s) were not shown]"
)

# Characters kept verbatim in a report filename. Narrow on purpose:
# WriteDocumentTool rejects absolute paths and traversal segments, and a
# rejected write would lose the artifact.
_FILENAME_SAFE = frozenset("abcdefghijklmnopqrstuvwxyz0123456789-")

# Most load-bearing first: settled evidence before disputed evidence, disputed
# evidence before unanswered questions. Enumerated so the order can never come
# from dict or set iteration.
_VERDICT_ORDER = (
    "verified",
    "contradicted",
    "unverified",
    "insufficient_evidence",
)


class ReportPointDraft(ContractModel):
    """One claim-linked statement the model proposes, before validation.

    Declares no ``Field`` constraints for the same reason as
    ``planner.SubTopicDraft``: it is converted to a strict OpenAI JSON schema.
    ``claim_ids`` carries the registry labels the prompt showed (``C001``);
    the validator resolves each label to a canonical ``Claim.claim_id`` and
    never trusts a free-form id.

    ``basis`` is the model's own derivation, when it states one: a converted
    value, a summed total, a compared pair. Empty means the statement asserts
    no more than its evidence does, which is the default. A statement that
    introduces a figure its evidence does not carry is admitted only through a
    basis whose premises the evidence does state.
    """

    text: str
    claim_ids: list[str]
    source_urls: list[str]
    basis: str = ""


class ConstraintDraft(ContractModel):
    """One ranked constraint row, before validation.

    A constraint is a claim-linked point plus the two decision columns the
    reader report prints. ``deployment_mechanism`` and ``geography`` are
    provider-attested prose in the Task 6 contract: no typed evidence field
    locally proves their semantic contents. Task 7 checks them against the
    evidence the row cites instead of trusting them: a cell whose wording the
    cited evidence does not carry is repaired to ``not stated`` and recorded,
    because an uncited factual table cell is a factual assertion like any
    other.
    """

    constraint: str
    deployment_mechanism: str
    geography: str
    claim_ids: list[str]
    source_urls: list[str]


class AnswerRowDraft(ContractModel):
    """One row of the answer-kind table, before validation.

    ``subject`` and ``dimension`` are labels — what is being compared, and on
    what basis — and ``finding`` is the evidenced statement the row makes.
    The columns are chosen by the frozen answer form, not by the model, so a
    comparison question cannot be answered with a deployment ranking.
    """

    subject: str
    dimension: str
    finding: str
    claim_ids: list[str]
    source_urls: list[str]


class ReportSectionDraft(ContractModel):
    """One model-written findings section, before validation."""

    title: str
    points: list[ReportPointDraft]


class ReportDraft(ContractModel):
    """The provider-facing report schema for one synthesis pass."""

    executive_summary: list[ReportPointDraft]
    ranked_constraints: list[ConstraintDraft]
    sections: list[ReportSectionDraft]
    uncertainty_notes: list[str]
    answer_rows: list[AnswerRowDraft] = []


# One example. The report contract above already states the empty
# uncertainty-notes case, and there is no "empty report" case to show. The
# label ``C001`` is the addressing scheme the real request uses.
_REPORT_REPLY_EXAMPLES = (
    (
        "Example input: the checked claim labelled C001 records a measured "
        "reduction, from https://evidence.example.test/report, with no "
        "evidence from other settings.",
        '{"executive_summary":[{"text":"The supplied evidence supports a '
        'measured reduction.","claim_ids":["C001"],"source_urls":'
        '["https://evidence.example.test/report"],"basis":""}],'
        '"ranked_constraints":'
        '[{"constraint":"Charge for road use inside the measured zone.",'
        '"deployment_mechanism":"area licence with camera enforcement",'
        '"geography":"not stated","claim_ids":["C001"],"source_urls":'
        '["https://evidence.example.test/report"]}],"sections":[{"title":'
        '"Measured result","points":[{"text":"The example study reports the '
        'measured result and its stated limits.","claim_ids":["C001"],'
        '"source_urls":["https://evidence.example.test/report"],'
        '"basis":""}]}],"answer_rows":[],'
        '"uncertainty_notes":["Evidence from other settings was not '
        'supplied."]}',
    ),
)


class SynthesisTask(AgentTask):
    """An ``AgentTask`` bound to the evidence its report is written from.

    Carrying the evidence on the task is what lets ``finalize(task, run)``
    compose a report without the agent holding mutable state across await
    points — the same reason ``SourceEvaluationTask`` exists. It is also the
    whole input of both renderers, which is why the plan's topics, the
    evidence timestamps, and the run's recorded errors travel here.

    Task 7 adds the frozen answer contract and the evidence registries: the
    answer form decides the report's structure, and the evidence units and
    claim clusters are what turn a statement's ids into citations. The
    dispositions and acquisition states travel too, because a limitation may
    only be asserted when the pass recorded the fact it describes.
    """

    session_id: str = Field(min_length=1)
    iteration: int = Field(default=0, ge=0)
    max_iterations: int = Field(default=0, ge=0)
    as_of: str = ""
    generated_on: str = ""
    """The run clock's date as ISO ``YYYY-MM-DD`` — not the evidence's date.

    The reader prints this on its **Generated on** line, next to the **As of**
    line that names the newest recorded *evidence* timestamp. The two are
    different facts: a question that names a date is answered *as of* that
    date, but the document was still printed on the run's own day. Stamped by
    ``build_task`` from the injected clock, so a test can pin it.
    """
    scope: str = ""
    sub_topics: list[SubTopic] = []
    claims: list[Claim] = []
    sources: list[ScoredSource] = []
    findings: list[Finding] = []
    limitations: list[str] = []
    errors: list[ResearchError] = []
    answer_contract: AnswerContract | None = None
    evidence_units: dict[str, EvidenceUnit] = Field(default_factory=dict)
    claim_clusters: dict[str, ClaimCluster] = Field(default_factory=dict)
    evidence_dispositions: list[EvidenceDisposition] = Field(
        default_factory=list
    )
    acquisition_state_by_target: dict[str, AcquisitionState] = Field(
        default_factory=dict
    )
    # The exact bounded registry shown to the provider.  The full ``claims``
    # snapshot remains available for the evidence ledger, but settled draft
    # points may resolve labels only through this prompt-visible subset.
    claim_packet: list[tuple[str, Claim]] | None = None

    @property
    def targets(self) -> list[EvidenceTarget]:
        """Every evidence target this run owes an answer, in plan order."""
        return [
            target
            for topic in self.sub_topics
            for target in topic.evidence_targets
        ]


class SynthesizedReport(ContractModel):
    """The two composed artifacts, with their own counts.

    Never sent to the provider — ``ReportDraft`` is. Do not route this agent
    through ``complete_output``. ``markdown`` and ``evidence_markdown`` are
    authoritative; ``path`` and ``evidence_path`` name the files the terminal
    finalizer publishes, so ``path`` stays ``None`` until publication exists
    and synthesis itself writes nothing at all. ``evidence_path`` here is the
    name the finalizer *will* publish the ledger under; it is not the state
    field of the same name, which only the finalizer writes.

    ``composition`` is the typed input both artifacts were rendered from. It
    travels into state so the graph's quality pass judges the exact points
    and canonical evidence this pass composed, rather than re-deriving them
    from Markdown.
    """

    markdown: str = ""
    path: str | None = None
    evidence_markdown: str = ""
    evidence_path: str | None = None
    composition: ReportComposition | None = None
    section_count: int = Field(default=0, ge=0)
    citation_count: int = Field(default=0, ge=0)
    unique_source_count: int = Field(default=0, ge=0)
    unique_claim_count: int = Field(default=0, ge=0)


def limitation_reasons(state: ResearchState) -> list[str]:
    """Enumerate every limitation this pass must disclose, in report order.

    Purely a function of recorded state, so "the report is honest about weak
    evidence" is testable without a provider. Keys are
    ``report.LIMITATION_REASONS`` keys; ``render_limitations`` raises on
    anything else.
    """
    reasons: list[str] = []
    if state.errors:
        reasons.append("errors_recorded")
    if state.iteration >= state.max_iterations:
        reasons.append("max_iterations_reached")
    if not state.evaluated_sources:
        reasons.append("no_sources_evaluated")
    elif any(
        source.evaluation_status == "scored" and source.low_confidence
        for source in state.evaluated_sources
    ):
        reasons.append("low_confidence_sources")
    if not any(claim.verdict == "verified" for claim in state.verified_claims):
        reasons.append("no_verified_claims")
    if any(claim.verdict == "contradicted" for claim in state.verified_claims):
        reasons.append("contradicted_claims")
    return reasons


def compose_limitations(
    task: SynthesisTask,
    *,
    provider_failed: bool,
) -> list[str]:
    """The enumerated limitations one composition discloses.

    ``report_generation_failed`` is appended here rather than in state: it
    describes *this* composition, and the composition event must carry the
    same list the artifacts rendered.
    """
    limitations = list(task.limitations)
    if provider_failed:
        limitations.append("report_generation_failed")
    return limitations


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


def claim_label(position: int) -> str:
    """The label a prompt uses to address one checked claim.

    Labels are prompt-local addressing, never identity: ``Claim.claim_id``
    stays the canonical fingerprint. They are zero-padded so a packet reads
    as a stable column and a copy error is visible.
    """
    if position < 1:
        raise ValueError("claim positions start at 1")
    return f"C{position:03d}"


def claim_registry(claims: Sequence[Claim]) -> list[tuple[str, Claim]]:
    """Canonical checked claims, each with the label it is addressed by.

    The label is the claim's position in the canonical registry, which
    ``merge_claim_snapshot`` keeps stable in first-seen order as later passes
    append to it.
    """
    return [
        (claim_label(position), claim)
        for position, claim in enumerate(canonical_claims(claims), start=1)
    ]


def _claim_impact_key(claim: Claim) -> tuple[int, int, int]:
    """Return recorded evidence breadth used as the packet impact key."""
    return (
        len(claim.consumed_finding_fingerprints),
        len(claim.verification_evidence),
        len(claim.source_urls),
    )


def ordered_claims_for_report(claims: Sequence[Claim]) -> list[Claim]:
    """Canonical checked claims, most load-bearing first.

    Coverage first (a claim carrying more planned topics answers more of the
    question), then verdict, then recorded evidence impact, then canonical
    order. Confidence is deliberately not a packet-priority signal: it is a
    model judgement, not a measure of how much of the research question a
    claim carries. Sorting is explicit and total, so no dict or set iteration
    order can reach the prompt.
    """
    ranked = sorted(
        enumerate(canonical_claims(claims)),
        key=lambda item: (
            -len(item[1].consumed_coverage_ids),
            _VERDICT_ORDER.index(item[1].verdict),
            tuple(-part for part in _claim_impact_key(item[1])),
            item[0],
        ),
    )
    return [claim for _, claim in ranked]


def bounded_claim_packet(
    registry: Sequence[tuple[str, Claim]],
    *,
    limit: int,
    budget_chars: int,
) -> tuple[list[tuple[str, Claim]], int]:
    """The claims a prompt can carry, ranked, bounded by count and characters.

    ``limit`` caps the packet's size; ``budget_chars`` caps the rendered
    length. The rank order is applied first, so a truncated packet keeps the
    most load-bearing claims rather than the first ones the state happened to
    record. The second element is how many claims the packet left out, which
    the prompt states rather than hiding.
    """
    if limit < 1:
        raise ValueError("limit must be at least 1")
    if budget_chars < 1:
        raise ValueError("budget_chars must be at least 1")
    order = {
        claim.claim_id: position
        for position, claim in enumerate(ordered_claims_for_report(
            [claim for _, claim in registry]
        ))
    }
    ranked = sorted(
        registry,
        key=lambda item: (order.get(item[1].claim_id, len(order)), item[0]),
    )
    minimum = len(render_report_claim_packet([], omitted=len(ranked)))
    if budget_chars < minimum:
        raise ValueError(
            "budget_chars must allow the empty claim-packet fallback "
            f"({minimum} chars)"
        )
    maximum = min(limit, len(ranked))
    # Select the largest ranked prefix whose *actual prompt representation*
    # fits.  This includes labels, verdict syntax, the claims' own text, URLs,
    # coverage, separators, and the omission notice — so a smaller packet is
    # one that carries fewer claims, never one that carries a claim in part.
    for size in range(maximum, -1, -1):
        packet = ranked[:size]
        omitted = len(ranked) - size
        rendered = render_report_claim_packet(packet, omitted=omitted)
        if len(rendered) <= budget_chars:
            return packet, omitted

    # The minimum fallback check above makes this defensive return unreachable.
    return [], len(ranked)


class PacketEntry(ContractModel):
    """One checked claim in the canonical packet, with everything behind it.

    The packet is what the writer is shown, so it carries the support and the
    counterevidence separately, the source assessment and the dates of each
    citation, the targets and required dimensions the claim owes, the
    obligations still open on those targets, and the failures this pass
    measured. ``evidence_label`` is a qualitative reading of the badge rather
    than a bare confidence, because "0.90" is not a calibrated probability.
    """

    label: str
    claim_id: str
    cluster_id: str | None = None
    text: str
    verdict: str
    confidence: float
    evidence_status: str | None = None
    evidence_label: str = ""
    citation_urls: list[str] = Field(default_factory=list)
    support: list[str] = Field(default_factory=list)
    counter: list[str] = Field(default_factory=list)
    source_assessment: list[str] = Field(default_factory=list)
    provenance: list[str] = Field(default_factory=list)
    """What this claim records about its figure: issuer, edition, release, period,
    scope. One line, or none when the extraction recorded nothing — never a
    date read off the page the claim cites."""
    target_ids: list[str] = Field(default_factory=list)
    required_dimensions: list[str] = Field(default_factory=list)
    answered_dimensions: list[str] = Field(default_factory=list)
    obligations: list[str] = Field(default_factory=list)
    measured_failures: list[str] = Field(default_factory=list)


class CanonicalPacket(ContractModel):
    """The compact packet, its explicit omissions, and its continuation plan.

    Selection is balanced across the critical targets before it is ranked by
    recorded impact, so a target with one narrow claim is represented rather
    than crowded out by a target with many. What does not fit is named:
    ``omitted_ids`` lists every claim the packet left out, and
    ``continuation_batches`` groups them into the batches a next pass would
    take. A continuation is not a deletion.
    """

    entries: list[PacketEntry] = Field(default_factory=list)
    omitted_ids: list[str] = Field(default_factory=list)
    continuation_batches: list[list[str]] = Field(default_factory=list)


def answer_form_instruction(contract: AnswerContract | None) -> str:
    """The structure the frozen answer form requires of this report.

    Section 2.3 freezes the answer form before any evidence is gathered, and a
    report that answers a comparison question with a deployment ranking has
    answered a question nobody asked. The form decides which second section
    the reader meets and which rows the writer returns; the renderer is what
    enforces it, so this text and ``report.reader_sections`` state the same
    thing.
    """
    if contract is None:
        return (
            "No answer form was frozen for this pass: return the ranked "
            "constraint list, and return an empty answer_rows list."
        )
    kind = contract.answer_kind
    common = (
        f"This is a {kind} question. Frozen scope: "
        f"{contract.scope_statement} Geographic scope: "
        f"{contract.geographic_scope}. The question is about "
        f"{contract.evidence_period_requirement} as of {contract.as_of_date}."
    )
    if kind == "constraints":
        return (
            f"{common} Return the ranked constraint list, most consequential "
            "first, and return an empty answer_rows list. Rank only on the "
            "comparison basis the evidence states; when no such basis exists, "
            "group the constraints by type or region and say so in the text "
            "instead of inventing an order."
        )
    shape = {
        "comparison": (
            "Return one answer_rows entry per compared option, with "
            "dimension naming the basis of comparison the evidence supports."
        ),
        "factual": (
            "Return one answer_rows entry per established fact, with "
            "dimension naming what the fact is about."
        ),
        "historical": (
            "Return one answer_rows entry per dated development, with "
            "dimension naming the period the entry's data cover — never the "
            "publication date of the source that reported it."
        ),
        "explanation": (
            "Return one answer_rows entry per mechanism, with subject naming "
            "what is explained and dimension naming the driver."
        ),
    }.get(kind, "Return an empty answer_rows list.")
    return (
        f"{common} {shape} Every entry carries subject, dimension, finding, "
        "claim_ids and source_urls; the finding is a settlement statement "
        "like any other, so it needs a checked claim and its urls. Return an "
        "empty ranked_constraints list: this question is not a deployment "
        "ranking."
    )


def _note_corpus(task: SynthesisTask, corpus: str) -> str:
    """The recorded facts an uncertainty note may state about the question.

    The frozen contract's scope and period, the plan's sub-topic titles, and
    each source's four recorded dates. All of them are records this run made
    before the note was written, so a note that names the question's horizon
    or a source's data period is reporting this pass, not asserting the world.
    """
    parts = [corpus]
    contract = task.answer_contract
    if contract is not None:
        parts.extend(
            (
                contract.scope_statement,
                contract.evidence_period_requirement,
                contract.geographic_scope,
                contract.as_of_date,
                contract.question,
            )
        )
    parts.extend(topic.title for topic in task.sub_topics)
    for source in task.sources:
        temporal = source.temporal
        parts.extend(
            value
            for value in (
                temporal.publication_date,
                temporal.data_period,
                temporal.forecast_horizon,
                temporal.effective_date,
            )
            if value
        )
    return " ".join(parts).casefold()


def measured_failures(task: SynthesisTask) -> list[str]:
    """The evidence failures this pass actually recorded, as enumerated tokens.

    A limitation note may describe a truncated read, a denied retrieval, or an
    evidence gap only when one of these exists. The tokens are project
    vocabulary — error types, disposition reasons, and refused URLs — never
    model prose.
    """
    tokens: list[str] = []
    for error in task.errors:
        token = error.error_type.strip()
        if token and token not in tokens:
            tokens.append(token)
    for disposition in task.evidence_dispositions:
        for raw in (disposition.reason, disposition.stage):
            token = raw.strip()
            if token and token not in tokens:
                tokens.append(token)
    for state in task.acquisition_state_by_target.values():
        for url in state.denied_urls:
            token = f"denied:{url}"
            if token not in tokens:
                tokens.append(token)
    return tokens


def _targets_by_id(task: SynthesisTask) -> dict[str, EvidenceTarget]:
    return {target.target_id: target for target in task.targets}


def _packet_rank(
    claims: Sequence[Claim],
    *,
    targets: Mapping[str, EvidenceTarget],
) -> list[Claim]:
    """Claims in packet order: critical targets first, then recorded impact.

    This is the balance the packet exists for. A highest-confidence prefix
    answers the question the model found easiest; one claim per critical
    target answers the question that was asked.
    """
    claimed_by_target: dict[str, list[Claim]] = {}
    for claim in claims:
        for target_id in claim.target_ids:
            claimed_by_target.setdefault(target_id, []).append(claim)
    ordered: list[Claim] = []
    seen: set[str] = set()

    def take(source: Sequence[Claim]) -> None:
        for claim in source:
            if claim.claim_id not in seen:
                seen.add(claim.claim_id)
                ordered.append(claim)

    for critical in (True, False):
        for target in targets.values():
            if target.critical is not critical:
                continue
            take(claimed_by_target.get(target.target_id, ()))
    take(ordered_claims_for_report(claims))
    return ordered


def _evidence_lines(
    evidence_ids: Sequence[str],
    evidence: Mapping[str, EvidenceUnit],
) -> list[str]:
    """``id locator "excerpt"`` for each selected passage, in selected order.

    The excerpt is published whole. It is the passage a claim rests on, and
    the writer may only restate what it can see: clamping it to the ledger's
    200-character display bound hid figures sitting below a page's navigation
    — the same shape that made the audited run's fact checker miss nine of its
    fourteen verdicts. What bounds this packet is how many claims it carries,
    and what it left out is stated rather than hidden.
    """
    lines: list[str] = []
    for evidence_id in evidence_ids:
        unit = evidence.get(evidence_id)
        if unit is None:
            continue
        lines.append(f'{evidence_id} {unit.locator} "{unit.excerpt}"')
    return lines


def _selected_ids(claim: Claim, cluster: ClaimCluster | None) -> list[str]:
    if cluster is not None and cluster.evidence_ids:
        return list(cluster.evidence_ids)
    return list(claim.evidence_selection)


def _packet_provenance(claim: Claim) -> list[str]:
    """What one checked claim records about the figure it states.

    Read from the claim's own provenance, never from the page it cites: a
    source's publication date is not the edition its data rest on, and a page
    can carry several figures — EIA's March 2025 release states both the 2024
    additions and the 2025 forecast. The writer can only state what it is
    shown, so the issuer, the edition, the release date, the period and the
    scope travel together.

    One rendered line per claim, or none when nothing was recorded: an empty
    line would invite the writer to fill it in.
    """
    provenance = claim.provenance
    if not provenance.recorded:
        return []
    line = provenance.as_text()
    details = [
        f"{label} {value}"
        for label, value in (
            ("period", provenance.data_period),
            ("scope", provenance.measure_scope),
        )
        if value
    ]
    if details:
        line = f"{line} ({'; '.join(details)})" if line else "; ".join(details)
    return [line] if line else []


def _packet_source_assessment(
    urls: Sequence[str],
    sources: Mapping[str, ScoredSource],
) -> list[str]:
    lines: list[str] = []
    for url in urls:
        source = sources.get(url)
        if source is None:
            lines.append(f"{url}: not assessed")
            continue
        if source.evaluation_status == "scored":
            flag = " low_confidence=true" if source.low_confidence else ""
            lines.append(
                f"{url}: scored overall={source.overall_score:.2f}{flag}"
                if source.overall_score is not None
                else f"{url}: scored"
            )
        else:
            lines.append(f"{url}: {source.evaluation_status}")
    return lines


def build_canonical_packet(
    *,
    claims: Sequence[Claim],
    clusters: Mapping[str, ClaimCluster],
    evidence: Mapping[str, EvidenceUnit],
    targets: Sequence[EvidenceTarget],
    sources: Sequence[ScoredSource],
    limit: int,
    batch_size: int | None = None,
    failures: Sequence[str] = (),
    support_budget: int = PACKET_SUPPORT_CHARS,
    labels: Mapping[str, str] | None = None,
) -> CanonicalPacket:
    """Assemble the compact packet the writer receives.

    ``limit`` bounds how many claims the writer may cite. Everything past it
    is listed in ``omitted_ids`` and grouped into continuation batches, so the
    prompt states what it is missing instead of presenting a truncated packet
    as the whole record. ``support_budget`` bounds the selected evidence the
    whole packet carries, across every claim: each claim with a selected
    passage reserves a first-passage share, the depth pass spends the rest,
    and a passage over what is left is cut between sentences with its
    withheld count stated — or named, when nothing is shown for that claim.

    ``labels`` is the checked-claim registry's own ``claim_id -> label`` map.
    A claim's label here always comes from it, never from this function's own
    ``_packet_rank`` position: the registry is the one label space a drafted
    point's ``claim_ids`` resolve against (``DraftContext.approved``), and a
    packet that relabelled by rank showed a claim under a label that
    resolved to a *different* claim there — one canonical label per checked
    claim, no matter which order this packet ranks it into. Omitted only by
    a caller with no registry of its own to give, in which case a claim's
    label falls back to its rank position here.
    """
    if limit < 1:
        raise ValueError("limit must be at least 1")
    target_index = {target.target_id: target for target in targets}
    source_index = {normalize_source_url(source.url): source for source in sources}
    canonical = canonical_claims(claims)
    ranked = _packet_rank(canonical, targets=target_index)
    label_map = dict(labels) if labels is not None else {}

    def label_for(position: int, claim: Claim) -> str:
        return label_map.get(claim.claim_id) or claim_label(position)

    entries: list[PacketEntry] = []
    remaining = support_budget
    # Reserve a first-passage share for every claim the packet will carry
    # before any claim takes a second passage. Spending the budget in claim
    # order let one oversized first passage starve every later claim of its
    # only support, which the omission notice named without saying whose.
    carried = ranked[: max(1, min(limit, len(ranked)))]
    selected_by_claim = {
        claim.claim_id: _selected_ids(
            claim, clusters.get(claim.cluster_id or "")
        )
        for claim in carried
    }
    # Only the claims that actually have a selected passage hold a share: a
    # claim with nothing to show reserved one anyway, and the budget it never
    # spent was unavailable to the claims that needed it — 10,603 characters
    # of a 16,000-character budget left unused while a 7,610-character
    # passage was cut. The depth pass then spends whatever is left, so a
    # passage that fits the free budget is carried whole.
    showing = [claim for claim in carried if selected_by_claim[claim.claim_id]]
    share = max(1, support_budget // max(1, len(showing)))
    support_by_claim: dict[str, list[str]] = {}
    for claim in carried:
        if not selected_by_claim[claim.claim_id]:
            continue
        lines = _evidence_lines(
            selected_by_claim[claim.claim_id][:1],
            evidence,
            budget=min(share, remaining),
        )
        remaining -= _support_cost(lines)
        support_by_claim[claim.claim_id] = lines
    for claim in carried:
        rest = selected_by_claim[claim.claim_id][1:]
        if not rest or remaining <= 0:
            continue
        lines = _evidence_lines(rest, evidence, budget=remaining)
        remaining -= _support_cost(lines)
        support_by_claim[claim.claim_id] = [
            *support_by_claim[claim.claim_id],
            *lines,
        ]
    for position, claim in enumerate(ranked, start=1):
        if len(entries) >= limit:
            break
        cluster = clusters.get(claim.cluster_id or "")
        selected = _selected_ids(claim, cluster)
        # What the cluster resolved, not its verified slice: a contradicted
        # member of a cluster that also holds a verified one was labelled
        # "verified pair" and given the verified member's citations.
        _, resolved_status = (
            resolved_verdict_and_status(cluster)
            if cluster is not None
            else (claim.verdict, claim.evidence_status)
        )
        # ``cites:`` is the validator's own allow-list — ``_build_point``
        # accepts only a url on the named claims' ``source_urls`` — so this
        # is the whole list: never a cluster's wider verdict-evidence urls,
        # never a selected passage's own url. Either would advertise a url
        # that copying the label exactly as shown would still get refused
        # for; those urls are not lost, they are on the passage lines below,
        # beside the excerpt they were found on.
        urls: list[str] = [
            url
            for url in dict.fromkeys(
                normalize_source_url(raw) for raw in claim.source_urls
            )
            if url
        ]
        badge = (resolved_status or "") or (claim.evidence_status or "")
        target_ids = list(
            dict.fromkeys(
                [
                    *claim.target_ids,
                    *(cluster.target_ids if cluster is not None else []),
                ]
            )
        )
        required: list[str] = []
        obligations: list[str] = []
        for target_id in target_ids:
            target = target_index.get(target_id)
            if target is None:
                continue
            for dimension in target.required_dimensions:
                if dimension not in required:
                    required.append(dimension)
            missing = [
                dimension
                for dimension in target.required_dimensions
                if dimension not in claim.consumed_coverage_ids
            ]
            if not selected:
                obligations.append(
                    f"{target_id} has no selected evidence yet"
                )
            elif missing:
                obligations.append(
                    f"{target_id} still owes: {', '.join(missing)}"
                )
        support = support_by_claim.get(claim.claim_id, [])
        counter = _evidence_lines(
            [
                evidence_id
                for evidence_id in selected
                if evidence.get(evidence_id) is not None
                and evidence[evidence_id].origin == "fact_checker"
                and claim.contradictions
            ],
            evidence,
            budget=max(0, remaining),
        )
        remaining -= _support_cost(counter)
        entries.append(
            PacketEntry(
                label=label_for(position, claim),
                claim_id=claim.claim_id,
                cluster_id=claim.cluster_id,
                # The claim as it was checked. A bound for a rendered table
                # cell is not a bound for a model's input: a 289-character
                # claim reached the writer as a cut sentence, and the report
                # then published "the claim is recorded only in part" — an
                # uncertainty invented by a display constant. The claim
                # block's own character budget decides how many claims fit.
                text=claim.text,
                verdict=claim.verdict,
                confidence=claim.confidence,
                evidence_status=claim.evidence_status,
                evidence_label=EVIDENCE_BADGE_LABELS.get(badge, badge),
                citation_urls=urls,
                support=support,
                counter=counter,
                source_assessment=_packet_source_assessment(urls, source_index),
                provenance=_packet_provenance(claim),
                target_ids=target_ids,
                required_dimensions=required,
                answered_dimensions=list(claim.consumed_coverage_ids),
                obligations=obligations,
                measured_failures=list(failures),
            )
        )
    omitted = [
        label_for(position, claim)
        for position, claim in enumerate(ranked[len(entries) :], start=len(entries) + 1)
    ]
    size = batch_size if batch_size is not None else max(1, limit)
    batches = [
        omitted[start : start + size] for start in range(0, len(omitted), size)
    ]
    return CanonicalPacket(
        entries=entries, omitted_ids=omitted, continuation_batches=batches
    )


def render_canonical_packet(packet: CanonicalPacket) -> str:
    """Render the packet as the addressable, self-describing block a model reads."""
    lines: list[str] = []
    for entry in packet.entries:
        lines.append(
            f"{entry.label} [{entry.verdict} {entry.confidence:.2f} | "
            f"{entry.evidence_label}] {entry.text}"
        )
        if entry.citation_urls:
            lines.append(f"  cites: {', '.join(entry.citation_urls)}")
        lines.append(
            "  supports: " + ("; ".join(entry.support) or "none recorded")
        )
        lines.append(
            "  contradicts: " + ("; ".join(entry.counter) or "none recorded")
        )
        for assessment in entry.source_assessment:
            lines.append(f"  source: {assessment}")
        if entry.provenance:
            lines.append(f"  provenance: {', '.join(entry.provenance)}")
        if entry.target_ids:
            lines.append(
                f"  targets: {', '.join(entry.target_ids)}; "
                f"dimensions: {', '.join(entry.required_dimensions) or 'none'}"
            )
        if entry.answered_dimensions:
            lines.append(
                f"  coverage: {', '.join(entry.answered_dimensions)}"
            )
        for obligation in entry.obligations:
            lines.append(f"  obligation: {obligation}")
        for failure in entry.measured_failures:
            lines.append(f"  failure: {failure}")
    if not packet.entries:
        lines.append("(no checked claim was available for this packet)")
    if packet.omitted_ids:
        lines.append(
            f"({len(packet.omitted_ids)} further checked claim(s) were omitted "
            "for length; they cannot be cited by this draft.)"
        )
        lines.append(f"omitted: {', '.join(packet.omitted_ids)}")
        for position, batch in enumerate(packet.continuation_batches, start=1):
            lines.append(
                f"continuation batch {position}: {', '.join(batch)}"
            )
    return "\n".join(lines)


def bounded_finding_digest(
    findings: Sequence[Finding],
    *,
    limit: int,
    budget_chars: int,
) -> str:
    """Render the longest leading open-question digest within its ceiling."""
    if limit < 1:
        raise ValueError("limit must be at least 1")
    if budget_chars < 1:
        raise ValueError("budget_chars must be at least 1")
    minimum = len(render_finding_digest([]))
    if budget_chars < minimum:
        raise ValueError(
            "budget_chars must allow the empty finding-digest fallback "
            f"({minimum} chars)"
        )
    candidates = list(findings)[:limit]
    for size in range(len(candidates), -1, -1):
        rendered = render_finding_digest(candidates[:size])
        if len(rendered) <= budget_chars:
            return rendered
    return render_finding_digest([])


def high_confidence_claims(
    claims: Sequence[Claim],
    *,
    threshold: float = DEFAULT_MEMORY_CONFIDENCE,
    limit: int = DEFAULT_MAX_MEMORY_FINDINGS,
) -> list[Claim]:
    """Verified claims confident enough to keep for future sessions, capped.

    Pure, and unused by synthesis: the terminal finalizer decides when a run's
    claims are worth keeping, and only after the terminal quality gates.
    """
    kept = [
        claim
        for claim in claims
        if claim.verdict == "verified" and claim.confidence >= threshold
    ]
    return kept[:limit]


def memory_payload(
    claim: Claim,
    *,
    session_id: str,
) -> tuple[str, dict[str, JsonValue]]:
    """Render one verified claim as a ``save_to_memory`` call's arguments.

    Metadata keys mirror ``memory.entries.MemoryEntry`` so a stored claim
    reads back the same way a stored finding does.
    """
    metadata: dict[str, JsonValue] = {
        "entry_type": "finding",
        "session_id": session_id,
        "agent_id": SYNTHESIZER_NAME,
        "confidence": round(claim.confidence, 4),
        "source_url": claim.source_urls[0],
        "verdict": claim.verdict,
    }
    return claim.text, metadata


def render_revision_guidance(state: ResearchState) -> str:
    """Render the critic's last feedback for a rewrite, or an empty string.

    Recommended queries are deliberately absent: they tell the *Researcher*
    what to retrieve next and would only invite this agent to write about
    evidence it does not have.
    """
    critique = state.critique
    if critique is None:
        return ""
    lines = ["A previous pass of this report was reviewed and sent back."]
    if critique.gaps:
        lines.append("Gaps the reviewer named:")
        lines.extend(
            f"- {summarize_text(gap.problem, limit=_GUIDANCE_CHARS)}"
            for gap in critique.gaps
        )
    if critique.unsupported_claims:
        lines.append("Statements the reviewer found unsupported:")
        lines.extend(
            f"- {summarize_text(claim, limit=_GUIDANCE_CHARS)}"
            for claim in critique.unsupported_claims
        )
    return "\n".join(lines)


def _resolve_claims(
    labels: Sequence[str],
    *,
    approved: Mapping[str, Claim],
) -> tuple[list[Claim], int]:
    """Resolve prompt labels to canonical claims, counting unknown ones."""
    resolved: list[Claim] = []
    seen: set[str] = set()
    unknown = 0
    for raw in labels:
        label = " ".join(raw.split()).upper()
        claim = approved.get(label)
        if claim is None:
            unknown += 1
            continue
        if claim.claim_id not in seen:
            seen.add(claim.claim_id)
            resolved.append(claim)
    return resolved, unknown


class DraftContext(ContractModel):
    """Everything one validation pass needs to judge a drafted statement.

    Carried as one object rather than as six parameters: the checks below all
    need the same evidence, targets, declared corpus and record lists, and a
    signature that grows a parameter per check is how a later check gets
    forgotten at one call site and not another.
    """

    approved: dict[str, Claim] = Field(default_factory=dict)
    clusters: dict[str, ClaimCluster] = Field(default_factory=dict)
    evidence: dict[str, EvidenceUnit] = Field(default_factory=dict)
    targets: dict[str, EvidenceTarget] = Field(default_factory=dict)
    dimensions_by_target: dict[str, list[str]] = Field(default_factory=dict)
    """Each target's required dimensions, as the shared derivation reads them."""
    corpus: str = ""
    raw_corpus: str = ""
    """``corpus``'s case-preserved counterpart, for the acronym check alone.

    A statement's own claims usually carry no selected evidence of their own
    (an unselected or uncited claim), and the per-statement corpus then falls
    back to this whole-pass one; without a case-preserved fallback here too,
    that fallback loses the capitalisation the acronym check needs and an
    attested "EIA's" was refused for having no evidence of its own to read
    case from.
    """
    note_corpus: str = ""
    """The figures a *question-shaped* note may name.

    Wider than the evidence corpus on purpose: a note about the question's own
    horizon, period, or scope is stating a fact this run recorded — in the
    frozen contract, the plan's sub-topics, or a source's recorded dates — and
    removing those figures would make the note say something else.
    """
    failures: list[str] = Field(default_factory=list)
    rejected: list[str] = Field(default_factory=list)
    rejected_points: list[RejectedDraftPoint] = Field(default_factory=list)
    dispositions: list[str] = Field(default_factory=list)
    returned: list[str] = Field(default_factory=list)
    counter: int = 0

    def next_id(self, prefix: str) -> str:
        self.counter += 1
        return f"{prefix}{self.counter:03d}"

    def note(self, disposition: str, where: str, reason: str) -> None:
        """Record one disposition and the project-generated reason for it."""
        if disposition not in self.dispositions:
            self.dispositions.append(disposition)
        self.rejected.append(f"{where}: {reason}")

    def reject_point(
        self,
        disposition: str,
        where: str,
        reason: str,
        *,
        text: str,
        claim_ids: Sequence[str],
        urls: Sequence[str],
    ) -> None:
        """Record one refused drafted point in full.

        The un-truncated companion to ``note``'s terse ``rejected`` reason:
        the exact drafted text, claim labels and source urls, so the
        evidence ledger's 'Rejected draft content' section and the quality
        record can show which drafted point tripped which reason without
        replaying the provider call that wrote it.
        """
        self.note(disposition, where, reason)
        self.rejected_points.append(
            RejectedDraftPoint(
                where=where,
                text=text,
                claim_ids=list(claim_ids),
                source_urls=list(urls),
                reason=reason,
            )
        )


def _attestation_corpus(
    approved: Mapping[str, Claim],
    evidence: Mapping[str, EvidenceUnit],
    *,
    casefold: bool = True,
) -> str:
    """The text a drafted statement's specific atoms must appear in.

    The checked claims themselves are included: a statement is allowed to
    restate its claim's wording, and a figure that is in the claim is in the
    evidence the claim was checked against. Nothing else is added — a corpus
    padded with a model's own prose would attest itself.
    """
    parts = [claim.text for claim in approved.values()]
    parts.extend(unit.excerpt for unit in evidence.values())
    text = " ".join(parts)
    return text.casefold() if casefold else text


def _content_tokens(text: str) -> list[str]:
    """The words a claim to be *about something* is carried by."""
    return [
        token
        for token in re.findall(r"[a-z][a-z0-9'-]{2,}", text.casefold())
        if token not in _ATTESTATION_STOPWORDS
    ]


def _corpus_tokens(corpus: str) -> set[str]:
    """The words and figures the corpus carries, as whole tokens.

    Whole tokens, not substrings: "1200 hectares" does not attest "12", and a
    substring test would let one measured figure vouch for a different one
    that happens to share its digits. The trailing separators are stripped so
    the corpus side and ``_figure_number`` agree: an excerpt is an exact
    sentence of a source document, so its figures very often end one. Only the
    ends are stripped, so "1,200" keeps its internal separator and stays
    distinct from "1200".
    """
    tokens = {
        token.strip(".,'-\u2019")
        for token in re.findall(r"[a-z0-9][a-z0-9'.,\u2019-]*", corpus.casefold())
    }
    tokens.discard("")
    return tokens


def unattested_atoms(text: str, corpus: str, raw_corpus: str = "") -> list[str]:
    """Specific factual atoms the corpus does not carry.

    Figures and names are what a paraphrase does not invent and a fabrication
    does. An unattested ordinary word is prose; an unattested figure or proper
    noun is a new fact, and this is the deterministic half of the support
    review — the half that cannot be argued with.

    ``raw_corpus``, when given, is the *case-preserved* text the acronym
    check reads: ``corpus`` itself is usually casefolded already by its
    caller, and a folded corpus can never carry a capitalised name run.
    Omitted, the acronym check falls back to ``corpus`` as given, which is
    strict rather than permissive when that text has already lost its case.
    """
    tokens = _corpus_tokens(corpus)
    found: list[str] = []
    for token in _significant_figures(text):
        number = _figure_number(token)
        if number and number not in tokens and token not in found:
            found.append(token)
    for match in _PROPER_NOUN_PATTERN.finditer(text):
        token = match.group(0)
        # A sentence-opening capitalised word is capitalised by position: this
        # checker cannot tell "Charge" from "California" without a lexicon, so
        # the position exemption stays. An acronym is the exception, because
        # no ordinary sentence opener is all-caps. The acronym is read without
        # its possessive ("IEA's", "SEIA's"), and a word with an internal
        # capital ("BloombergNEF") is a name wherever it stands.
        base = re.sub(r"['\u2019]s$", "", token)
        if (
            _SENTENCE_INITIAL.search(text[: match.start()])
            and not _ACRONYM_PATTERN.fullmatch(base)
            and not re.search(r"[a-z][A-Z]", base)
        ):
            continue
        folded = token.casefold()
        # A unit is not a name: "GW" in a statement whose evidence spells out
        # "gigawatts" is an abbreviation of the same measured quantity, and the
        # figure it belongs to has already been checked against that evidence.
        # A common abbreviation is not a name for the same reason: the domain
        # writes "EVs" where its evidence writes "electric vehicles".
        if folded in _UNIT_WORDS or folded in _COMMON_ABBREVIATIONS:
            continue
        if (
            not _name_attested(token, tokens, raw_corpus or corpus)
            and token not in found
        ):
            found.append(token)
    return found


# Words an organisation's or a place's initials skip: "Energy Information
# Administration" is EIA, and "United States of America" is USA.
_INITIAL_SKIP = frozenset({"of", "and", "the", "for", "on", "in", "&"})


# A leading word a name run may drop without adding to its own initials:
# "U.S. Energy Information Administration" spells EIA, not UEIA, because the
# leading "U.S." names the country the agency belongs to, not a word of the
# agency's own name. Kept narrow, on the review's own example, rather than
# generalised to "any leading word may be dropped" — that would let a run
# spell an acronym it never wrote by discarding whichever word makes it fit.
_COUNTRY_PREFIXES = frozenset({"us", "u.s", "uk", "u.k"})
# Punctuation stripped from a run word's ends before it is compared as a
# whole word: a token carries the sentence's own comma or closing period, and
# neither is part of the word.
_NAME_PUNCT = ".,;:!?()[]{}\u2019'\""


def _is_title_word(word: str) -> bool:
    """True when ``word``'s first letter is capitalised."""
    match = re.search(r"[A-Za-z]", word)
    return bool(match) and match.group(0).isupper()


def _run_initial(word: str) -> str:
    """The initial letter of one name-run word ("U.S." spells "U")."""
    match = re.search(r"[A-Za-z]", word)
    return match.group(0).upper() if match else ""


def _name_runs(corpus: str) -> list[list[str]]:
    """Maximal runs of capitalised words in case-preserved ``corpus`` text.

    Tokenised on whitespace, so "U.S." stays one token and contributes one
    initial rather than splitting into the two letters "u" and "s" — the
    split that let a claim spelling "U.S. Energy Information Administration"
    also spell the invented "SEIA". A connector from ``_INITIAL_SKIP``
    bridges two capitalised words without ending the run ("United States of
    America"); any other lowercase word ends it, and so does a sentence
    carrying no capitalisation at all — a lowercase phrase such as "installed
    energy additions" is never a name run, however its initials happen to
    fall.
    """
    runs: list[list[str]] = []
    current: list[str] = []
    pending: list[str] = []
    for token in corpus.split():
        bare = token.strip(_NAME_PUNCT).casefold()
        if _is_title_word(token):
            current.extend(pending)
            current.append(token)
            pending = []
        elif bare in _INITIAL_SKIP and current:
            pending.append(token)
        else:
            if current:
                runs.append(current)
            current, pending = [], []
    if current:
        runs.append(current)
    return runs


def _run_words(run: Sequence[str]) -> list[str]:
    """A run's words, connectors dropped: they spell no initial of their own."""
    return [
        word for word in run if word.strip(_NAME_PUNCT).casefold() not in _INITIAL_SKIP
    ]


def _spelled_out(acronym: str, corpus: str) -> bool:
    """True when case-preserved ``corpus`` writes the name ``acronym`` spells.

    Matched only against a contiguous span inside one maximal run of
    capitalised words — never a whole sentence — so a lowercase phrase such
    as "installed energy additions" carries no name at all, however its
    initials happen to fall. "U.S. Energy Information Administration" spells
    EIA, and only EIA once a leading country token ("U.S.") is dropped from
    the search, never the "SEIA" the old letter-only scan read out of a split
    "u"/"s"; the span search (rather than requiring the whole run) is what
    still finds EIA when the run runs on into an adjacent capitalised word —
    a claim's own "... Administration's March 12, 2025 analysis" sweeps the
    month into the run, and the name it spells does not move for that.
    """
    wanted = acronym.upper()
    size = len(wanted)
    if size == 0:
        return False
    for run in _name_runs(corpus):
        words = _run_words(run)
        searched = [words]
        if words and words[0].strip(_NAME_PUNCT).casefold() in _COUNTRY_PREFIXES:
            searched.append(words[1:])
        for candidate in searched:
            for start in range(len(candidate) - size + 1):
                span = candidate[start : start + size]
                if "".join(_run_initial(word) for word in span) == wanted:
                    return True
    return False


def _name_attested(token: str, tokens: set[str], corpus: str) -> bool:
    """Whether the corpus states this name, in any form it writes names in.

    The audited pass refused "EIA's", "Monitor's" and "EIA-based" although
    the claims said "U.S. Energy Information Administration", "Energy Storage
    Monitor" and "citing EIA": a possessive and a "-based" compound name the
    same body, and an acronym is the body its evidence spells out. Every
    capitalised part of a compound still has to be attested, so an invented
    "IEA-based" or "Wood Mackenzie's" is refused as before.
    """
    # The evidence writing the name whole ("short-term", "year-in-review",
    # "eia's") attests it before any part of it is looked at.
    if token.casefold() in tokens:
        return True
    base = re.sub(r"['\u2019]s$", "", token)
    if base.casefold() in tokens:
        return True
    parts = [part for part in base.split("-") if part[:1].isupper()] or [base]
    for part in parts:
        folded = part.casefold()
        if folded in tokens:
            continue
        if _ACRONYM_PATTERN.fullmatch(part) and _spelled_out(part, corpus):
            continue
        return False
    return True


def unattested_words(text: str, corpus: str) -> list[str]:
    """Every content word of a short cell the corpus does not carry.

    Whole tokens, for the same reason the figures use them: a substring test
    lets "generation" vouch for "gen", and a cell is meant to be lifted from
    the evidence rather than composed. A mechanism or a geography nobody's
    evidence states is the uncited cell this check exists to repair.
    """
    tokens = _corpus_tokens(corpus)
    return [token for token in _content_tokens(text) if token not in tokens]


def hedge_marker(text: str) -> str:
    """The first modality marker a text carries, or ``""`` for none."""
    match = (
        _HEDGE_PATTERN.search(text)
        or _HEDGE_REPORTING_PATTERN.search(text)
        or _HEDGE_MAY_PATTERN.search(text)
    )
    return match.group(0).casefold() if match else ""


def _figure_numbers(text: str) -> list[str]:
    """Every numeric token a text carries, years included."""
    return [
        number
        for number in (
            _figure_number(match.group(0))
            for match in _FIGURE_PATTERN.finditer(text)
        )
        if number
    ]


def dropped_modality(text: str, claims: Sequence[Claim]) -> str:
    """The modality a statement dropped, or ``""`` when it dropped none.

    Read from the claims the statement rests on, and only for the claim the
    statement is *about*: a point cites every claim it rests on, so a 2024
    addition statement also cites the 2025 forecast claim whose wording it
    never restates. A claim's hedge binds a statement when the two share a
    figure — the statement restates that claim's quantity — or when neither
    the claim nor any claim beside it carries a figure, which is a statement
    of one claim's own proposition.

    A claim that states no modality is a claim a statement may state plainly,
    which is why this returns empty rather than requiring a hedge of every
    statement.
    """
    if hedge_marker(text):
        return ""
    figures = set(_figure_numbers(text))
    quantified = any(_figure_numbers(claim.text) for claim in claims)
    for claim in claims:
        marker = hedge_marker(claim.text)
        if not marker:
            continue
        claim_figures = set(_figure_numbers(claim.text))
        if claim_figures and not claim_figures.intersection(figures):
            continue
        if not claim_figures and quantified:
            continue
        return marker
    return ""


def hardened_modality(text: str, corpus: str) -> str:
    """The uncertainty the cited evidence states and a statement hardens away.

    Read from the *evidence*, not from the claim: a claim can have hardened
    its source already — the audited claim said "would set a record" where the
    page said "could set a record" — so the claim's own wording cannot witness
    what was lost. The statement is refused only when it asserts with a strong
    modal what the evidence hedged; a statement that carries the evidence's own
    uncertainty, or hedges in any other way, states no more than it was shown.
    """
    if not hedge_marker(corpus):
        return ""
    for modal in _STRONG_MODALS:
        for match in re.finditer(rf"\b{modal}\b", text, re.IGNORECASE):
            # The exemption is the modal's own clause. A statement that
            # reports a figure ("EIA forecast 18.2 GW …") and then asserts an
            # outcome in the next clause ("which would set a record") hedged
            # nothing about that outcome, and exempting the whole statement on
            # the reporting verb published the audited hardening.
            if hedge_marker(_clause_around(text, match.start())):
                continue
            return modal
    return ""


# The third-party nouns a note can hand a total to without naming anyone.
_THIRD_PARTY_NOUNS = (
    "the agency",
    "the authority",
    "the issuer",
    "the publisher",
    "the source",
)


def attributes_a_source(text: str) -> bool:
    """True when a note hands a total to someone other than this pass.

    Conservative on purpose: the question is not *which* words appear but
    whether the note can be read as this pass describing itself, and a single
    name anywhere in it says it cannot. A possessive total, a "totals of"
    phrase, a third-party noun, an acronym, or any capitalised word that is
    not a sentence opener is attribution — so "EIA totals exclude …",
    "EIA's totals" and "the totals of the agency" are all sources' boundaries
    whatever clause they sit in, while "the totals this report considered" is
    this pass's own coverage and stays self-description.
    """
    folded = text.casefold()
    if any(noun in folded for noun in _THIRD_PARTY_NOUNS):
        return True
    if re.search(r"(?:'s|\u2019s)\s+totals?\b|\btotals?\s+of\b", folded):
        return True
    if _ACRONYM_PATTERN.search(text):
        return True
    return any(
        not _SENTENCE_INITIAL.search(text[: match.start()])
        for match in _PROPER_NOUN_PATTERN.finditer(text)
    )


def scope_fact(text: str) -> str:
    """The scope convention a text asserts, or ``""`` for none.

    A scope fact — which plants a source's total includes, which it leaves out,
    which issuer's boundary it follows — is a finding, and a finding needs
    checked evidence behind it. What this refuses is narrow on purpose: the
    note must name one of the totals' own subjects *and* an inclusion or
    exclusion verb. A note that describes this pass ("the checked evidence does
    not include a full-year outturn", "not included in this report") is the
    framing a source-free note exists to carry, and it is not a claim about
    what a source counted.
    """
    lowered = " ".join(text.casefold().split())
    clauses = _CLAUSE_SPLIT.split(lowered)
    for index, clause in enumerate(clauses):
        subject = next(
            (name for name in _SCOPE_SUBJECTS if name in clause), ""
        )
        if not subject:
            continue
        if not any(verb in clause for verb in _SCOPE_VERBS):
            continue
        # A clause that only describes this pass introduces the one that
        # follows: "In this pass, behind-the-meter storage is excluded from
        # the totals" is the pass describing itself. Only the *preceding*
        # clause exempts — exempting on a following one would let the audited
        # note through by appending ", so this report cannot convert …".
        # "Reported totals" is never this pass's own coverage: a note naming
        # them is a claim about what a source counted, so it is never exempt.
        if (
            index
            and subject != _REPORTED_TOTALS
            and any(
                phrase in clauses[index - 1]
                for phrase in _SCOPE_SELF_PHRASES
            )
            and not any(
                name in clauses[index - 1] for name in _SCOPE_SUBJECTS
            )
            # A pass phrase licenses self-description, never a claim about a
            # source: "In this pass, behind-the-meter storage is excluded from
            # EIA's totals" is a boundary however it opens, while "…from the
            # totals this report considered" is this pass's own coverage.
            and not attributes_a_source(text)
        ):
            continue
        # The exemption is per clause *and* positional: a note that opens by
        # describing this pass ("in this pass, behind-the-meter storage …") is
        # the pass describing itself, while a note that asserts the boundary
        # first and mentions this report afterwards is the assertion. A
        # substring test anywhere in the note published the audited sentence
        # with six words appended.
        if any(
            phrase in clause[: clause.index(subject)]
            for phrase in _PASS_PHRASES
        ):
            continue
        return subject
    return ""


def _capacity_figures(sentence: str) -> list[tuple[int, str]]:
    """(position, number) for every figure in a sentence that carries a unit.

    A bare year is not a capacity figure. "In 2024, developers installed
    10.4 GW" and "developers installed 10.4 GW … in 2024" state one fact, and
    a rule that could attach a qualifier to the year refused the second.
    """
    return [
        (match.start(), _figure_number(match.group(1)))
        for match in _CAPACITY_FIGURE.finditer(sentence)
    ]


def _qualifier_attachments(text: str) -> set[tuple[str, str]]:
    """The (figure, qualifier) pairs a text asserts, by nearest-figure attachment.

    A capacity qualifier describes the nearest capacity figure to its left in
    its own sentence, and the nearest to its right when none precedes it. That
    is the dominant form both ways round — "operational ... of 43.6 GW" and
    "52 GW of nameplate capacity" — and it is what keeps one sentence naming
    two figures from attaching one figure's qualifier to the other.
    """
    pairs: set[tuple[str, str]] = set()
    for sentence in re.split(r"(?<=[.!?])\s+", text.casefold()):
        figures = _capacity_figures(sentence)
        if not figures:
            continue
        for word in _QUALIFIER_WORDS:
            for match in re.finditer(rf"\b{word}\b", sentence):
                left = [item for item in figures if item[0] < match.start()]
                figure = (
                    max(left, key=lambda item: item[0])[1]
                    if left
                    else min(figures, key=lambda item: item[0])[1]
                )
                pairs.add((figure, word))
    return pairs


def unattached_qualifiers(text: str, corpus: str) -> list[str]:
    """Qualifiers a statement attaches to a figure its evidence does not.

    The audited report called 43.6 GW "nameplate" while its source gave 43.6 GW
    as *operational* capacity and nearly 52 GW of nameplate capacity. Both
    words are in the corpus, so a word-level test passes that sentence; the
    pairing is the claim, and the pairing is what is checked here.
    """
    attested = _qualifier_attachments(corpus)
    return [
        f"{qualifier} {figure}"
        for figure, qualifier in sorted(_qualifier_attachments(text))
        if (figure, qualifier) not in attested
    ]


def _bounded_passage(text: str, *, limit: int) -> str:
    """One passage carrying at most ``limit`` characters, cut at a boundary.

    The cut lands on a sentence boundary when there is one inside the budget
    and on the last word boundary otherwise, and either way it says how much
    was withheld and whether the cut is a sentence end. A partial word is a
    token the writer cannot read; a partial sentence is a fragment it must not
    restate, which the marker states.
    """
    if len(text) <= limit:
        return text
    head = text[:limit]
    boundary = max(
        head.rfind(". "), head.rfind(".\n"), head.rfind("! "), head.rfind("? ")
    )
    if boundary > 0:
        kept = head[: boundary + 1]
        return kept + _PACKET_OMISSION.format(count=len(text) - len(kept))
    space = head.rfind(" ")
    if space <= 0:
        return _PACKET_OMISSION_UNSENTENCED.format(count=len(text))
    kept = head[:space]
    return kept + _PACKET_OMISSION_UNSENTENCED.format(count=len(text) - len(kept))


def _evidence_lines(
    evidence_ids: Sequence[str],
    evidence: Mapping[str, EvidenceUnit],
    *,
    budget: int = PACKET_SUPPORT_CHARS,
) -> list[str]:
    """``id locator "excerpt"`` for each selected passage, in selected order.

    The excerpt is published whole wherever it fits. It is the passage a claim
    rests on, and the writer may only restate what it can see: clamping it to
    the ledger's 200-character display bound hid figures sitting below a page's
    navigation — the same shape that made the audited run's fact checker miss
    nine of its fourteen verdicts. ``budget`` is a *model-input* bound: whole
    passages are carried until it is spent, a passage that cannot fit is cut
    between sentences and its withheld count stated, and the passages that were
    not shown at all are named rather than silently dropped.
    """
    if budget <= 0:
        if not evidence_ids:
            return []
        return [
            f"({len(evidence_ids)} further selected passage(s) were not shown "
            f"for length: {', '.join(evidence_ids)})"
        ]
    lines: list[str] = []
    used = 0
    omitted: list[str] = []
    for evidence_id in evidence_ids:
        unit = evidence.get(evidence_id)
        if unit is None:
            continue
        excerpt = unit.excerpt
        if len(excerpt) > budget - used:
            if lines:
                omitted.append(evidence_id)
                continue
            excerpt = _bounded_passage(excerpt, limit=budget)
        used += len(excerpt)
        lines.append(
            f'{evidence_id} {unit.locator} {unit.source_url} "{excerpt}"'
        )
    if omitted:
        lines.append(
            f"({len(omitted)} further selected passage(s) were not shown for "
            f"length: {', '.join(omitted)})"
        )
    return lines


def _is_recommendation(text: str) -> bool:
    lowered = f" {text.casefold()} "
    return any(marker in lowered for marker in _PRESCRIPTIVE_MARKERS)


def _support_cost(lines: Sequence[str]) -> int:
    """The characters one group of evidence lines spends from the budget."""
    return sum(len(line) for line in lines)


def _unrecorded_evidence_claim(text: str, failures: Sequence[str]) -> str:
    """The evidence state a note asserts that this pass did not record."""
    lowered = text.casefold()
    recorded = " ".join(failures).casefold()
    for label, wanted, recorded_tokens in _UNSUPPORTED_STATE_TOKENS:
        if not any(token in lowered for token in wanted):
            continue
        if any(token in recorded for token in recorded_tokens):
            continue
        return label
    return ""


def _statement_for_claims(
    *,
    statement_id: str,
    text: str,
    claims: Sequence[Claim],
    context: DraftContext,
    basis: str = "",
    mode: StatementMode | None = None,
) -> ReportStatement:
    """The statement record behind a validated point.

    The draft path's half of the shared derivation: it supplies the task's
    evidence, clusters and target dimensions, plus the basis and the explicit
    mode the validated path knows and the fixture path does not.
    """
    return derive_statement(
        statement_id=statement_id,
        text=text,
        claims=claims,
        clusters=context.clusters,
        evidence=context.evidence,
        dimensions_by_target=context.dimensions_by_target,
        basis=basis,
        mode=mode,
    )


def _cited_evidence(
    claims: Sequence[Claim], context: DraftContext, *, casefold: bool = True
) -> str:
    """The exact passages the named claims selected, as one text.

    A statement may restate its evidence; this is the text it may restate
    *from*. It is the selected support, not the whole read, so a figure pulled
    from elsewhere in the document is still an unattested atom here. The
    recorded proposition's own dimensions are included: an atom that records
    its subject, place, period, or attribution is the extractor's finding
    about the evidence, and a cell may state what the atom records.
    """
    parts: list[str] = []
    for claim in claims:
        for evidence_id in claim.evidence_selection:
            unit = context.evidence.get(evidence_id)
            if unit is not None:
                parts.append(unit.excerpt)
    for cluster_id in clusters_for_claims(claims, context.clusters):
        cluster = context.clusters[cluster_id]
        proposition = cluster.proposition
        parts.append(proposition.text)
        parts.extend(
            value
            for value in (
                proposition.subject,
                proposition.geography,
                proposition.observation_period,
                proposition.population,
                proposition.quantity_noun,
                proposition.attribution,
                proposition.forecast_status,
            )
            if value
        )
        for evidence_id in cluster.evidence_ids:
            unit = context.evidence.get(evidence_id)
            if unit is not None:
                parts.append(unit.excerpt)
    text = " ".join(parts)
    return text.casefold() if casefold else text


def _claims_carrying(
    claims: Sequence[Claim],
    accepted: Sequence[str],
    *,
    text: str,
    context: DraftContext,
    basis: str,
) -> tuple[list[Claim], list[str]]:
    """The named claims that carry every URL the point cites, and those URLs.

    A point's citations must resolve through each claim it names: the
    quality gate reads a claim that does not cite a URL its point cites as an
    unresolved citation. So a named claim that lacks one of the point's URLs
    is dropped from the point. No URL is ever added to a claim.

    When no named claim carries them all, the anchor is chosen by evidence,
    not by the order the draft listed its claims: a relay named beside the
    issuer's own claim, citing only the issuer's URL, used to keep whichever
    claim the draft happened to list first, and lost the point when the relay
    came first and its own claim did not state the point's figure. Each
    candidate that carries any cited URL is tried, most-cited-URLs first, and
    the first candidate whose narrowed claims attest the point's own figures
    is kept; a candidate that carries what the point cites but not what it
    states is not what the point is about.
    """
    def urls(claim: Claim) -> set[str]:
        return {normalize_source_url(url) for url in claim.source_urls}

    wanted = set(accepted)
    carriers = [claim for claim in claims if wanted <= urls(claim)]
    if carriers:
        return carriers, list(accepted)
    candidates = [claim for claim in claims if wanted & urls(claim)]
    ranked = sorted(
        candidates, key=lambda claim: len(wanted & urls(claim)), reverse=True
    )
    for anchor in ranked:
        kept = [url for url in accepted if url in urls(anchor)]
        narrowed = [claim for claim in claims if set(kept) <= urls(claim)]
        if not _unsupported_figures(text, narrowed, context, basis):
            return narrowed, kept
    anchor = candidates[0]
    kept = [url for url in accepted if url in urls(anchor)]
    return [claim for claim in claims if set(kept) <= urls(claim)], kept


def _build_point(
    *,
    text: str,
    labels: Sequence[str],
    urls: Sequence[str],
    context: DraftContext,
    where: str,
    statement_prefix: str = "S",
    basis: str = "",
    decision_section: bool = False,
) -> ReportPoint | None:
    """Validate one drafted point against the checked-claim registry.

    Returns ``None`` and appends a project-generated reason when the point
    cannot be printed: no text, no known checked claim, no source URL, a URL
    the claims it names do not carry, a figure or a name its selected evidence
    does not state, or a recommendation outside the answer section the
    question asked for. Reasons never quote provider text, so they are safe
    for ``ResearchError.details`` and for the ledger.
    """
    if not text.strip():
        context.reject_point(
            "unlinked_statement", where, "blank statement",
            text=text, claim_ids=labels, urls=urls,
        )
        return None
    claims, unknown = _resolve_claims(labels, approved=context.approved)
    if not claims:
        context.reject_point(
            "unlinked_statement", where, "no known checked claim",
            text=text, claim_ids=labels, urls=urls,
        )
        return None
    if unknown:
        context.rejected.append(
            f"{where}: {unknown} claim id(s) outside the registry"
        )
    claim_urls: list[str] = [
        url
        for url in dict.fromkeys(
            normalize_source_url(raw)
            for claim in claims
            for raw in claim.source_urls
        )
        if url
    ]
    approved_urls = set(claim_urls)
    accepted: list[str] = []
    dropped = 0
    for raw in urls:
        url = normalize_source_url(raw)
        if url not in approved_urls:
            dropped += 1
            continue
        if url not in accepted:
            accepted.append(url)
    if not accepted and urls:
        # Something was drafted, but none of it survived narrowing to the
        # named claims: cite those claims' own urls instead of losing the
        # point over one relay-only citation list.
        accepted = list(claim_urls)
    if dropped:
        context.dispositions.append("citation_narrowed_to_claims")
        context.rejected.append(
            f"{where}: {dropped} url(s) not on those claims were dropped"
        )
    if not accepted:
        context.reject_point(
            "unlinked_statement", where, "no source url for a settled statement",
            text=text, claim_ids=labels, urls=urls,
        )
        return None
    named = len(claims)
    claims, accepted = _claims_carrying(
        claims, accepted, text=text, context=context, basis=basis
    )
    if len(claims) < named:
        # Not a refusal: the point stands on the claims that carry what it
        # cites. A relay named beside the issuer's own claim, citing only the
        # issuer's page, left audit2's S001 with an unresolved citation.
        context.dispositions.append("claim_link_narrowed_to_citation")

    if not decision_section and _is_recommendation(text):
        context.reject_point(
            "unsupported_recommendation", where,
            "a recommendation outside the answer",
            text=text, claim_ids=labels, urls=urls,
        )
        context.returned.append(summarize_text(text, limit=_CLAIM_TEXT_CHARS))
        return None
    lowered = dropped_modality(text, claims)
    if lowered:
        context.reject_point(
            "unsupported_modality", where,
            "the statement drops the modality its evidence carries",
            text=text, claim_ids=labels, urls=urls,
        )
        context.returned.append(summarize_text(text, limit=_CLAIM_TEXT_CHARS))
        return None
    cited = _cited_evidence(claims, context) or context.corpus
    hardened = hardened_modality(text, cited)
    if hardened:
        context.reject_point(
            "unsupported_modality", where,
            "the statement hardens the modality its evidence carries",
            text=text, claim_ids=labels, urls=urls,
        )
        context.returned.append(summarize_text(text, limit=_CLAIM_TEXT_CHARS))
        return None
    qualifiers = unattached_qualifiers(text, cited)
    if qualifiers:
        context.reject_point(
            "unsupported_qualifier", where,
            "an unsupported figure qualification",
            text=text, claim_ids=labels, urls=urls,
        )
        context.returned.append(summarize_text(text, limit=_CLAIM_TEXT_CHARS))
        return None
    missing = _unsupported_figures(text, claims, context, basis)
    if missing:
        context.reject_point(
            "unsupported_figure", where, "an unsupported figure",
            text=text, claim_ids=labels, urls=urls,
        )
        context.returned.append(summarize_text(text, limit=_CLAIM_TEXT_CHARS))
        return None
    extrapolation = _unsupported_names(text, claims, context)
    if extrapolation:
        context.reject_point(
            "unsupported_extrapolation", where,
            "a name or place the evidence does not state",
            text=text, claim_ids=labels, urls=urls,
        )
        context.dispositions.append("returned_to_fact_checker")
        context.returned.append(summarize_text(text, limit=_CLAIM_TEXT_CHARS))
        return None
    return ReportPoint(
        text=_display_clamp(text, limit=_POINT_CHARS),
        claim_ids=[claim.claim_id for claim in claims],
        source_urls=accepted,
        statement=_statement_for_claims(
            statement_id=context.next_id(statement_prefix),
            text=_display_clamp(text, limit=_POINT_CHARS),
            claims=claims,
            context=context,
            basis=basis,
        ),
    )


def _unsupported_names(
    text: str,
    claims: Sequence[Claim],
    context: DraftContext,
) -> list[str]:
    """Named entities the cited evidence does not state.

    A statement that carries a result into a country, a company, or a
    programme its evidence never names has extrapolated. The check is the same
    attestation the figures get, applied to the names: a geography is either
    in the evidence or it is a new claim.
    """
    # A checked claim's own words, and what its ADMITTED provenance recorded
    # from the page — the attributed issuer, the measured scope, and the
    # month of the release date — are names that evidence states. ``vintage``
    # and ``statement_date`` are not admitted (researcher.py stores them as
    # the extractor wrote them, never checked against the read), so they are
    # left out here: setting audit2 claim #1's vintage to a BloombergNEF
    # title let that name onto an EIA figure with rejected=[].
    recorded: list[str] = []
    for claim in claims:
        provenance = claim.provenance
        recorded.append(claim.text)
        recorded.extend(
            value
            for value in (provenance.attributed_issuer, provenance.measure_scope)
            if value
        )
        month = re.match(r"(?:19|20)\d{2}-(\d{2})", provenance.release_date or "")
        if month and 1 <= int(month.group(1)) <= 12:
            recorded.append(calendar.month_name[int(month.group(1))])
    joined = " ".join(recorded)
    corpus = f"{_cited_evidence(claims, context) or context.corpus} {joined.casefold()}"
    # The acronym check reads case: a folded corpus can spell "SEIA" out of a
    # split "u"/"s" that a real, case-preserved "U.S. Energy Information
    # Administration" never does.
    raw_corpus = (
        f"{_cited_evidence(claims, context, casefold=False) or context.raw_corpus} "
        f"{joined}"
    )
    return [
        atom
        for atom in unattested_atoms(text, corpus, raw_corpus)
        if not re.match(r"\d", atom)
    ]


def _claim_attested_text(
    claims: Sequence[Claim], context: DraftContext
) -> str:
    """The figures one statement may restate: what its own claims state.

    The claim's own words, the values its atoms record, and the provenance it
    carries — an edition and a release date are part of a figure, so a
    statement may name them. The *selected passage* is deliberately not part
    of it: one paragraph can state several propositions, and a figure in it
    that no cited claim states is not thereby checked. The measured smoke
    published 19.6 GW statements on the sole 10.4 GW claim's cluster exactly
    that way, and the reader met an unchecked forecast as a settled fact.

    Only *admitted* provenance travels: the attributed issuer, the measured
    scope, the recorded data period, and the release date's own month —
    the same bound ``_unsupported_names`` already holds a rendered point to.
    ``vintage`` and ``statement_date`` are the extractor's own prose, never
    checked against the read (``researcher.py`` stores them as written), so
    they are left out here: admitting them let audit2's #1 borrow a
    BloombergNEF title onto an EIA figure, and a cell built on an
    unchecked vintage alone is the same hallucination.
    """
    parts: list[str] = []
    for claim in claims:
        parts.append(claim.text)
        provenance = claim.provenance
        parts.extend(
            value
            for value in (
                provenance.attributed_issuer,
                provenance.measure_scope,
                provenance.data_period,
            )
            if value
        )
        month = re.match(r"(?:19|20)\d{2}-(\d{2})", provenance.release_date or "")
        if month and 1 <= int(month.group(1)) <= 12:
            parts.append(calendar.month_name[int(month.group(1))])
    for cluster_id in clusters_for_claims(claims, context.clusters):
        proposition = context.clusters[cluster_id].proposition
        parts.append(proposition.text)
        parts.extend(
            value
            for value in (
                proposition.subject,
                proposition.value,
                proposition.unit,
                proposition.observation_period,
                proposition.geography,
                proposition.population,
                proposition.quantity_noun,
                proposition.denominator,
                proposition.comparator,
            )
            if value
        )
    return " ".join(parts).casefold()


def _unsupported_figures(
    text: str,
    claims: Sequence[Claim],
    context: DraftContext,
    basis: str,
) -> list[str]:
    """Figures the cited claims do not state.

    A unit conversion or an arithmetic step passes only as a recorded
    derivation: the statement declares a ``basis``, that basis names the
    operation, and the premises it rests on — the figures the cited evidence
    states — are attested. That is what separates a checked derivation from a
    model that did some arithmetic and called it a conversion, and it is the
    one place the selected passage still attests a figure: a derivation is
    *about* the evidence it was read off.
    """
    missing = [
        atom
        for atom in unattested_atoms(text, _claim_attested_text(claims, context))
        if re.match(r"\d", atom)
    ]
    if not missing:
        return []
    if not basis.strip():
        return missing
    corpus = _cited_evidence(claims, context) or context.corpus
    tokens = _corpus_tokens(corpus)
    if any(_figure_number(atom) in tokens for atom in missing):
        # Restating a figure the passage already carries is not deriving it:
        # a declared basis only earns a pass for a number the passage itself
        # does not state.
        return missing
    if not _names_an_operation(basis):
        return missing
    if not _derivation_premises(basis, tokens):
        return missing
    return []


def _build_cell(
    *,
    text: str,
    row: ReportPoint,
    context: DraftContext,
    where: str,
    cell: str,
) -> tuple[str, ReportStatement]:
    """One table cell: published only when the cited evidence carries it.

    A cell is a factual assertion like any other, so it is either attested by
    the evidence its row cites or it is repaired to ``not stated`` with the
    repair recorded. ``not stated`` is the contract's own sentinel — the
    prompt asks for it when the evidence is silent — so it is read as "no
    cell", never as prose to attest. The returned statement is always present,
    so the reader can see that the cell was considered and what happened to
    it.

    Words *and* atoms are checked. ``unattested_words`` alone leaves a cell
    with no words in it — a Period, a date, a bare figure — attested
    vacuously, and a fabricated period would then publish as an attributed
    statement with the row's evidence ids attached, which is worse than the
    context label it replaced: it would read as a checked fact.

    The check runs on the whole written cell and the display bound is applied
    afterwards, exactly as it is for a point: a cell that fits the evidence
    may still be too long to publish, and its own cut marker is this project's
    word, not one the evidence has to carry. Attesting the whole cell is the
    stricter direction — every word the published prefix carries is a word the
    evidence was asked about.
    """
    raw = " ".join(text.split())
    if raw.casefold().strip(" .") == "not stated":
        raw = ""
    if not raw:
        return "", ReportStatement(
            statement_id=context.next_id("C"),
            text="not stated",
            mode="context",
            basis="the row's evidence does not state this cell",
        )
    selected = _selected_claims(row, context)
    evidence_text = (
        f"{_cited_evidence(selected, context) or context.corpus} "
        f"{_claim_attested_text(selected, context)}"
    ).strip()
    unattested = [
        *unattested_words(raw, evidence_text),
        *unattested_atoms(raw, evidence_text),
    ]
    if unattested:
        context.note(
            "unsupported_cell",
            f"{where} {cell}",
            "no evidence for this cell",
        )
        context.dispositions.append("returned_to_fact_checker")
        # The refusal names the cell at its own logging bound, which is what
        # the fact checker is asked to adjudicate — never the display form.
        context.returned.append(summarize_text(raw, limit=_CELL_CHARS))
        return "", ReportStatement(
            statement_id=context.next_id("C"),
            text="not stated",
            mode="context",
            basis="the cited evidence does not carry this cell",
        )
    written = _display_clamp(raw, limit=_CELL_CHARS)
    statement = _statement_for_claims(
        statement_id=context.next_id("C"),
        text=written,
        claims=selected,
        context=context,
        basis="cell carried by the row's evidence",
        mode="attributed" if selected else "context",
    )
    return written, statement


def _build_constraint(
    draft: ConstraintDraft,
    *,
    context: DraftContext,
    where: str,
) -> ReportConstraint | None:
    point = _build_point(
        text=draft.constraint,
        labels=draft.claim_ids,
        urls=draft.source_urls,
        context=context,
        where=where,
        statement_prefix="C",
        decision_section=True,
    )
    if point is None:
        return None
    mechanism, mechanism_statement = _build_cell(
        text=draft.deployment_mechanism,
        row=point,
        context=context,
        where=where,
        cell="deployment mechanism",
    )
    geography, geography_statement = _build_cell(
        text=draft.geography,
        row=point,
        context=context,
        where=where,
        cell="geography",
    )
    return ReportConstraint(
        text=point.text,
        claim_ids=point.claim_ids,
        source_urls=point.source_urls,
        statement=point.statement,
        deployment_mechanism=mechanism,
        geography=geography,
        mechanism_statement=mechanism_statement,
        geography_statement=geography_statement,
    )


def _build_answer_row(
    draft: AnswerRowDraft,
    *,
    context: DraftContext,
    where: str,
) -> ReportAnswerRow | None:
    """Validate one answer-kind row: two labels and one evidenced finding."""
    point = _build_point(
        text=draft.finding,
        labels=draft.claim_ids,
        urls=draft.source_urls,
        context=context,
        where=where,
        statement_prefix="A",
        decision_section=True,
    )
    if point is None:
        return None
    # The label columns are factual cells like any other — a Period is a date
    # and a Subject is usually a named entity — so they are attested against
    # the row's evidence and repaired to "not stated" when it does not carry
    # them, exactly as the constraint table's mechanism and geography are.
    labels: list[ReportStatement] = []
    for cell_name, raw in (
        ("subject", draft.subject),
        ("dimension", draft.dimension),
    ):
        _, statement = _build_cell(
            text=raw,
            row=point,
            context=context,
            where=where,
            cell=cell_name,
        )
        labels.append(statement)
    return ReportAnswerRow(cells=[*labels, point.statement])


def _build_uncertainty_statements(
    notes: Sequence[str],
    *,
    context: DraftContext,
    published: Sequence[ReportPoint] = (),
) -> list[ReportStatement]:
    """Turn drafted uncertainty prose into checked, figure-free statements.

    A note is allowed to be source-free — it is the one place this pass's own
    framing belongs — but it is *not* allowed to be unchecked: a figure it
    prints is a factual assertion, and an evidence state it asserts must have
    a recorded disposition. An unsupported figure is removed with the phrase
    that carried it, and the note keeps the topic it was about.
    """
    statements: list[ReportStatement] = []
    for position, note in enumerate(notes, start=1):
        if not note.strip():
            continue
        where = f"uncertainty note {position}"
        text = " ".join(note.split())
        unrecorded = _unrecorded_evidence_claim(text, context.failures)
        if unrecorded:
            context.note(
                "unsupported_limitation",
                where,
                f"no recorded disposition for a {unrecorded} read",
            )
            continue
        asserted_scope = scope_fact(text)
        if asserted_scope:
            context.note(
                "unsupported_scope_fact",
                where,
                "a scope fact with no checked claim behind it",
            )
            continue
        denied = _denied_stated_figure(text, context.evidence)
        if denied:
            context.note(
                "unsupported_limitation",
                where,
                "denies a figure the evidence of that work states",
            )
            continue
        if _REPORT_DERIVATION.search(text) and not any(
            point.statement is not None and point.statement.basis
            for point in published
        ):
            context.note(
                "unsupported_limitation",
                where,
                "describes a derived value no published statement carries",
            )
            continue
        repaired = _strip_unsupported_figures(text, context.note_corpus)
        if repaired != text:
            context.note(
                "unsupported_figure",
                where,
                "an unsupported figure",
            )
            context.dispositions.append("returned_to_fact_checker")
            context.returned.append(
                summarize_text(text, limit=_CLAIM_TEXT_CHARS)
            )
        if not repaired.strip():
            continue
        statements.append(
            ReportStatement(
                statement_id=context.next_id("U"),
                text=_display_clamp(repaired, limit=_POINT_CHARS),
                mode="context",
                basis=_uncertainty_basis(repaired),
            )
        )
    return statements


# A note describing a derived value "in this report". Only a published point
# that declared its derivation basis can be what such a note describes;
# otherwise it narrates draft content validation already refused.
_REPORT_DERIVATION = re.compile(
    r"\bin this report\b[^.;]*\b(?:arithmetic|implication|implied|derived|"
    r"derivation)\b|\b(?:arithmetic|implication|implied|derived|derivation)\b"
    r"[^.;]*\bin this report\b",
    re.IGNORECASE,
)
# A note that says a work carries no figure.
_ABSENT_FIGURE = re.compile(
    r"\bno\s+(?:[a-z-]+\s+){0,2}figures?\b|\bwithout\s+(?:a|any)\s+"
    r"(?:[a-z-]+\s+){0,2}figures?\b",
    re.IGNORECASE,
)
# A capitalised multi-word work title ("Short-Term Energy Outlook").
_WORK_TITLE = re.compile(r"\b[A-Z][\w'-]*(?:\s+[A-Z][\w'-]*)+\b")


def _denied_stated_figure(
    text: str, evidence: Mapping[str, EvidenceUnit]
) -> bool:
    """True when a note says a work has no figure while its read states one.

    The audited note denied the January STEO a capacity figure because its
    checked claim had lost the number; the selected passages of the same read
    said "growing by 47% (14 GW) in 2025". A limitation about evidence is read
    against the evidence, never against a lossy restatement of it.
    """
    if not _ABSENT_FIGURE.search(text):
        return False
    titles = {
        title.casefold()
        for title in _WORK_TITLE.findall(text)
        if " " in title
    }
    if not titles:
        return False
    urls = {
        unit.source_url
        for unit in evidence.values()
        if any(title in unit.excerpt.casefold() for title in titles)
    }
    return any(
        _CAPACITY_FIGURE.search(unit.excerpt)
        for unit in evidence.values()
        if unit.source_url in urls
    )


# The basis a source-free note carries is the group key the renderer reads,
# not the token that happened to match: the note vocabulary and the reader's
# groups are one vocabulary, and a basis that said "disagree" while the
# renderer looked for "conflicting" left the note ungrouped. Order matters —
# an unretrieved topic is a stronger fact than a disagreement about it, and a
# note that is both conflicting and out of scope belongs in the narrower
# group.
_UNCERTAINTY_BASIS_TOKENS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "not acquired",
        (
            "not acquired",
            "was not retrieved",
            "no read",
            "not read",
            "never retrieved",
            "could not be retrieved",
        ),
    ),
    (
        "outside scope",
        ("outside scope", "out of scope", "beyond the scope", "not in scope"),
    ),
    (
        "uncertain/conflicting",
        (
            "disagree",
            "conflict",
            "contradict",
            "uncertain",
            "contested",
            "inconsistent",
        ),
    ),
)


def _uncertainty_basis(text: str) -> str:
    """Classify a source-free note into the group the reader meets it in."""
    lowered = text.casefold()
    for key, tokens in _UNCERTAINTY_BASIS_TOKENS:
        if any(token in lowered for token in tokens):
            return key
    return "uncertain/conflicting"


# The units a figure may carry, so that removing an unsupported figure takes
# its unit with it and nothing else. "890 GW" is one phrase; "2030 horizon" is
# a year and a noun, and the noun has to survive the repair.
_UNIT_WORDS = frozenset(
    {
        "gw", "gws", "mw", "mws", "kw", "kws", "tw", "gwh", "mwh", "kwh",
        "kwh/y", "gw/y", "mw/y", "twh", "km", "km2", "sq", "kg", "t", "mt",
        "kt", "bn", "million", "billion", "trillion", "usd", "eur", "gbp",
        "dollars", "euros", "pounds", "percent", "%", "pct", "tonnes",
        "tons", "jobs", "units", "seconds", "minutes", "hours", "days",
        "weeks", "months", "years", "people", "households", "vehicles",
    }
)

# Abbreviations that name a technology, a quantity or a common noun rather
# than a place or an organisation. Auditable on purpose: an entry here is a
# token the name check will never refuse, so each one has to be defensible —
# and the domain's own vocabulary (EVs, PV, CO2, GDP, HVDC, PPAs) is written
# this way constantly, while the evidence spells it out. Plurals are listed
# beside their singular for the same reason: "GHGs" is the same noun as "ghg".
#
# "UK", "EU", "US" and "IEA" are deliberately absent: catching a place or an
# agency the evidence never names is what the acronym check is for.
#
# This carve-out is for *prose*. A table cell is checked by
# ``unattested_words`` too, which reads "EVs" as a content word, so a cell
# still has to be lifted from the evidence rather than abbreviated — the cell
# path is the one place the wording is copied rather than composed.
_COMMON_ABBREVIATIONS = frozenset(
    {
        "ai", "ac", "api", "bess", "bevs", "cagr", "capex", "ccs", "ccus",
        "ch4", "co2", "covid", "dc", "ders", "ev", "evs", "gdp", "ghg",
        "ghgs", "gpus", "hvdc", "ice", "ict", "iot", "lcoe", "llms", "lng",
        "ml", "ndcs", "nox", "ok", "opex", "phevs", "ppa", "ppas", "pv",
        "smrs", "tsos", "it",
    }
)


def _strip_unsupported_figures(text: str, corpus: str) -> str:
    """Remove every figure the recorded evidence does not state.

    The unit goes with the figure — "the 890 GW of installed storage was not
    reported" becomes "the installed storage was not reported" — because a
    caveat that prints the number it says is missing has still told the reader
    the number. Only a recognised *unit* is consumed with it: a year followed
    by an ordinary noun ("the 2030 horizon") keeps its noun, so the repair
    cannot leave a sentence without its subject.
    """
    sentences: list[str] = []
    tokens = _corpus_tokens(corpus)
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        figures = [
            figure
            for figure in _significant_figures(sentence)
            if _figure_number(figure) not in tokens
        ]
        if not figures:
            sentences.append(sentence)
            continue
        repaired = sentence
        for figure in figures:
            number = _figure_number(figure)
            # `_significant_figures` attaches a unit only when the next word
            # is one this contract knows, so whatever follows the number here
            # is that unit and nothing else.
            tail = figure.strip()[len(number) :].strip()
            repaired = re.sub(
                rf"\b{re.escape(number)}\s*"
                + (rf"{re.escape(tail)}\s*" if tail else "")
                + r"(?:of\s+)?",
                "",
                repaired,
            )
        repaired = " ".join(repaired.split())
        if _content_tokens(repaired):
            sentences.append(repaired)
    return " ".join(sentences).strip()


def date_basis_for(contract: AnswerContract) -> str:
    """Which period the question is about, and the date the answer is as of.

    Both halves are things a reader checks: the frozen ``as_of_date`` is the
    date the answer claims to be current for, and the requirement is the period
    the plan decided the question means. The planner's own requirement
    frequently names that date already, and appending it again would print one
    fact twice on one line — a reader counting two dates there reads two
    claims — so the frozen date is added only when the requirement does not
    state it.
    """
    requirement = contract.evidence_period_requirement.strip()
    if contract.as_of_date in requirement:
        return requirement
    return f"{requirement}; as of {contract.as_of_date}"


def build_report_composition(
    task: SynthesisTask,
    draft: ReportDraft | None,
    *,
    max_sections: int,
    limitations: Sequence[str],
    quality_status: str = QUALITY_STATUS_NOT_GATED,
) -> tuple[ReportComposition, list[str]]:
    """Validate a draft into the composition both artifacts render.

    Returns the composition and the enumerated reasons any drafted content was
    refused. Nothing is silently dropped: an empty reader report says which
    drafted statements were refused and why. Every statement that survives is
    a ``ReportStatement`` — the reader mode its evidence supports, the exact
    selected evidence ids behind it, the targets and dimensions it answers,
    and any derivation it declared — so the reader map is complete by
    construction rather than by inspection.
    """
    if max_sections < 1:
        raise ValueError("max_sections must be at least 1")
    prompt_registry = (
        task.claim_packet
        if task.claim_packet is not None
        else claim_registry(task.claims)
    )
    approved = {label: claim for label, claim in prompt_registry}
    context = DraftContext(
        approved=approved,
        clusters=dict(task.claim_clusters),
        evidence=dict(task.evidence_units),
        targets={target.target_id: target for target in task.targets},
        dimensions_by_target=dimensions_by_target(task.targets),
        corpus=_attestation_corpus(approved, task.evidence_units),
        raw_corpus=_attestation_corpus(
            approved, task.evidence_units, casefold=False
        ),
        failures=measured_failures(task),
    )
    context.note_corpus = _note_corpus(task, context.corpus)
    summary: list[ReportPoint] = []
    constraints: list[ReportConstraint] = []
    rows: list[ReportAnswerRow] = []
    sections: list[ReportSection] = []
    uncertainty: list[ReportStatement] = []
    if draft is not None:
        seen: set[tuple[str, tuple[str, ...]]] = set()
        summary = _build_points(
            draft.executive_summary,
            context=context,
            where="executive summary",
            seen=seen,
        )
        constraints = _build_constraints(draft.ranked_constraints, context=context)
        rows = _build_answer_rows(draft.answer_rows, context=context)
        if len(draft.sections) > max_sections:
            context.rejected.append(
                f"{len(draft.sections) - max_sections} section(s) past the "
                "section cap"
            )
        for position, item in enumerate(draft.sections[:max_sections], start=1):
            title = " ".join(item.title.split())
            if not title:
                context.rejected.append(f"section {position}: blank title")
                continue
            points = _build_points(
                item.points,
                context=context,
                where=f"section {position}",
                seen=seen,
            )
            if not points:
                context.rejected.append(
                    f"section {position}: no printable point"
                )
                continue
            sections.append(ReportSection(title=title, points=points))
        uncertainty = _build_uncertainty_statements(
            draft.uncertainty_notes,
            context=context,
            published=[
                *summary,
                *(point for section in sections for point in section.points),
            ],
        )
    contract = task.answer_contract
    if contract is not None and contract.answer_kind in ("factual", "historical"):
        summary, sections = _scope_answer_summary(
            summary, sections, context=context
        )
    if draft is not None:
        summary, sections = _render_omitted_bound_claims(
            summary, sections, context=context
        )
    if not rows:
        # A draft that supplied no rows is not a report with no answer: the
        # statements that answer the question are already validated here, so
        # the table is derived from them rather than asked for again.
        rows = _derive_answer_rows(
            answer_kind=contract.answer_kind if contract is not None else None,
            summary=summary,
            sections=sections,
            context=context,
        )
    composition = ReportComposition(
        question=task.instruction,
        session_id=task.session_id,
        iteration=task.iteration,
        max_iterations=task.max_iterations,
        as_of=task.as_of,
        scope=task.scope,
        quality_status=quality_status,
        sub_topics=list(task.sub_topics),
        claims=list(task.claims),
        sources=list(task.sources),
        findings=list(task.findings),
        limitations=list(limitations),
        errors=list(task.errors),
        summary=summary,
        constraints=constraints,
        sections=sections,
        uncertainty_notes=[statement.text for statement in uncertainty],
        uncertainty_statements=uncertainty,
        rejected=context.rejected,
        rejected_points=context.rejected_points,
        answer_kind=contract.answer_kind if contract is not None else None,
        answer_rows=rows,
        claim_clusters=dict(task.claim_clusters),
        evidence_units=dict(task.evidence_units),
        statement_dispositions=list(dict.fromkeys(context.dispositions)),
        returned_to_fact_checker=list(dict.fromkeys(context.returned)),
        generated_on=task.generated_on,
        date_basis=(
            date_basis_for(contract) if contract is not None else ""
        ),
        requested_word_limit=(
            contract.requested_word_limit if contract is not None else None
        ),
    )
    # The statement map is checked before anything renders: an unknown
    # evidence id or a substantive statement with no link behind it is a
    # refusal, not a rendering surprise. Then the fit runs here, once: the
    # length ceiling is a property of the composition this pass publishes, so
    # the gates, the reviewer, the quality record and the Markdown all read
    # the same statement set — with the drop reasons on the composition where
    # every one of them can see them. Fitting inside each renderer instead let
    # a report omit the only answer to a critical target while every consumer
    # of ``state.composition`` still counted that statement.
    validate_report_statements(composition)
    fitted, _fit_reasons = fit_report_composition(composition)
    return fitted, context.rejected


_UNMATCHED_TITLE = "Other reported figures (not matched to a planned question)"
_OMITTED_BOUND_TITLE = "Checked findings for planned questions the draft left out"


def _scope_answer_summary(
    summary: Sequence[ReportPoint],
    sections: Sequence[ReportSection],
    *,
    context: DraftContext,
) -> tuple[list[ReportPoint], list[ReportSection]]:
    """Keep the answer slot for figures that answer a planned question.

    A measured summary point whose claims are bound to no planned target
    moves to the findings, under a heading that says so, but only when a
    bound summary point already answers the same role: the same stated
    period, and the same forecast-or-outcome reading. Otherwise it stays,
    because it is the only answer to that part of the question, and the
    reader report labels it as not matched to a planned question. It is
    never dropped. When no measured summary point is bound at all, binding
    failed for the whole pass, and the summary is left as drafted.
    """
    measured = [point for point in summary if _answering_statement(point)]
    bound_roles = {
        _answer_role(point)
        for point in measured
        if _answers_planned_target(point, context)
    }
    if not bound_roles:
        return list(summary), list(sections)
    kept: list[ReportPoint] = []
    moved: list[ReportPoint] = []
    for point in summary:
        if (
            _answering_statement(point)
            and not _answers_planned_target(point, context)
            and _answer_role(point) in bound_roles
        ):
            moved.append(point)
        else:
            kept.append(point)
    shown = {
        tuple(point.claim_ids) for section in sections for point in section.points
    }
    moved = [point for point in moved if tuple(point.claim_ids) not in shown]
    result = list(sections)
    if moved:
        context.dispositions.append("unmatched_answer_moved_to_findings")
        result.append(ReportSection(title=_UNMATCHED_TITLE, points=moved))
    return kept, result


def _text_role(text: str) -> tuple[frozenset[str], bool]:
    """What part of the question a measured text answers.

    The measurement years it states ("in 2025", "for 2025") and whether it is
    hedged: "could almost double to 18.2 GW" answers the forecast half and
    "a record 15 GW was added" the outcome half, even for the same year.
    """
    years = frozenset(
        match.group(1) for match in _MEASURE_PERIOD.finditer(text)
    )
    return years, bool(hedge_marker(text))


def _answer_role(point: ReportPoint) -> tuple[frozenset[str], bool]:
    """What part of the question a measured point answers."""
    return _text_role(point.text)


def _normalized_text(text: str) -> str:
    """Case- and whitespace-insensitive text, for matching one restated fact."""
    return " ".join(text.casefold().split())


def _restates_a_rendered_point(
    claim: Claim, rendered: Sequence[ReportPoint]
) -> bool:
    """True when an already-rendered point already states this claim's content.

    Two checked claims can carry one source's own finding twice: audit2's #1
    and #17 both carry EIA 64705's 10.4 GW addition, from different clusters.
    Giving #17 its own point under "left out" reprints a fact the reader
    already met rather than answering a planned question the draft missed.
    Matched by the same measured years plus a shared unit-bearing figure, or
    by the claim's own wording normalizing to a rendered point's — either is
    one restated fact, however differently worded. The hedge component of
    the role is deliberately not compared: audit-3's own 19.6 GW claim read
    "planned" where the rendered point read "carried the projection ...
    plans" (no hedge marker), so comparing hedge state too missed the match
    and printed the same figure twice.
    """
    return _find_restated_point(claim, rendered) is not None


_FORECAST_MARKER_PATTERN = re.compile(
    r"\b(?:plan|plans|planned|planning|project|projects|projected|"
    r"projection|projections|forecast|forecasts|forecasted|forecasting|"
    r"expect|expects|expected|anticipate|anticipates|anticipated)\b",
    re.IGNORECASE,
)


def _forecast_role(text: str) -> bool:
    """True when the text reads as a plan, projection, or forecast — not a
    stated outcome. Broader than ``hedge_marker``'s own pattern on purpose:
    "carried the projection ... plans to add" and "planned to add" are the
    same forecast role in different words, and a text that reads either way
    must never be matched against an outcome that merely shares its figure
    and year — one issuer's own forecast is not its own later actual.
    """
    return bool(_FORECAST_MARKER_PATTERN.search(text))


# A past-tense, realised-outcome verb. Matched only outside a future or
# conditional modal's own clause ("would be installed" still names a plan,
# not a report of what happened) via ``_clause_around``, the same governing
# scope ``hardened_modality`` reads a strong modal's exemption from.
_REALIZED_OUTCOME_PATTERN = re.compile(
    r"\b(?:installed|added|deployed|commissioned|came\s+online|built|"
    r"reached|hit|beat|exceeded|surpassed)\b",
    re.IGNORECASE,
)
_FUTURE_MODAL_PATTERN = re.compile(
    r"\b(?:would|will|could|might|may|should|shall)\b", re.IGNORECASE
)


def _realized_outcome(text: str) -> bool:
    """True when the text reports something that already happened.

    A matched verb inside a clause a future or conditional modal governs is
    still a plan ("EIA forecast that 16 GW would be installed"), not a
    report of an outcome, so it does not count. Nor does a verb read as an
    infinitive complement ("expected to hit 15 GW"): "hit" and "beat" spell
    their infinitive and past-tense forms identically, and a verb right
    after "to" is what a forecast is expected *to do*, not a report that it
    did it. Mirrors ``claim_clusters.py``'s own to-infinitive guard for the
    same ambiguity, so the two classifiers agree.
    """
    for match in _REALIZED_OUTCOME_PATTERN.finditer(text):
        if re.search(r"\bto\s+\Z", text[: match.start()], re.IGNORECASE):
            continue
        if not _FUTURE_MODAL_PATTERN.search(_clause_around(text, match.start())):
            return True
    return False


def _stated_role(text: str) -> str:
    """Whether the text reads as a forecast, a realised outcome, or both.

    A forecast marker alone is not the whole story: "the operator beat
    projections: 16 GW was installed in 2025" carries a forecast marker
    ("projections") *and* a past-tense, realised-outcome verb ("beat",
    "installed") — it reports what happened, not an open plan. "mixed" is
    the conservative reading for that case: neither a forecast nor an
    actual is safe to match it against, because a real one of either kind
    could be absorbed into a sentence that already settled the question the
    other one still has open.
    """
    forecast = _forecast_role(text)
    outcome = _realized_outcome(text)
    if forecast and outcome:
        return "mixed"
    return "forecast" if forecast else "actual"

def _find_restated_point(
    claim: Claim, rendered: Sequence[ReportPoint]
) -> ReportPoint | None:
    """The already-rendered point whose content already states this claim, if any."""
    # A unit-bearing figure only: a bare year is not a figure a fact rests on
    # — it is usually also the role's own period, so two claims about
    # unrelated facts that both happen to be dated "in 2025" would otherwise
    # match on the year alone.
    def unit_bearing(text: str) -> set[str]:
        return {
            token
            for token in _significant_figures(text)
            if token != _figure_number(token)
        }

    years = _text_role(claim.text)[0]
    figures = unit_bearing(claim.text)
    normalized = _normalized_text(claim.text)
    for point in rendered:
        if normalized == _normalized_text(point.text):
            return point
        if years == _text_role(point.text)[0] and figures & unit_bearing(point.text):
            return point
    return None


def _restatement_is_attachable(
    point: ReportPoint, claim: Claim, *, context: DraftContext
) -> bool:
    """True when the point may safely fold this claim's content into itself.

    A shared figure and year is necessary but not sufficient: two issuers
    can publish the same rounded figure for the same year on unrelated
    facts (WoodMac's own 13.3 GW forecast is not BNEF's), and one issuer's
    own forecast is not its own later actual even when the figure survived
    unchanged — whichever of the two already rendered. Checked the way a
    drafted point naming this claim would be: a name the claim's own corpus
    does not attest is refused (``_unsupported_names``), and a stated-role
    mismatch is refused via ``_stated_role`` — "forecast", "actual", or
    "mixed" when the text both names a forecast and reports a past,
    realised outcome ("beat projections ... was installed"), which is
    refused against *either* role since neither is safe to assume. This is
    normalised past ``hedge_marker``'s own narrow pattern so "carried the
    projection ... plans" and "planned" read as the same forecast role —
    deliberately not a call to ``dropped_modality`` itself, which reads the
    former as unhedged on that narrow pattern and would refuse the audited
    restatement this project exists to fix. A recorded issuer that
    disagrees is refused too.
    """
    if _unsupported_names(point.text, [claim], context):
        return False
    point_role = _stated_role(point.text)
    claim_role = _stated_role(claim.text)
    if point_role == "mixed" or claim_role == "mixed" or point_role != claim_role:
        return False
    claims_by_id = {c.claim_id: c for c in context.approved.values()}
    point_issuers = {
        claims_by_id[claim_id].provenance.attributed_issuer.casefold()
        for claim_id in point.claim_ids
        if claim_id in claims_by_id
        and claims_by_id[claim_id].provenance.attributed_issuer
    }
    claim_issuer = claim.provenance.attributed_issuer
    if (
        point_issuers
        and claim_issuer
        and claim_issuer.casefold() not in point_issuers
    ):
        return False
    return True


def _attach_claim_to_point(
    point: ReportPoint, claim: Claim, *, context: DraftContext
) -> ReportPoint | None:
    """Fold a restated bound claim into the point that already states it.

    The point's own words are kept — the claim only restates what the
    reader already sees — but its statement is rebuilt over the wider claim
    set, so the target this claim answers and the dimensions it covers are
    credited to the point instead of lost with the skipped claim. Its own
    urls travel too, narrowed to what the new claim actually carries: a
    point naming a claim that carries none of its cited urls is exactly the
    unresolved citation the quality gate hard-fails on, so a claim that
    shares none of the point's urls is refused here (``None``) rather than
    attached, and one that shares only some narrows the point down to the
    overlap. The statement keeps its own id: a repaired point must never
    take the id of a kept record.
    """
    point_urls = {normalize_source_url(url) for url in point.source_urls}
    claim_urls = {normalize_source_url(url) for url in claim.source_urls}
    if point_urls <= claim_urls:
        urls = list(point.source_urls)
    else:
        common = point_urls & claim_urls
        if not common:
            return None
        urls = [
            url for url in point.source_urls
            if normalize_source_url(url) in common
        ]
    claims_by_id = {c.claim_id: c for c in context.approved.values()}
    claim_ids = list(dict.fromkeys([*point.claim_ids, claim.claim_id]))
    claims = [
        claims_by_id[claim_id] for claim_id in claim_ids if claim_id in claims_by_id
    ]
    statement_id = (
        point.statement.statement_id if point.statement else context.next_id("S")
    )
    basis = (point.statement.basis if point.statement else "") or ""
    return point.model_copy(
        update={
            "claim_ids": claim_ids,
            "source_urls": urls,
            "statement": _statement_for_claims(
                statement_id=statement_id,
                text=point.text,
                claims=claims,
                context=context,
                basis=basis,
            ),
        }
    )


def _render_omitted_bound_claims(
    summary: Sequence[ReportPoint],
    sections: Sequence[ReportSection],
    *,
    context: DraftContext,
) -> tuple[list[ReportPoint], list[ReportSection]]:
    """Give a statement to every target-bound attributed claim the draft omitted.

    audit2's claim #14, EIA's own 15 GW 2025 actual, reached the reader only
    as an uncited line in the uncertainty list because no drafted point named
    it. A claim bound to a planned target, with primary-source or
    independent support and no contradiction, is stated in its own checked
    words and cited to the URLs it carries. Every guard a drafted point
    passes still applies. A claim whose content a rendered point (or an
    earlier one added here) already states is not given a point of its own:
    it is attached to the point that already states it instead, so the
    target it answers and the dimensions it covers are credited to that
    point rather than either lost with the claim or printed a second time.
    """
    summary = list(summary)
    sections = [
        section.model_copy(update={"points": list(section.points)})
        for section in sections
    ]
    if not context.targets:
        return summary, sections
    rendered_points = [
        *summary, *(p for section in sections for p in section.points)
    ]
    rendered = {
        claim_id for point in rendered_points for claim_id in point.claim_ids
    }
    points: list[ReportPoint] = []
    for label, claim in context.approved.items():
        if (
            claim.claim_id in rendered
            or claim.verdict == "contradicted"
            or claim.evidence_status not in ("source_supported", "verified_pair")
            or not set(claim.target_ids).intersection(context.targets)
        ):
            continue
        restated = _find_restated_point(claim, [*rendered_points, *points])
        attached = (
            _attach_claim_to_point(restated, claim, context=context)
            if restated is not None
            and _restatement_is_attachable(restated, claim, context=context)
            else None
        )
        if attached is not None:
            replaced_locally = False
            for index, pending in enumerate(points):
                if pending is restated:
                    points[index] = attached
                    replaced_locally = True
                    break
            if not replaced_locally:
                _replace_point(summary, sections, restated, attached)
            rendered_points = [
                attached if point is restated else point
                for point in rendered_points
            ]
            rendered.add(claim.claim_id)
            context.dispositions.append("omitted_bound_claim_attached")
            continue
        point = _build_point(
            text=claim.text,
            labels=[label],
            urls=claim.source_urls,
            context=context,
            where=f"omitted bound claim {label}",
        )
        if point is not None:
            points.append(point)
    if not points:
        return summary, sections
    context.dispositions.append("omitted_bound_claim_rendered")
    sections = [*sections, ReportSection(title=_OMITTED_BOUND_TITLE, points=points)]
    return summary, sections


def _replace_point(
    summary: list[ReportPoint],
    sections: list[ReportSection],
    old: ReportPoint,
    new: ReportPoint,
) -> None:
    """Swap one already-rendered point for its attached replacement, in place."""
    for index, point in enumerate(summary):
        if point is old:
            summary[index] = new
            return
    for section in sections:
        for index, point in enumerate(section.points):
            if point is old:
                section.points[index] = new
                return


def _build_points(
    drafts: Sequence[ReportPointDraft],
    *,
    context: DraftContext,
    where: str,
    seen: set[tuple[str, tuple[str, ...]]],
) -> list[ReportPoint]:
    """Validate a list of drafted points, refusing repeats of an earlier one.

    Repetition is refused by the *fact* a statement rests on, not by its
    wording: a summary restatement of one cluster and a detailed discussion of
    it are two statements of one fact, which is allowed but counted once,
    while a second near-identical statement of the same fact in the same
    section is the duplicate inflation this check removes.
    """
    points: list[ReportPoint] = []
    for position, item in enumerate(drafts, start=1):
        point = _build_point(
            text=item.text,
            labels=item.claim_ids,
            urls=item.source_urls,
            context=context,
            where=f"{where} point {position}",
            basis=item.basis,
        )
        if point is None:
            continue
        key = (
            " ".join(point.text.casefold().split()),
            tuple(point.claim_cluster_ids),
        )
        if key in seen:
            context.note(
                "duplicate_statement",
                f"{where} point {position}",
                "repeats an earlier statement",
            )
            continue
        seen.add(key)
        points.append(point)
    return points


def _build_constraints(
    drafts: Sequence[ConstraintDraft],
    *,
    context: DraftContext,
) -> list[ReportConstraint]:
    rows: list[ReportConstraint] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for position, item in enumerate(drafts, start=1):
        row = _build_constraint(
            item, context=context, where=f"constraint {position}"
        )
        if row is None:
            continue
        key = (
            " ".join(row.text.casefold().split()),
            tuple(row.claim_cluster_ids),
        )
        if key in seen:
            context.note(
                "duplicate_statement",
                f"constraint {position}",
                "repeats an earlier statement",
            )
            continue
        seen.add(key)
        rows.append(row)
    return rows


def _build_answer_rows(
    drafts: Sequence[AnswerRowDraft],
    *,
    context: DraftContext,
) -> list[ReportAnswerRow]:
    rows: list[ReportAnswerRow] = []
    for position, item in enumerate(drafts, start=1):
        row = _build_answer_row(
            item, context=context, where=f"answer row {position}"
        )
        if row is not None:
            rows.append(row)
    return rows


# The recorded proposition atom each *label* column of an answer-kind table is
# filled from when the draft supplied no rows at all. The column names are the
# renderer's own (``report.ANSWER_TABLE_COLUMNS``), and the order inside each
# entry is the order they are preferred in: what the clause asserts about, then
# what it measured, then the period it covers. Every one of them is something
# the extractor recorded when the claim was split, and never wording this pass
# composed to fill a cell. A column whose recorded atoms are all empty reads
# "not stated", exactly as a drafted cell the evidence does not carry does.
#
# ``population`` is *last* rather than beside ``quantity_noun``: it is the
# loosest read of the three — the atom splitter filled it with the "U" of
# "The U.S." on the traced pass — while the quantity noun and the observation
# period are the two dimensions the plan's own vocabulary asks an answer to
# name ("measure: …", "period: …"). Preferred order costs nothing when the
# earlier atom is recorded and keeps a fragment out of a reader's cell when it
# is not, which is why the period stands in for a comparison's basis of
# comparison only when the clause records no measurand at all.
_ANSWER_LABEL_ATOMS: dict[str, tuple[str, ...]] = {
    "subject": ("subject", "population", "quantity_noun"),
    "option": ("subject", "population", "quantity_noun"),
    "dimension": ("quantity_noun", "observation_period", "population"),
    "period": ("observation_period",),
}
# The shortest atom that can fill a cell. A one-character proposition field is
# the atom splitter's fragment rather than a label — "The U.S." was recorded as
# the population "U" — and a column that printed it would be publishing an
# artifact of extraction as the dimension of a fact.
_MIN_LABEL_CHARS = 2
# A period a sentence is *about* and does not state: "for the year of writing"
# names no year, so no year the same sentence mentions can be taken as its
# period. A determiner plus one of the period nouns, unless a year follows it
# ("the year 2024" states its own year and is left alone).
_UNNAMED_PERIOD = re.compile(
    r"\b(?:the|that|this|same|following|previous|current)\s+"
    r"(?:year|calendar year|period|date)\b(?!\s*,?\s*(?:19|20)\d{2})",
    re.IGNORECASE,
)
# A clause that states the baseline a figure is measured against rather than
# the figure itself. "up from 10.4 GW in 2024" answers what the finding rose
# from, so the year in it is not the year of the finding's own figure.
_COMPARISON_CLAUSE = re.compile(
    r"^\s*(?:up|down|rising|falling|increasing|decreasing)?\s*"
    r"(?:from|compared\s+(?:with|to)|versus|vs\.?)\b",
    re.IGNORECASE,
)
# What separates one *period* clause from the next: a sentence terminator, and
# a comma that is not inside a figure. Deliberately coarser than
# ``_CLAUSE_SPLIT``, which is written for the modality and scope checks: that
# one cuts a figure from its own unit ("10,400 MW") and from its own period
# ("10.4 GW (10,400 MW) of … in 2024"), which is the pair this test has to keep
# together, and it would split the traced summary's "one EIA figure for 2024:
# generators added 10.4 GW … in 2024" away from its own year.
_PERIOD_CLAUSE_SPLIT = re.compile(r"(?<=[.;!?])\s+|,(?!\d)\s*")
# A sentence boundary, so a year in an earlier sentence cannot date this one's
# figure.
_SENTENCE_SPLIT = re.compile(r"(?<=[.;!?])\s+")
# Only a year explicitly dated to a measurement can label it. A release date
# ("March 12, 2025") or a data vintage ("January 2025 Inventory") is not
# the year of the adjacent 10.4 GW addition.
_MEASURE_PERIOD = re.compile(
    r"\b(?:in|during|for|by|through|as\s+of)\s+"
    r"(?:calendar\s+year\s+)?((?:19|20)\d{2})\b",
    re.IGNORECASE,
)

def _stated_label(text: str, value: str) -> str:
    """Return the source's own spelling of a contiguous recorded label.

    Atom extraction discards punctuation, so ``U S energy storage market`` is
    not what a finding saying ``U.S. energy storage market`` states. Restore
    the span from the validated point instead of printing the atom splitter's
    normalized tokens or inventing a subject it does not mention at all.
    """
    words = re.findall(r"[A-Za-z0-9]+", value)
    if not words:
        return ""
    pattern = r"\b" + r"[\W_]*".join(map(re.escape, words)) + r"\b"
    match = re.search(pattern, text, re.IGNORECASE)
    if match is None:
        return ""
    stated = match.group(0)
    return value if stated.casefold() == value.casefold() else stated




def _selected_claims(
    point: ReportPoint,
    context: DraftContext,
) -> list[Claim]:
    """The checked claims a rendered point names, in registry order."""
    wanted = set(point.claim_ids)
    return [
        claim for claim in context.approved.values() if claim.claim_id in wanted
    ]

def _answers_planned_target(point: ReportPoint, context: DraftContext) -> bool:
    """A measured answer must cite a checked claim bound to this plan."""
    if not context.targets:
        return True
    return any(
        set(claim.target_ids).intersection(context.targets)
        for claim in _selected_claims(point, context)
    )


def _answering_statement(point: ReportPoint) -> ReportStatement | None:
    """The statement this point answers with, or ``None`` when it answers none.

    Two conditions, both about what the point already carries. The mode has to
    be one that answers — a recorded disagreement that settles nothing is not
    an answer, and neither is this pass's own framing. And the text has to
    state a figure with a unit: the plan asks every factual answer for "the
    specific fact asked for, with its value, unit, and the date the value
    applies to", so a value with a unit is what makes a validated statement an
    answer rather than true prose about the pass ("no energy-capacity figure
    appears among the checked claims"). A factual answer that is not a
    measurement at all — a name, a date, a yes or no — keeps the honest empty
    table rather than publishing a row the form did not ask for.
    """
    statement = point.statement
    if statement is None or point.mode not in ANSWERING_STATEMENT_MODES:
        return None
    measured = any(
        token != _figure_number(token)
        for token in _significant_figures(point.text)
    )
    return statement if measured else None


def _recorded_label_atom(
    point: ReportPoint,
    atoms: Sequence[str],
    context: DraftContext,
) -> str:
    """The first atom one of a point's cited clusters records, or ``""``.

    Read from the clusters the statement cites, not from its prose: those are
    the qualifiers the extractor recorded for the claim the reader statement
    was validated against, so a label taken from them states what the cited
    evidence itself carries.

    A recorded *period* is the case the traced 18.2 GW row got wrong: its
    cluster recorded "2024" from a sentence whose own year is the one it does
    not name ("for the year of writing") and whose only stated year is the
    count it rose *from*. So the period atom is accepted only when the finding
    states that year as the one it is about (``_states_the_period``); otherwise
    the column falls through to the next atom, and to "not stated" if none is
    left.
    """
    for cluster_id in point.claim_cluster_ids:
        cluster = context.clusters.get(cluster_id)
        if cluster is None:
            continue
        proposition = cluster.proposition
        for atom in atoms:
            value = " ".join(getattr(proposition, atom, "").split())
            if len(value) < _MIN_LABEL_CHARS:
                continue
            if atom == "observation_period":
                if not _states_the_period(point.text, value):
                    continue
                return value
            stated = _stated_label(point.text, value)
            if stated:
                return stated
    return ""


def _period_span(text: str) -> str:
    """The span of ``text`` whose years may date the finding's own figure.

    From the start of the sentence that carries the finding's figure through
    the end of the clause that carries it, with every comparison clause left
    out. Two shapes decide the boundaries. A year *after* that clause dates
    something else in the sentence ("projected 19.6 GW for 2025, after adding
    10.4 GW in 2024"), and a year inside a comparison clause states what the
    figure rose from ("up from 10.4 GW in 2024") — neither is this fact's
    period. A period fronted before the figure ("For 2025 the EIA projected …
    19.6 GW") is inside the span, which is what lets it label the row.

    The split is the period-clause one (``_PERIOD_CLAUSE_SPLIT``) rather than
    the grammatical one the modality checks use: the year that dates a figure
    is separated from it by no more than a comma outside the figure itself,
    and ``_CLAUSE_SPLIT`` would cut the traced "one EIA figure for 2024:
    generators added 10.4 GW … in 2024" away from its own year.
    """
    for sentence in _SENTENCE_SPLIT.split(text):
        span: list[str] = []
        for clause in _PERIOD_CLAUSE_SPLIT.split(sentence):
            if _COMPARISON_CLAUSE.match(clause):
                continue
            span.append(clause)
            if any(
                token != _figure_number(token)
                for token in _significant_figures(clause)
            ):
                return " ".join(span)
    return ""


def _states_the_period(text: str, period: str) -> bool:
    """Only date a measured figure with the period the statement assigns it.

    A year in a release date or inventory vintage may precede the figure, but
    ``10.4 GW ... in 2024`` names its own period after the figure. Prefer that
    explicit measurement-year attachment; otherwise admit a fronted ``For
    2025 ... 19.6 GW``. Bare year mentions (notably a release date) are never
    measurement periods.
    """
    years = set(_YEAR.findall(period))
    if not years or _UNNAMED_PERIOD.search(text):
        return False
    span = _period_span(text)
    if not span:
        return False
    figures = [
        figure for figure in _significant_figures(span)
        if figure != _figure_number(figure)
    ]
    if not figures:
        return False
    figure_at = span.casefold().find(figures[0].casefold())
    stated = list(_MEASURE_PERIOD.finditer(span))
    after = [match.group(1) for match in stated if match.start() > figure_at]
    before = [match.group(1) for match in stated if match.start() < figure_at]
    year = after[0] if after else (before[-1] if before else None)
    return year is not None and years == {year}


def _derived_label(
    *,
    point: ReportPoint,
    column: str,
    claims: Sequence[Claim],
    context: DraftContext,
) -> ReportStatement:
    """One derived label cell: a recorded atom, or the contract's own sentinel.

    The repair branch is the drafted path's own repair — ``_build_cell``
    returns the same text, mode and basis for a cell its row's evidence does
    not state — because a cell this pass cannot source is "not stated" rather
    than something composed to fill the column.
    """
    atom = _recorded_label_atom(
        point, _ANSWER_LABEL_ATOMS.get(column, ()), context
    )
    if not atom:
        return ReportStatement(
            statement_id=context.next_id("C"),
            text="not stated",
            mode="context",
            basis="the row's evidence does not state this cell",
        )
    return _statement_for_claims(
        statement_id=context.next_id("C"),
        text=_display_clamp(atom, limit=_CELL_CHARS),
        claims=claims,
        context=context,
        basis="cell carried by the row's evidence",
        # The drafted path's own reading of a cell with no selected claim
        # behind it (``_build_cell``): the wording is published as framing
        # rather than as an assertion with an evidence link it does not have.
        mode="attributed" if claims else "context",
    )


def _derive_answer_rows(
    *,
    answer_kind: str | None,
    summary: Sequence[ReportPoint],
    sections: Sequence[ReportSection],
    context: DraftContext,
) -> list[ReportAnswerRow]:
    """The answer-kind rows of a pass whose draft supplied none.

    ``answer_rows`` is optional in the request schema, and the draft that
    counts is the *retry*: the 08b9b469 pass answered both halves of its
    question, published "(no factual row was drafted for this pass)", and
    recorded no refusal, because the retry after a truncated call came back
    without the field. Asking again is not the fix — the report call is
    already at its output limit — and nothing has to be asked: the composition
    holds the statements the pass settled, so the rows are derived from those.

    A row is derived only for a point that answers with a figure
    (``_answering_statement``), and only for the answer forms whose second
    section is a table of facts. The summary is read first: it is the pass's
    own answer-first selection and the findings restate it, so the findings
    stand in only when the summary answers nothing and one fact never becomes
    two rows. Every label comes from the cited claims' recorded propositions
    and the finding is the statement the pass already validated, so a derived
    row cites exactly the sources its statement does.

    Rows prefer the pass's own scoped summary: by the time this runs,
    ``_scope_answer_summary`` has already kept every summary point that is
    either bound to a planned target or the sole answer for its role (its own
    period, forecast or outcome), so every one of them earns a row — an
    unbound sole forecast answers half the question exactly as a bound
    outcome answers the other half, and filtering the summary down to only
    the bound half here dropped its row from Key facts. When the summary
    answers nothing, binding failed for the whole pass and the findings stand
    in, bound points preferred.
    """
    columns = ANSWER_TABLE_COLUMNS.get(answer_kind or "")
    if not columns:
        return []
    def measured(points: Sequence[ReportPoint]) -> list[tuple[ReportPoint, ReportStatement]]:
        return [
            (point, statement)
            for point in points
            if (statement := _answering_statement(point)) is not None
        ]

    def bound(
        pairs: list[tuple[ReportPoint, ReportStatement]],
    ) -> list[tuple[ReportPoint, ReportStatement]]:
        return [pair for pair in pairs if _answers_planned_target(pair[0], context)]

    in_summary = measured(summary)
    in_findings = measured([p for section in sections for p in section.points])
    answering = in_summary or bound(in_findings) or in_findings
    rows: list[ReportAnswerRow] = []
    for point, statement in answering:
        claims = _selected_claims(point, context)
        cells = [
            _derived_label(
                point=point,
                column=column.casefold(),
                claims=claims,
                context=context,
            )
            for column in columns
        ]
        cells.append(statement)
        rows.append(ReportAnswerRow(cells=cells))
    if rows:
        context.dispositions.append("answer_rows_derived")
    return rows


def compose_report(
    task: SynthesisTask,
    *,
    draft: ReportDraft | None,
    limitations: Sequence[str],
    max_sections: int = DEFAULT_MAX_SECTIONS,
    quality_status: str = QUALITY_STATUS_NOT_GATED,
) -> tuple[SynthesizedReport, list[ResearchError]]:
    """Compose both artifacts and record the counts observability needs.

    Writes nothing: the reader report and its ledger are returned as strings,
    and their filenames are composed so the terminal finalizer never has to
    re-derive which pass they belong to.
    """
    composition, rejected = build_report_composition(
        task,
        draft,
        max_sections=max_sections,
        limitations=limitations,
        quality_status=quality_status,
    )
    report = SynthesizedReport(
        markdown=render_reader_report(composition),
        evidence_markdown=render_evidence_ledger(composition),
        evidence_path=evidence_report_filename(
            session_id=task.session_id, iteration=task.iteration
        ),
        composition=composition,
        section_count=len(composition.sections),
        citation_count=len(reader_citations(composition)),
        unique_source_count=len(composition.sources),
        unique_claim_count=len(composition.claims),
    )
    errors = [invalid_draft_error(rejected, draft=draft)] if rejected else []
    return report, errors


def report_messages(
    task: SynthesisTask,
    *,
    finding_digest: int,
    claim_digest: int,
) -> list[ChatMessage]:
    """Build the messages that request one structured report draft.

    The evidence packet is one canonical, addressable block: every checked
    claim it carries is labelled by the same registry position a drafted
    point's ``claim_ids`` resolve against (``claim_registry`` /
    ``DraftContext.approved``), ranked by coverage, verdict, and recorded
    impact for *display order* only — the label itself never moves with
    that order. Showing the same claim under two different labels in two
    blocks let a draft cite the richer block's label and resolve to a
    different claim than the one it read; one label space closes that. Raw
    findings are carried as open questions only: they are leads, and the
    response contract forbids resting a settled statement on one.
    """
    registry = claim_registry(task.claims)
    packet, omitted = bounded_claim_packet(
        registry,
        limit=claim_digest,
        budget_chars=SYNTHESIS_CLAIM_PACKET_CHARS,
    )
    # Carry the exact prompt-visible registry into composition.  The complete
    # canonical snapshot remains on ``task.claims`` for the evidence ledger.
    task.claim_packet = packet
    open_questions = bounded_finding_digest(
        task.findings,
        limit=finding_digest,
        budget_chars=SYNTHESIS_OPEN_QUESTIONS_CHARS,
    )
    label_by_claim_id = {claim.claim_id: label for label, claim in packet}
    canonical = build_canonical_packet(
        claims=[claim for _, claim in packet],
        clusters=task.claim_clusters,
        evidence=task.evidence_units,
        targets=task.targets,
        sources=task.sources,
        limit=claim_digest,
        failures=measured_failures(task),
        labels=label_by_claim_id,
    )
    canonical_body = render_canonical_packet(canonical)
    if omitted:
        canonical_body = (
            f"{canonical_body}\n({omitted} further checked claim(s) were "
            "omitted for length; they cannot be cited by this draft.)"
        )
    sections = [f"# Research question\n{task.instruction}"]
    if task.guidance.strip():
        sections.append(f"# Context\n{task.guidance}")
    sections.extend(
        [
            (
                "# As of and scope\n"
                f"As of: {task.as_of.strip() or 'no dated evidence recorded'}\n"
                f"Scope: {task.scope.strip() or 'not stated'}"
            ),
            f"# Answer form\n{answer_form_instruction(task.answer_contract)}",
            f"# Canonical evidence packet\n{canonical_body}",
            (
                "# Retrieved findings (open questions only)\n"
                f"{open_questions}"
            ),
            f"# Source quality\n{render_source_quality(task.sources)}",
            f"# Known limitations\n{render_limitations(task.limitations)}",
            f"# Response contract\n{REPORT_INSTRUCTION}",
            (
                "# Reply format\n"
                f"{render_structured_reply_format(_REPORT_REPLY_EXAMPLES)}"
            ),
        ]
    )
    return [
        ChatMessage(role="developer", content=SYNTHESIZER_SYSTEM_PROMPT),
        ChatMessage(role="user", content="\n\n".join(sections)),
    ]


def report_provider_error(error: Exception) -> ResearchError:
    """Record that the report call could not reach the provider.

    Non-recoverable, mirroring ``researcher.extraction_provider_error``: no
    prose exists for this pass. Both artifacts are still composed — they are
    rendered from recorded evidence — so this is a quality failure, not a lost
    record.
    """
    return agent_error(
        agent_name=SYNTHESIZER_NAME,
        error_type="synthesizer_report_provider_error",
        message=(
            "The model provider failed while the report was written; the "
            "artifacts were assembled from recorded evidence alone."
        ),
        recoverable=False,
        details=agent_provider_failure_details(
            "synthesizer_report_draft", error
        ),
    )


def report_output_limit_retry(
    error: Exception,
    *,
    reasoning_effort: str,
    outcome: str,
) -> ResearchError:
    """Record that a truncated report call was re-asked at another effort.

    The retry is a second paid call, so it is recorded even when it works.
    Recoverable either way: a retry that answers produced the prose, and one
    that truncates again leaves this call's own non-recoverable record beside
    this note of what was tried first.

    The message names the call, the effort and the outcome, and deliberately
    names no token figure: this call sends no per-operation output budget, so
    the budget it keeps is the provider's global cap — a number this agent does
    not own and must not invent.
    """
    if outcome not in OUTPUT_LIMIT_RETRY_OUTCOMES:
        raise ValueError(f"unknown retry outcome: {outcome!r}")
    return agent_error(
        agent_name=SYNTHESIZER_NAME,
        error_type="synthesizer_report_output_limit_retry",
        message=(
            "The synthesizer_report_draft call was truncated by the output "
            f"limit; it was re-asked once at reasoning_effort {reasoning_effort} "
            "under the same output budget (the provider's global cap), and "
            f"{OUTPUT_LIMIT_RETRY_READINGS[outcome]}"
        ),
        recoverable=True,
        details=agent_provider_failure_details(
            "synthesizer_report_draft",
            error,
            attempt=2,
            reasoning_effort=reasoning_effort,
            outcome=outcome,
        ),
    )


def invalid_draft_error(
    rejected: Sequence[str], *, draft: ReportDraft | None = None
) -> ResearchError:
    """Warn that some drafted report content was refused.

    ``draft`` is the model's own raw reply, when the caller has one: every
    point it proposed, whether it survived validation or not, with its full
    text, claim labels and source urls exactly as drafted and never
    truncated. The reasons alone name what went wrong; without the draft
    beside them, a later diagnosis cannot tell which drafted point tripped
    which reason — audit-3's own retained trace kept only token counts, not
    messages, and the label collision it hid took a live reproduction to
    find instead of a read of this record.
    """
    details: dict[str, JsonValue] = {"rejected": list(rejected)}
    if draft is not None:
        details["drafted_points"] = _drafted_point_records(draft)
    return agent_error(
        agent_name=SYNTHESIZER_NAME,
        error_type="synthesizer_invalid_draft",
        message=(
            "Some drafted report content was malformed, or rested on a claim "
            "or source that is not in the checked evidence; it is listed in "
            "the evidence ledger instead of the reader report."
        ),
        details=details,
    )


def _drafted_point_records(draft: ReportDraft) -> list[dict[str, JsonValue]]:
    """Every point the model drafted, its full text and citations, unclamped.

    One record per claim-linked item the schema carries — the executive
    summary, each findings section, the ranked constraints, and the answer
    rows — in the shape ``_build_point`` validates, before any of it was
    narrowed, repaired, or refused. Diagnostic only: never fed back into a
    prompt, so nothing here is length-bounded the way a routed assertion is.
    """
    records: list[dict[str, JsonValue]] = []

    def add(
        where: str, text: str, claim_ids: Sequence[str], urls: Sequence[str]
    ) -> None:
        records.append(
            {
                "where": where,
                "text": text,
                "claim_ids": list(claim_ids),
                "source_urls": list(urls),
            }
        )

    for position, point in enumerate(draft.executive_summary, start=1):
        add(
            f"executive summary point {position}",
            point.text,
            point.claim_ids,
            point.source_urls,
        )
    for section_position, section in enumerate(draft.sections, start=1):
        for position, point in enumerate(section.points, start=1):
            add(
                f"section {section_position} point {position}",
                point.text,
                point.claim_ids,
                point.source_urls,
            )
    for position, constraint in enumerate(draft.ranked_constraints, start=1):
        add(
            f"constraint {position}",
            constraint.constraint,
            constraint.claim_ids,
            constraint.source_urls,
        )
    for position, row in enumerate(draft.answer_rows, start=1):
        add(f"answer row {position}", row.finding, row.claim_ids, row.source_urls)
    return records


def no_evidence_error() -> ResearchError:
    """Warn that the report was assembled over no evidence at all."""
    return agent_error(
        agent_name=SYNTHESIZER_NAME,
        error_type="synthesizer_no_evidence",
        message=(
            "No claim, source, or finding was available; the report states "
            "its own limitations and nothing else."
        ),
    )


def synthesis_started_event(
    *,
    claim_count: int,
    source_count: int,
    finding_count: int,
    limitation_count: int,
) -> ResearchEvent:
    """Announce that synthesis began, before any provider call."""
    return agent_event(
        agent_name=SYNTHESIZER_NAME,
        event_type="synthesizer.synthesis.started",
        message="Report synthesis started.",
        metadata={
            "claim_count": claim_count,
            "source_count": source_count,
            "finding_count": finding_count,
            "limitation_count": limitation_count,
        },
    )


def synthesis_completed_event(
    report: SynthesizedReport,
    *,
    limitations: Sequence[str],
    claim_count: int,
) -> ResearchEvent:
    """Report the counts this agent composed, and where it composed them.

    No ``output_path``: synthesis writes nothing, so an event that named a
    file here would advertise an artifact that does not exist. ``evidence_path``
    is the *name* the terminal finalizer will publish the ledger under.

    ``limitations`` carries enumerated ``LIMITATION_REASONS`` keys, never
    prose, so a consumer can group on them.
    """
    return agent_event(
        agent_name=SYNTHESIZER_NAME,
        event_type="synthesizer.synthesis.completed",
        message="Report artifacts composed.",
        metadata={
            "section_count": report.section_count,
            "citation_count": report.citation_count,
            "unique_source_count": report.unique_source_count,
            "unique_claim_count": report.unique_claim_count,
            "evidence_path": report.evidence_path,
            "report_chars": len(report.markdown),
            "evidence_chars": len(report.evidence_markdown),
            "claim_count": claim_count,
            "limitations": list(limitations),
        },
    )


class SynthesizerAgent(BaseAgent[SynthesizedReport]):
    """Compose the reader report and its evidence ledger.

    Runs no ReAct loop: the report is one structured call, and everything
    structural is rendered locally. ``run`` is overridden for the same reason
    ``SourceEvaluatorAgent`` overrides it — the shared single-loop
    ``BaseAgent.run`` cannot express this shape.

    ``allowed_tools`` still declares the two persistence tools because the
    terminal finalizer publishes both artifacts and owns long-term memory
    through exactly these tools; this agent's own run calls neither.
    """

    name = SYNTHESIZER_NAME
    description = "Write the final report from verified claims and sources."
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
        max_sections: int = DEFAULT_MAX_SECTIONS,
        finding_digest: int = SYNTHESIS_FINDING_DIGEST,
        claim_digest: int = SYNTHESIS_CLAIM_DIGEST,
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
                "SynthesizerAgent clock must return a timezone-aware "
                "datetime; got a naive datetime instead"
            )
        self._clock = clock
        if max_sections < 1:
            raise ValueError("max_sections must be at least 1")
        if finding_digest < 1:
            raise ValueError("finding_digest must be at least 1")
        if claim_digest < 1:
            raise ValueError("claim_digest must be at least 1")
        self._max_sections = max_sections
        self._finding_digest = finding_digest
        self._claim_digest = claim_digest

    @property
    def output_schema(self) -> type[SynthesizedReport]:
        """The composed artifacts. Never sent to the provider.

        ``draft_report`` asks for ``ReportDraft`` instead, because the report
        record carries constraints that do not survive strict JSON schema
        conversion. Do not route this agent through ``complete_output``.
        """
        return SynthesizedReport

    def system_prompt(self, task: AgentTask) -> str:
        del task
        return SYNTHESIZER_SYSTEM_PROMPT

    def build_task(self, state: ResearchState) -> SynthesisTask:
        """Bind this run to the evidence and limitations already recorded."""
        return SynthesisTask(
            instruction=state.original_question,
            guidance=render_revision_guidance(state),
            session_id=state.session_id,
            iteration=state.iteration,
            max_iterations=state.max_iterations,
            as_of=report_as_of(
                findings=state.raw_findings,
                reads=list(state.read_records.values()),
            ),
            generated_on=self._clock().date().isoformat(),
            scope=report_scope(state.sub_topics),
            sub_topics=list(state.sub_topics),
            claims=list(state.verified_claims),
            sources=list(state.evaluated_sources),
            findings=list(state.raw_findings),
            limitations=limitation_reasons(state),
            errors=list(state.errors),
            answer_contract=state.answer_contract,
            evidence_units=dict(state.evidence_units),
            claim_clusters=dict(state.claim_clusters),
            evidence_dispositions=list(state.evidence_dispositions),
            acquisition_state_by_target=dict(
                state.acquisition_state_by_target
            ),
        )

    async def draft_report(
        self,
        task: SynthesisTask,
    ) -> tuple[ReportDraft | None, list[ResearchError], bool]:
        """Ask the model for the report's prose.

        Makes no provider call when there is no evidence at all, so the
        writing step can never invent a report out of nothing. The third
        element is ``True`` only when the call itself failed.

        Opened at ``reasoning_effort="high"``, not the provider's default: a
        live pass measured both of its default-effort draft attempts
        truncated at the output limit (27,301 and then 32,768 of 32,768
        tokens spent on reasoning before a point was written), and only the
        "high" retry that followed ever produced a draft. The retry ladder
        stays — a truncated first attempt is still re-asked once, under the
        same output budget, the provider's global cap, since this call sends
        no per-operation budget of its own — and it has no fallback beyond
        that: without a synthesis there is no truthful report, so a second
        truncation keeps this call's own failure path rather than degrading.
        A provider outage is not re-asked at all.
        """
        if not task.claims and not task.sources and not task.findings:
            return None, [no_evidence_error()], False
        messages = report_messages(
            task,
            finding_digest=self._finding_digest,
            claim_digest=self._claim_digest,
        )
        truncations: list[ProviderOutputLimitError] = []
        for position, effort in enumerate(_DRAFT_ATTEMPT_EFFORTS):
            last_attempt = position + 1 == len(_DRAFT_ATTEMPT_EFFORTS)
            try:
                draft = await self.provider.complete_structured(
                    messages,
                    ReportDraft,
                    agent_name=self.name,
                    reasoning_effort=effort,
                )
            except ProviderOutputLimitError as error:
                truncations.append(error)
                if last_attempt:
                    return (
                        None,
                        [
                            *self._retry_records(truncations, "truncated"),
                            report_provider_error(error),
                        ],
                        True,
                    )
                continue
            except ProviderError as error:
                # An outage is not an answer: the retry is recorded as failed,
                # and this call's own failure path is unchanged.
                return (
                    None,
                    [
                        *self._retry_records(truncations, "failed"),
                        report_provider_error(error),
                    ],
                    True,
                )
            return draft, self._retry_records(truncations, "answered"), False
        raise AssertionError("a report attempt must produce a draft or a route")

    def _retry_records(
        self,
        truncations: Sequence[ProviderOutputLimitError],
        outcome: str,
    ) -> list[ResearchError]:
        """The record of this call's one retry, or nothing.

        Empty when the first attempt was not truncated, so an ordinary pass
        adds nothing to the run's records.
        """
        if not truncations:
            return []
        return [
            report_output_limit_retry(
                truncations[0],
                reasoning_effort=OUTPUT_LIMIT_RETRY_EFFORT,
                outcome=outcome,
            )
        ]

    def compose(
        self,
        task: SynthesisTask,
        draft: ReportDraft | None,
        *,
        limitations: Sequence[str] | None = None,
        provider_failed: bool = False,
    ) -> tuple[SynthesizedReport, list[ResearchError]]:
        """Compose both artifacts, naming anything that was refused."""
        return compose_report(
            task,
            draft=draft,
            limitations=(
                list(limitations)
                if limitations is not None
                else compose_limitations(task, provider_failed=provider_failed)
            ),
            max_sections=self._max_sections,
        )

    async def finalize(
        self,
        task: AgentTask,
        run: ReActRun,
    ) -> SynthesizedReport | None:
        """Adapt drafting and composition to the ``BaseAgent`` hook.

        ``run`` calls the pieces directly so it can keep the errors this hook
        signature has nowhere to return.
        """
        del run
        if not isinstance(task, SynthesisTask):
            raise AgentConfigurationError(
                "SynthesizerAgent.finalize requires a SynthesisTask"
            )
        draft, _, provider_failed = await self.draft_report(task)
        report, _ = self.compose(task, draft, provider_failed=provider_failed)
        return report

    def state_update(
        self,
        result: SynthesizedReport | None,
        run: ReActRun,
    ) -> ResearchStateUpdate:
        """Both artifacts and their counts. ``run`` adds the progress events.

        No artifact is written and no memory entry is saved: the terminal
        finalizer publishes, and it is the only writer. ``composition``
        travels with them so the graph can judge the exact points this pass
        composed.

        ``state.evidence_path`` is deliberately not stamped. The name this
        pass composed is a *future* filename, not a write, and
        ``ResearchState.evidence_path`` means "the ledger the terminal
        finalizer actually published". Stamping it here made
        ``evidence_path_from_state`` fall back to a file that does not exist
        on any run that halts after this node, and ``cli.render_summary``
        print it. The name still travels on the composed report and on the
        synthesis event, which is where a composed name belongs.
        """
        update: ResearchStateUpdate = {"errors": list(run.errors)}
        if result is not None:
            update["report"] = result.markdown
            update["report_evidence"] = result.evidence_markdown
            update["composition"] = result.composition
            update["unique_source_count"] = result.unique_source_count
            update["unique_claim_count"] = result.unique_claim_count
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

    async def publish_claim(
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

    async def run(self, state: ResearchState) -> AgentRun[SynthesizedReport]:
        """Draft and compose both artifacts, recording every count.

        No ReAct loop runs, so the returned ``ReActRun`` is synthetic with
        zero iterations and zero tool calls. ``stop_reason`` is
        ``"provider_error"`` only when the report call itself failed, so a
        caller reading ``react.succeeded`` learns the same thing it would
        from any other agent.
        """
        task = self.build_task(state)
        events: list[ResearchEvent] = [
            synthesis_started_event(
                claim_count=len(task.claims),
                source_count=len(task.sources),
                finding_count=len(task.findings),
                limitation_count=len(task.limitations),
            )
        ]
        errors: list[ResearchError] = []

        async with self.tracker.agent_span(self.name) as span:
            draft, draft_errors, provider_failed = await self.draft_report(task)
            errors.extend(draft_errors)
            # Computed once and passed to both consumers: the artifacts and the
            # completion event must disclose the same list, and two copies of
            # the rule are the same duplicated-rule shape this branch treats as
            # a defect elsewhere — here it would land in a user-visible artifact
            # and in telemetry at the same time.
            limitations = compose_limitations(
                task, provider_failed=provider_failed
            )
            report, compose_errors = self.compose(
                task,
                draft,
                limitations=limitations,
                provider_failed=provider_failed,
            )
            errors.extend(compose_errors)
            events.append(
                synthesis_completed_event(
                    report,
                    limitations=limitations,
                    claim_count=len(task.claims),
                )
            )
            span.set_outputs(
                {
                    "agent_name": self.name,
                    "section_count": report.section_count,
                    "citation_count": report.citation_count,
                    "unique_source_count": report.unique_source_count,
                    "unique_claim_count": report.unique_claim_count,
                    "evidence_path": report.evidence_path,
                    "provider_failed": provider_failed,
                }
            )

        react = ReActRun(
            agent_name=self.name,
            stop_reason="provider_error" if provider_failed else "finished",
            errors=errors,
        )
        return AgentRun(
            agent_name=self.name,
            result=report,
            react=react,
            errors=errors,
            state_update={
                **self.state_update(report, react),
                "events": events,
            },
            call_fingerprints=dict(self._call_fingerprints),
        )
