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

import re
from collections.abc import Mapping, Sequence

from pydantic import Field, JsonValue

from deep_research.agents.base import (
    OUTPUT_LIMIT_ATTEMPT_EFFORTS,
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
    QUALITY_STATUS_NOT_GATED,
    ReportComposition,
    ReportConstraint,
    ReportPoint,
    ReportSection,
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
# fits by count instead. `summarize_text` marks every cut it makes.
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
    r"[,;]|[:!?](?=\s|$)|\.(?=\s|$)"
    r'|[\u2014\u2013()\[\]\u201c\u201d"|/]'
    r"|\b(?:and|but|while|which|so|thus|therefore)\b"
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
_SCOPE_SUBJECTS = (
    "behind-the-meter",
    "behind the meter",
    "front-of-meter",
    "front of meter",
    "reported totals",
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
    dates: list[str] = Field(default_factory=list)
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


def _packet_dates(
    urls: Sequence[str],
    sources: Mapping[str, ScoredSource],
) -> list[str]:
    dates: list[str] = []
    for url in urls:
        source = sources.get(url)
        if source is None:
            continue
        for label, value in (
            ("publication", source.temporal.publication_date),
            ("data_period", source.temporal.data_period),
            ("forecast", source.temporal.forecast_horizon),
            ("effective", source.temporal.effective_date),
        ):
            if value:
                dates.append(f"{label}={value}")
        if dates:
            break
    return dates


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
    """
    if limit < 1:
        raise ValueError("limit must be at least 1")
    target_index = {target.target_id: target for target in targets}
    source_index = {normalize_source_url(source.url): source for source in sources}
    canonical = canonical_claims(claims)
    ranked = _packet_rank(canonical, targets=target_index)
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
        resolved_verdict, resolved_status = (
            resolved_verdict_and_status(cluster)
            if cluster is not None
            else (claim.verdict, claim.evidence_status)
        )
        support = list(
            dict.fromkeys(
                [
                    *(
                        cluster.verdict_evidence.get(resolved_verdict, [])
                        if cluster is not None
                        else []
                    ),
                    *claim.source_urls,
                ]
            )
        )
        urls: list[str] = []
        for raw in (*claim.source_urls, *support):
            url = normalize_source_url(raw)
            if url and url not in urls:
                urls.append(url)
        for evidence_id in selected:
            unit = evidence.get(evidence_id)
            if unit is None:
                continue
            url = normalize_source_url(unit.source_url)
            if url and url not in urls:
                urls.append(url)
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
                label=claim_label(position),
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
                dates=_packet_dates(urls, source_index),
                target_ids=target_ids,
                required_dimensions=required,
                answered_dimensions=list(claim.consumed_coverage_ids),
                obligations=obligations,
                measured_failures=list(failures),
            )
        )
    omitted = [
        claim_label(position)
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
        if entry.dates:
            lines.append(f"  dates: {', '.join(entry.dates)}")
        if entry.target_ids:
            lines.append(
                f"  targets: {', '.join(entry.target_ids)}; "
                f"dimensions: {', '.join(entry.required_dimensions) or 'none'}"
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
    note_corpus: str = ""
    """The figures a *question-shaped* note may name.

    Wider than the evidence corpus on purpose: a note about the question's own
    horizon, period, or scope is stating a fact this run recorded — in the
    frozen contract, the plan's sub-topics, or a source's recorded dates — and
    removing those figures would make the note say something else.
    """
    failures: list[str] = Field(default_factory=list)
    rejected: list[str] = Field(default_factory=list)
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


def _attestation_corpus(
    approved: Mapping[str, Claim],
    evidence: Mapping[str, EvidenceUnit],
) -> str:
    """The text a drafted statement's specific atoms must appear in.

    The checked claims themselves are included: a statement is allowed to
    restate its claim's wording, and a figure that is in the claim is in the
    evidence the claim was checked against. Nothing else is added — a corpus
    padded with a model's own prose would attest itself.
    """
    parts = [claim.text for claim in approved.values()]
    parts.extend(unit.excerpt for unit in evidence.values())
    return " ".join(parts).casefold()


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


def unattested_atoms(text: str, corpus: str) -> list[str]:
    """Specific factual atoms the corpus does not carry.

    Figures and names are what a paraphrase does not invent and a fabrication
    does. An unattested ordinary word is prose; an unattested figure or proper
    noun is a new fact, and this is the deterministic half of the support
    review — the half that cannot be argued with.
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
        # no ordinary sentence opener is all-caps.
        if _SENTENCE_INITIAL.search(text[: match.start()]) and not (
            _ACRONYM_PATTERN.fullmatch(token)
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
        if folded not in tokens and token not in found:
            found.append(token)
    return found


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
    for clause in _CLAUSE_SPLIT.split(lowered):
        subject = next(
            (name for name in _SCOPE_SUBJECTS if name in clause), ""
        )
        if not subject:
            continue
        if not any(verb in clause for verb in _SCOPE_VERBS):
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
        lines.append(f'{evidence_id} {unit.locator} "{excerpt}"')
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


def _cited_evidence(claims: Sequence[Claim], context: DraftContext) -> str:
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
    return " ".join(parts).casefold()


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
        context.note("unlinked_statement", where, "blank statement")
        return None
    claims, unknown = _resolve_claims(labels, approved=context.approved)
    if not claims:
        context.note("unlinked_statement", where, "no known checked claim")
        return None
    if unknown:
        context.rejected.append(
            f"{where}: {unknown} claim id(s) outside the registry"
        )
    approved_urls = {
        normalize_source_url(url)
        for claim in claims
        for url in claim.source_urls
    }
    accepted: list[str] = []
    invented = 0
    for raw in urls:
        url = normalize_source_url(raw)
        if url not in approved_urls:
            invented += 1
            continue
        if url not in accepted:
            accepted.append(url)
    if invented:
        context.rejected.append(
            f"{where}: {invented} source url(s) not on those claims"
        )
        return None
    if not accepted:
        context.note(
            "unlinked_statement", where, "no source url for a settled statement"
        )
        return None
    if not decision_section and _is_recommendation(text):
        context.note(
            "unsupported_recommendation",
            where,
            "a recommendation outside the answer",
        )
        context.returned.append(summarize_text(text, limit=_CLAIM_TEXT_CHARS))
        return None
    lowered = dropped_modality(text, claims)
    if lowered:
        context.note(
            "unsupported_modality",
            where,
            "the statement drops the modality its evidence carries",
        )
        context.returned.append(summarize_text(text, limit=_CLAIM_TEXT_CHARS))
        return None
    cited = _cited_evidence(claims, context) or context.corpus
    hardened = hardened_modality(text, cited)
    if hardened:
        context.note(
            "unsupported_modality",
            where,
            "the statement hardens the modality its evidence carries",
        )
        context.returned.append(summarize_text(text, limit=_CLAIM_TEXT_CHARS))
        return None
    qualifiers = unattached_qualifiers(text, cited)
    if qualifiers:
        context.note(
            "unsupported_qualifier",
            where,
            "an unsupported figure qualification",
        )
        context.returned.append(summarize_text(text, limit=_CLAIM_TEXT_CHARS))
        return None
    missing = _unsupported_figures(text, claims, context, basis)
    if missing:
        context.note(
            "unsupported_figure",
            where,
            "an unsupported figure",
        )
        context.returned.append(summarize_text(text, limit=_CLAIM_TEXT_CHARS))
        return None
    extrapolation = _unsupported_names(text, claims, context)
    if extrapolation:
        context.note(
            "unsupported_extrapolation",
            where,
            "a name or place the evidence does not state",
        )
        context.dispositions.append("returned_to_fact_checker")
        context.returned.append(summarize_text(text, limit=_CLAIM_TEXT_CHARS))
        return None
    return ReportPoint(
        text=summarize_text(text, limit=_POINT_CHARS),
        claim_ids=[claim.claim_id for claim in claims],
        source_urls=accepted,
        statement=_statement_for_claims(
            statement_id=context.next_id(statement_prefix),
            text=summarize_text(text, limit=_POINT_CHARS),
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
    corpus = _cited_evidence(claims, context) or context.corpus
    return [
        atom
        for atom in unattested_atoms(text, corpus)
        if not re.match(r"\d", atom)
    ]


def _unsupported_figures(
    text: str,
    claims: Sequence[Claim],
    context: DraftContext,
    basis: str,
) -> list[str]:
    """Figures the cited evidence does not state.

    A unit conversion or an arithmetic step passes only as a recorded
    derivation: the statement declares a ``basis``, that basis names the
    operation, and the premises it rests on — the figures the evidence does
    state — are attested. That is what separates a checked derivation from a
    model that did some arithmetic and called it a conversion.
    """
    corpus = _cited_evidence(claims, context) or context.corpus
    missing = [
        atom for atom in unattested_atoms(text, corpus) if re.match(r"\d", atom)
    ]
    if not missing:
        return []
    if not basis.strip():
        return missing
    tokens = _corpus_tokens(corpus)
    if not _names_an_operation(basis):
        return missing
    if not _derivation_premises(basis, tokens):
        return missing
    return []


def _optional_text(text: str, *, limit: int) -> str:
    """A written value, clamped, or an empty string when nothing was written.

    ``summarize_text`` renders a blank input as ``"(empty)"``, which is the
    right placeholder inside a sentence but wrong inside a table cell: the
    renderer's ``not stated`` is the honest value for a column the evidence
    did not fill.
    """
    if not text.strip():
        return ""
    return summarize_text(text, limit=limit)


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
    """
    written = _optional_text(text, limit=_CELL_CHARS)
    if written.casefold().strip(" .") == "not stated":
        written = ""
    if not written:
        return "", ReportStatement(
            statement_id=context.next_id("C"),
            text="not stated",
            mode="context",
            basis="the row's evidence does not state this cell",
        )
    selected = [
        claim
        for claim in context.approved.values()
        if claim.claim_id in set(row.claim_ids)
    ]
    evidence_text = _cited_evidence(selected, context) or context.corpus
    unattested = [
        *unattested_words(written, evidence_text),
        *unattested_atoms(written, evidence_text),
    ]
    if unattested:
        context.note(
            "unsupported_cell",
            f"{where} {cell}",
            "no evidence for this cell",
        )
        context.dispositions.append("returned_to_fact_checker")
        context.returned.append(summarize_text(written, limit=_CELL_CHARS))
        return "", ReportStatement(
            statement_id=context.next_id("C"),
            text="not stated",
            mode="context",
            basis="the cited evidence does not carry this cell",
        )
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
                text=summarize_text(repaired, limit=_POINT_CHARS),
                mode="context",
                basis=_uncertainty_basis(repaired),
            )
        )
    return statements


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
            draft.uncertainty_notes, context=context
        )
    contract = task.answer_contract
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
    errors = [invalid_draft_error(rejected)] if rejected else []
    return report, errors


def report_messages(
    task: SynthesisTask,
    *,
    finding_digest: int,
    claim_digest: int,
) -> list[ChatMessage]:
    """Build the messages that request one structured report draft.

    The evidence packet is built from canonical checked claims, ranked by
    coverage, verdict, and recorded impact, and bounded by the exact rendered
    character representation. Raw findings are carried as open questions
    only: they are leads, and the response contract forbids resting a settled
    statement on one.
    """
    packet, omitted = bounded_claim_packet(
        claim_registry(task.claims),
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
    canonical = build_canonical_packet(
        claims=[claim for _, claim in packet],
        clusters=task.claim_clusters,
        evidence=task.evidence_units,
        targets=task.targets,
        sources=task.sources,
        limit=claim_digest,
        failures=measured_failures(task),
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
            (
                "# Checked claims to cite\n"
                f"{render_report_claim_packet(packet, omitted=omitted)}"
            ),
            (
                "# Canonical evidence packet\n"
                f"{render_canonical_packet(canonical)}"
            ),
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
            + (
                "the retry returned a reply."
                if outcome == "answered"
                else "the retry was truncated as well."
            )
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


def invalid_draft_error(rejected: Sequence[str]) -> ResearchError:
    """Warn that some drafted report content was refused."""
    return agent_error(
        agent_name=SYNTHESIZER_NAME,
        error_type="synthesizer_invalid_draft",
        message=(
            "Some drafted report content was malformed, or rested on a claim "
            "or source that is not in the checked evidence; it is listed in "
            "the evidence ledger instead of the reader report."
        ),
        details={"rejected": list(rejected)},
    )


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

        An output-limit truncation is re-asked once at the lower effort, under
        the same output budget — the global cap, since this call sends no
        per-operation budget of its own. This is the largest output the run
        asks for (27,301 of 32,768 tokens on the audited live pass) and it has
        no fallback: without a synthesis there is no truthful report, so a
        second truncation keeps this call's own failure path rather than
        degrading. A provider outage is not re-asked at all.
        """
        if not task.claims and not task.sources and not task.findings:
            return None, [no_evidence_error()], False
        messages = report_messages(
            task,
            finding_digest=self._finding_digest,
            claim_digest=self._claim_digest,
        )
        truncations: list[ProviderOutputLimitError] = []
        for position, effort in enumerate(OUTPUT_LIMIT_ATTEMPT_EFFORTS):
            last_attempt = position + 1 == len(OUTPUT_LIMIT_ATTEMPT_EFFORTS)
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
                return (
                    None,
                    [*self._retry_records(truncations, "answered"), report_provider_error(error)],
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
