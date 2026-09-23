"""The Critic: review the exact candidate and emit typed repair actions.

The provider is asked for ``CritiqueDraft`` and never for ``Critique``:
``Critique`` declares ``CriticScore`` (1..10) and a non-blank rationale,
which strict structured outputs reject. Local code stamps the parts the
model must not be trusted with — the clamped score, the de-duplicated
notes, the bounded gap ids, and above all the routing decision.

The Critic has **no tools**. The historical spot-check loop spent ten
search/memory calls per pass and could open no page, so its searches could
not establish missing support, and a search snippet is not read-bearing
evidence (Section 2.1). What it reviews instead is one ``CriticPacket``
built from the exact candidate: the frozen question and answer contract,
the complete reader content, every reader statement with the evidence ids
behind it, the batched read excerpts, the deterministic hard checks, and
the open targets. The packet is fingerprinted, and the one permitted
repair of a malformed reply is refused unless it is the same packet.

Routing convention: ``route_decision`` checks the iteration bound *first*.
"The critic must not continue forever" is the one rule no model judgement
may override, so it is settled before anything the model said is read.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Collection, Sequence
from typing import Annotated, ClassVar

from pydantic import Field, ValidationError, model_validator

from deep_research.agents.base import AgentCompleter, AgentRun, BaseAgent
from deep_research.agents.errors import (
    AgentConfigurationError,
    agent_error,
    agent_provider_failure_details,
)
from deep_research.agents.events import agent_event
from deep_research.agents.identity import (
    merge_claim_snapshot,
    merge_source_snapshot,
)
from deep_research.agents.prompts import (
    CRITIC_REVIEW_SYSTEM_PROMPT,
    CRITIC_SYSTEM_PROMPT,
    CRITIQUE_INSTRUCTION,
    CRITIQUE_REPAIR_INSTRUCTION,
    AgentTask,
    render_source_quality,
)
from deep_research.agents.steps import ReActRun
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import Tracker
from deep_research.providers import (
    ChatMessage,
    ProviderError,
    ProviderOutputLimitError,
    StructuredOutputError,
    StructuredValidationDiagnostic,
)
from deep_research.providers.validation import (
    validation_diagnostic,
    validation_summary,
)
from deep_research.tools.base import BaseTool
from deep_research.utils.config import AgentRuntimeConfig, EffectiveModelConfig
from deep_research.utils.types import (
    answering_statement_for,
    EVIDENCE_BADGE_LABELS,
    QUESTION_TARGET_ID,
    AnswerContract,
    Claim,
    ClaimCluster,
    ContractModel,
    Critique,
    CritiqueGap,
    EvidenceUnit,
    GapKind,
    GapSeverity,
    RepairAction,
    ReportComposition,
    ReportQualitySnapshot,
    ReportStatement,
    ResearchError,
    ResearchEvent,
    ResearchState,
    ResearchStateUpdate,
    ScoredSource,
    SubTopic,
    gap_contract_problem,
)

CRITIC_NAME = "critic"

# The spec's acceptance threshold: a report scoring below this always buys
# another research pass while budget remains.
ACCEPTANCE_SCORE = 7
MIN_CRITIC_SCORE = 1
MAX_CRITIC_SCORE = 10

CRITIC_REPORT_CHARS = 6000
"""The size at which a reader section is called out as large in the request.

It is **not** a truncation bound. The request carries every reader section
whole — a per-section cap only moved the historical prefix failure inside a
section — and this value decides when a section's heading says how many
characters it carries, so the model knows it is reading a large section. Long
report tests also use it as the length a report must exceed to count as long.
"""
CRITIC_EVIDENCE_CHARS = 2000
DEFAULT_MAX_NOTES = 10

# --- the review packet's bounds ---------------------------------------------
#
# There are none, and that is the design. The report is never the thing that
# gets truncated: the historical failure was a late contradiction, a fabricated
# limitation, and a citation near the report's end falling outside old prefix
# boundaries, so the packet carries the complete reader content and every reader
# statement, and the request renders every reader section whole.
#
# Evidence is *batched*, because it is the one part with no natural ceiling —
# but batching groups items, it does not drop them: every item is carried as the
# exact passage the read registered (a qualifier at the end of a long passage is
# the sentence that turns an acceptance into a contradiction), and every
# registered unit is carried, in citation order, however many there are. The
# claim and source sections are the same: every checked claim, whole, and every
# cited source.
#
# The bound that replaced truncation is therefore batching, and the fallback
# when a request cannot fit is the provider-error path — the review is recorded
# ``failed`` and the run fails closed, never a partial view of the packet.
CRITIC_EVIDENCE_BATCH_CHARS = 4000

# One initial request plus exactly one repair. The provider already performs a
# single transport-level repair; this is the agent-level re-ask that carries
# the schema diagnostics back to the model.
CRITIC_REVIEW_ATTEMPTS = 2

CRITIC_REVIEW_OPERATION = "critic_report_review"
"""The operation label every record about this agent's one call carries.

The provider failure, the output-limit retry, and an unavailable review all
point at the same request, so a reader grouping a run's warnings by operation
sees them together instead of wondering which call each one is about.
"""

OUTPUT_LIMIT_RETRY_EFFORT = "high"
"""The effort a truncated review call is re-asked at, for either reviewer.

A reasoning token is a completion token, so the same output budget buys more
answer at a lower effort — and the Critic's own profile reasons at ``max``, so
the retry is where the effort comes *down*. Lower rather than higher on
purpose: this is a second attempt at getting the reply the run could not get,
not a request for a better one, and the budget is deliberately unchanged.

One value for both reviewers, imported rather than repeated: the terminal
report review follows the same rule, and two copies of "high" would let the two
halves of one rule drift apart.
"""

REVIEW_RETRY_OUTCOMES = ("answered", "truncated")
"""What one retry can come back with.

``answered`` is a reply — whether the review used it or the repair had to
re-ask it, which is the caller's own record to make; ``truncated`` is the same
truncation a second time. Two outcomes of the *retry call*, never a verdict
about the report, so the record cannot disagree with the review it precedes.
"""

REVIEW_ATTEMPT_EFFORTS: tuple[str | None, ...] = (
    None,
    OUTPUT_LIMIT_RETRY_EFFORT,
)
"""The efforts one review is attempted at, in order, and there are never more.

``None`` is the agent's own configured effort, which the first attempt always
uses, so an ordinary review is one call whose request is byte-identical to the
one this agent has always sent. The second entry is reached only by an
output-limit truncation of the first.
"""

PACKET_FINGERPRINT_CHARS = 12

# ``_render_balanced_report_sections`` renders this for the identity block of
# an empty report; a report that is present renders every reader section,
# using ``_SECTION_NOT_PRESENT`` for the ones it does not carry.
_NO_REPORT = "(no report)"
_SECTION_NOT_PRESENT = "(reader-report section not present)"

_READER_SECTION_ORDER = (
    ("summary", "Summary"),
    ("constraints", "Constraint ranking"),
    ("findings", "Findings"),
    ("uncertainty", "Uncertainty"),
    ("methodology", "Methodology"),
    ("references", "References"),
)


def _reader_section_key(heading: str) -> str | None:
    """Map a reader H2 heading to its stable prompt section key."""
    normalized = " ".join(re.sub(r"[^a-z0-9]+", " ", heading.casefold()).split())
    if normalized.startswith(("summary", "executive summary")):
        return "summary"
    if "constraint" in normalized:
        return "constraints"
    if "finding" in normalized:
        return "findings"
    if any(word in normalized for word in ("uncertainty", "conflict", "gap")):
        return "uncertainty"
    if "method" in normalized:
        return "methodology"
    if any(word in normalized for word in ("reference", "citation")):
        return "references"
    return None


def _split_reader_report(report: str) -> dict[str, str]:
    """Split a rendered reader report into independent prompt sections.

    The Critic receives each section separately.  This keeps a character cap
    on one section from hiding all later sections in a combined prefix.
    Unknown headings remain in the identity bucket so no report text is
    silently discarded.
    """
    sections: dict[str, list[str]] = {key: [] for key, _ in _READER_SECTION_ORDER}
    identity: list[str] = []
    current: str | None = None
    for line in report.strip().splitlines():
        match = re.match(r"^##\s+(.+?)\s*$", line)
        if match:
            current = _reader_section_key(match.group(1))
            if current is None:
                identity.append(line)
            continue
        if current is None:
            identity.append(line)
        else:
            sections[current].append(line)
    return {
        "identity": "\n".join(identity).strip(),
        **{
            key: "\n".join(lines).strip() for key, lines in sections.items()
        },
    }


def _render_balanced_report_sections(
    report: str,
    *,
    report_sections: dict[str, str] | None = None,
    note_limit: int = CRITIC_REPORT_CHARS,
) -> list[str]:
    """Render the report identity and every reader section, each one complete.

    Nothing is truncated. The historical failure was a late contradiction, a
    fabricated limitation, and a citation near the report's end falling outside
    a prefix; a per-section cap only moved that failure inside a section, since
    one long ``## Findings`` would still lose its own tail. The reader report
    is bounded upstream by the answer contract (<=8,000 words unless the
    contract asks for more), so the request carries it whole and states which
    sections are large rather than cutting them.
    """
    parsed = _split_reader_report(report)
    for raw_key, content in (report_sections or {}).items():
        key = raw_key if raw_key in parsed else _reader_section_key(raw_key)
        if key is not None:
            parsed[key] = content
    has_reader_section = any(parsed[key] for key, _ in _READER_SECTION_ORDER)
    identity = parsed["identity"]
    identity_fence_info = "reader-identity"
    if not report.strip():
        identity = _NO_REPORT
    elif not has_reader_section:
        # Compatibility for short/non-structured fixtures. A real rendered
        # report always has reader sections and follows the balanced path.
        identity = report.strip()
        identity_fence_info = _REPORT_FENCE_INFO
    sections = [("identity", "Report identity and declarations", identity)]
    sections.extend(
        (key, label, parsed[key]) for key, label in _READER_SECTION_ORDER
    )
    rendered: list[str] = []
    for key, label, content in sections:
        value = content.strip() or _SECTION_NOT_PRESENT
        fence = _report_fence(value)
        info = identity_fence_info if key == "identity" else f"reader-{key}"
        heading = label
        if len(value) > note_limit:
            heading = f"{label} ({len(value)} characters, carried whole)"
        rendered.append(f"# {heading}\n{fence}{info}\n{value}\n{fence}")
    return rendered

# The report is quoted inside a Markdown fence of its own: the opening fence is
# the begin marker and the closing fence is the end marker. A fence is the one
# Markdown construct that delimits a verbatim region, and its content is not
# parsed as Markdown, so no report heading can be read as a request section. The
# fence is made longer than any backtick run inside the report so the report
# cannot close it early.
_REPORT_FENCE_MIN = 3
_REPORT_FENCE_INFO = "report"


def _report_fence(report: str) -> str:
    """Return a backtick fence that no run inside ``report`` can close."""
    longest = max((len(run) for run in re.findall(r"`+", report)), default=0)
    return "`" * max(_REPORT_FENCE_MIN, longest + 1)


# This request's own sections are `#` (H1). The canonical report is H2 body
# sections (`REPORT_SECTIONS`) with H3 sub-groups, plus one H1 title,
# `# Research report: <question>`. Request sections were previously `##`, which
# put them at the same visual level as the report's own sections — `## Recorded
# problems` directly followed the report's `## Limitations` with nothing to
# distinguish them but the markers. At H1 every request heading outranks the
# report, so the report reads as content nested inside `# Report under review`.
# This also matches the spot-check path, which already introduces the report
# with the H1 title above.

# Two concrete, valid JSON instances that bracket the scale. The earlier
# skeleton used angle-bracket placeholders like `<integer 1-10>`, which is not
# valid JSON, so the model was shown something that was neither a schema nor an
# example. Two labelled examples demonstrate the range, and the band table below
# tells the model how to choose between them, because examples without
# calibration guidance invite it to split the difference. The provider already
# supplies the schema in a trailing system message, so this section supplies the
# examples the DeepSeek JSON Output guide asks for, and nothing that competes
# with it.
_LOW_EXAMPLE_SCORE = 3
_HIGH_EXAMPLE_SCORE = 9

_CRITIQUE_LOW_EXAMPLE_JSON = (
    '{"score": 3, "gaps": [{"gap_id": "gap-01", "target_ids": ["topic-02"], '
    '"claim_cluster_ids": [], "statement_ids": ["F004"], "kind": "coverage", '
    '"severity": "major", "repair_action": "acquire", "problem": "The report '
    'never states what share of cement emissions clinker substitution can '
    'remove, which is the figure the question turns on.", '
    '"recommended_queries": ["clinker substitution share of cement '
    'emissions"]}, {"gap_id": "gap-02", "target_ids": ["topic-03"], '
    '"claim_cluster_ids": [], "statement_ids": [], "kind": "missing_support", '
    '"severity": "major", "repair_action": "acquire", "problem": "It gives no '
    'cost figures for the alternatives it recommends.", '
    '"recommended_queries": ["low-carbon cement cost premium per tonne"]}], '
    '"unsupported_claims": ["The claim that commercial-scale '
    'deployment is accelerating, which no cited source measures."], '
    '"recommended_queries": ["clinker substitution share of cement emissions", '
    '"low-carbon cement cost premium per tonne"], "rationale": "The report '
    'names technologies and directions but supplies no measured figures, and '
    'its central claim about deployment rests on no cited source at all, so '
    'the question is answered only in generalities."}'
)

_CRITIQUE_HIGH_EXAMPLE_JSON = (
    '{"score": 9, "gaps": [{"gap_id": "gap-01", "target_ids": [], '
    '"claim_cluster_ids": [], "statement_ids": ["S002"], "kind": '
    '"presentation", "severity": "minor", "repair_action": "synthesize", '
    '"problem": "The same durability figure is restated in the summary and '
    'again in the first finding.", "recommended_queries": []}], '
    '"unsupported_claims": [], "recommended_queries": [], "rationale": "The '
    'report answers the question completely, every load-bearing figure is '
    'attributed to a strong and diverse set of named sources, and it states '
    'its own durability uncertainty plainly instead of hiding it."}'
)

# How to choose a score. The thresholds themselves are not published to the
# model: routing is computed locally, and `CRITIQUE_INSTRUCTION` tells the model
# not to decide continuation. This table describes report quality, not policy.
_CRITIQUE_SCORE_BANDS = (
    "Choose the score from the report's weakest load-bearing element, not from "
    "its overall polish:\n"
    "1-3: the question is largely unanswered, or the central claims rest on no "
    "cited source.\n"
    "4-6: a partial answer whose key numbers, mechanisms, or trade-offs are "
    "missing or unsupported.\n"
    "7-8: the question is answered and every load-bearing claim is attributed, "
    "with at most narrow gaps.\n"
    "9-10: reserve for an answer that is complete, strongly and diversely "
    "sourced, and explicit about its own uncertainty."
)


_RATIONALE_CHARS = 600

# Enumerated, project-generated routing reasons. Never provider text: these
# reach ResearchEvent.metadata and the recorded rationale.
ROUTING_REASONS = {
    "accepted_quality": "The report met the acceptance threshold.",
    "max_iterations_reached": (
        "The refinement budget is exhausted; this is the final report."
    ),
    "low_score": "The report scored below the acceptance threshold.",
    "critical_gaps": "Material gaps remain in the research.",
    "unsupported_claims": (
        "The report leans on statements no source supports."
    ),
    "missing_report": "No report was available to review.",
    "provider_unavailable": (
        "The model provider failed while the report was reviewed."
    ),
    "review_failed": (
        "The model's review never validated, so the report was not judged; "
        "the run ended rather than accepting an unreviewed report."
    ),
    "review_unavailable": (
        "Both review calls were truncated by the output limit, so no "
        "judgement of the report exists; the run publishes it unscored rather "
        "than ending."
    ),
}

# The two conditions under which no model review exists at all. A failed
# review is the third, and it has its own constructor (``failed_critique``)
# because it must also record the attempt count and the bounded diagnostics
# that ``fallback_critique`` has no way to carry.
CRITIQUE_FALLBACK_REASONS = ("missing_report", "provider_unavailable")


class CritiqueGapDraft(ContractModel):
    """One provider-reported gap, validated to the same contract as the model.

    The mirrored shape of ``CritiqueGap`` plus a ``gap_id`` the model may echo
    and this module ignores. The bounded id is stamped locally — it has to be
    stable, unique, and bounded within one review, and a model-chosen id is
    none of those — but an id field the reply examples show is a field the
    model will fill, so the schema accepts it rather than rejecting a
    well-formed review over an echo.

    ``validate_actionable`` applies :func:`gap_contract_problem` — the same
    rule ``CritiqueGap`` applies — at the *provider* boundary. Model validators
    are invisible to the JSON schema, so pydantic is what enforces them: the
    reply is validated into this draft before anything local is stamped, a
    violation is a structured-output failure like any other, and it takes the
    repair path. Without this, a reply the schema accepted and the contract
    rejected would abort the review instead of being repaired — and the shape
    the previous prompt taught (every gap carrying ``recommended_queries``) is
    exactly one of those replies.
    """

    gap_id: str = ""
    coverage_id: str | None = None
    target_ids: list[str] = Field(default_factory=list)
    claim_cluster_ids: list[str] = Field(default_factory=list)
    statement_ids: list[str] = Field(default_factory=list)
    kind: GapKind
    severity: GapSeverity
    repair_action: RepairAction
    problem: str = Field(min_length=1)
    """The defect, in the model's words, and never blank.

    ``min_length=1`` matters more than it looks: ``ContractModel`` strips
    whitespace, so a reply of ``"   "`` arrived as ``""`` and the contract rule
    — which reads the ids, not the text — had nothing to say about it. The gap
    was then dropped by the local normalizer, and a material defect with a
    perfectly valid scope vanished from the review. A blank problem is a
    malformed reply, so it fails here and takes the repair path.
    """
    recommended_queries: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_actionable(self) -> "CritiqueGapDraft":
        problem = gap_contract_problem(
            kind=self.kind,
            severity=self.severity,
            repair_action=self.repair_action,
            coverage_id=self.coverage_id,
            target_ids=self.target_ids,
            statement_ids=self.statement_ids,
            claim_cluster_ids=self.claim_cluster_ids,
            recommended_queries=self.recommended_queries,
        )
        if problem is not None:
            raise ValueError(problem)
        return self


def normalize_gap_drafts(values: object) -> object:
    """Coerce the pre-Task-8 free-text gap list into typed gap drafts.

    The single place the legacy gap shape is understood *at a state boundary*:
    the state-facing ``Critique`` calls this so an old persisted artifact stays
    readable. The provider-facing ``CritiqueDraft`` deliberately does **not**:
    a live reply that ignores the typed response contract is a malformed reply,
    and rewriting it here rewarded it with a material ``target_ids=["question"]``
    gap instead of the one repair.

    A legacy gap is a *string*, and only a string is treated as one. That
    distinction is what lets an unscoped **typed** gap fail validation
    instead of being quietly given a scope: a dict with no target, statement,
    or cluster came from the model and is exactly the unactionable gap the
    contract refuses, while a bare string predates the typed contract and
    named nothing because there was nothing to name.

    A legacy gap was listed only when closing it would materially change the
    answer, so it becomes a material ``coverage`` gap scoped to the whole
    answer — ``target_ids=["question"]``. No planned topic id is invented for
    it: the sentinel says "the answer to the question", which is what the old
    contract meant and what Task 9 has to route.

    A non-dict or non-list value is returned untouched, leaving the error to
    the field validator that owns it.
    """
    if not isinstance(values, dict) or not isinstance(values.get("gaps"), list):
        return values
    converted = dict(values)
    converted["gaps"] = [
        {
            "coverage_id": None,
            "target_ids": [QUESTION_TARGET_ID],
            "claim_cluster_ids": [],
            "statement_ids": [],
            "kind": "coverage",
            "severity": "major",
            "repair_action": "acquire",
            "problem": gap,
            "recommended_queries": [],
        }
        if isinstance(gap, str)
        else gap
        for gap in values["gaps"]
    ]
    return converted


_CritiqueNote = Annotated[str, Field(min_length=1)]
"""One provider-supplied note that must say something.

``ContractModel`` strips whitespace, so ``"   "`` arrives as ``""``. A blank
note is not a note: ``normalize_notes`` dropped it, which meant a reply that
reported an unsupported claim could be read as a reply that reported none, and
a score of 9 then accepted the report on the strength of a defect that was
deleted locally. It is a malformed reply instead, and takes the repair.

Private on purpose: the alias spells one field's rule inside one module, and
every public module-level name here is pinned into the package's exported
surface (``tests/test_imports.py``).
"""


class CritiqueDraft(ContractModel):
    """One model review, before domain validation.

    Every field here is the provider's own value, or the reply is refused.
    ``score`` is a plain ``int`` bounded to the declared 1-10 range rather than
    ``CriticScore``, so the *range* is part of the reply contract while the
    error a caller sees stays a schema failure: a model that answers 0, 42,
    ``"9"`` or ``true`` made a formatting mistake, and the reply is refused and
    re-asked once rather than coerced or clamped into a number nobody wrote.
    ``gaps`` is bounded to the same limit the normalizer applies, so an
    overflowing reply is refused instead of having its tail silently dropped
    before routing, and a gap's ``kind``, ``severity`` and ``repair_action``
    are required rather than defaulted: a default would be a routing judgement
    this module made and the model never sent.

    The legacy *string* gap shape is **not** accepted here. It belongs to old
    persisted state, which ``Critique`` reads through ``normalize_gap_drafts``;
    a live reply that ignored the typed contract is repaired rather than
    rewritten into a material obligation.
    """

    score: int = Field(ge=MIN_CRITIC_SCORE, le=MAX_CRITIC_SCORE, strict=True)
    gaps: list[CritiqueGapDraft] = Field(max_length=DEFAULT_MAX_NOTES)
    unsupported_claims: list[_CritiqueNote]
    recommended_queries: list[str]
    rationale: str


# --- the review packet -------------------------------------------------------


def _badge_label(badge: str) -> str:
    """The reader-facing label of one evidence badge, or an honest blank."""
    return EVIDENCE_BADGE_LABELS.get(
        badge, EVIDENCE_BADGE_LABELS[""]
    )


class CriticEvidenceItem(ContractModel):
    """One read excerpt the Critic may treat as evidence.

    Only a registered ``EvidenceUnit`` becomes one of these: an exact passage
    of a successful same-run read, with its read id, locator, and badge. A
    search result, a snippet, or a memory recall can never construct one, so
    "a search snippet cannot appear as verification evidence" is a property of
    the type rather than of the prompt.
    """

    evidence_id: str = Field(min_length=1)
    read_id: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    source_title: str = Field(min_length=1)
    locator: str = Field(min_length=1)
    excerpt: str = Field(min_length=1)
    target_ids: list[str] = Field(default_factory=list)
    badge: str = ""
    badge_label: str = Field(min_length=1)
    cited_by_statement_ids: list[str] = Field(default_factory=list)

    @classmethod
    def from_unit(
        cls,
        unit: EvidenceUnit,
        *,
        badge: str,
        cited_by_statement_ids: Sequence[str] = (),
    ) -> "CriticEvidenceItem":
        return cls(
            evidence_id=unit.evidence_id,
            read_id=unit.read_id,
            source_url=unit.source_url,
            source_title=unit.source_title,
            locator=unit.locator,
            excerpt=unit.excerpt,
            target_ids=list(unit.target_ids),
            badge=badge,
            badge_label=_badge_label(badge),
            cited_by_statement_ids=list(cited_by_statement_ids),
        )


class CriticEvidenceBatch(ContractModel):
    """A bounded group of evidence items, rendered under one heading.

    ``chars`` is the rendered length of the batch, recorded on the batch so
    "no batch exceeds the budget" is checkable without re-rendering it. A
    single item larger than the budget is its own batch and is allowed to
    exceed it: dropping or re-cutting an exact excerpt would change the
    evidence, which is worse than one long batch.
    """

    batch_id: str = Field(min_length=1)
    items: list[CriticEvidenceItem] = Field(min_length=1)
    chars: int = Field(ge=1)


class CriticTarget(ContractModel):
    """One evidence obligation, with what the report actually answered."""

    target_id: str = Field(min_length=1)
    coverage_id: str = Field(min_length=1)
    coverage_title: str = ""
    question: str = Field(min_length=1)
    required_dimensions: list[str] = Field(default_factory=list)
    required: bool = True
    critical: bool = False
    support_policy: str = "independent_pair"
    answered: bool = False
    """Whether one reader statement satisfies this obligation.

    Read straight from ``target_is_answered`` — the gate that decides coverage
    — so the Critic is never told an obligation is met that the run will later
    refuse, and never the reverse.
    """
    answered_dimension_ids: list[str] = Field(default_factory=list)
    answered_by_statement_ids: list[str] = Field(default_factory=list)
    """Which dimensions the answering statement filled, and which one it is.

    Diagnostic detail, rendered beside the target: the ids name the record the
    judgement rests on. They are read off the single answering statement, so a
    statement that does not answer contributes none of them.
    """

    @property
    def open(self) -> bool:
        """True when no reader statement satisfies every required dimension."""
        return not self.answered


class CriticPacket(ContractModel):
    """Everything one review is allowed to judge, and nothing else.

    The single input to a review: the frozen question and answer contract, the
    complete reader content, every reader statement with the evidence ids
    behind it, the batched read excerpts, the deterministic hard checks, and
    the target inventory with what is still open. It carries no score, no
    verdict from a later reviewer, and no prior run's judgement — the Critic
    judges evidence, and a packet that carried a score would be asking it to
    agree with one.

    ``fingerprint`` covers the exact text and ids the review was opened on.
    The one permitted repair is refused unless it re-reads the same
    fingerprint, so a repaired critique can never be a second review of
    different text wearing the first one's authority.
    """

    question: str = Field(min_length=1)
    answer_contract: AnswerContract | None = None
    reader_content: str = ""
    reader_sections: dict[str, str] = Field(default_factory=dict)
    statements: list[ReportStatement] = Field(default_factory=list)
    targets: list[CriticTarget] = Field(default_factory=list)
    evidence_batches: list[CriticEvidenceBatch] = Field(default_factory=list)
    omitted_evidence_ids: list[str] = Field(default_factory=list)
    hard_checks: list[str] = Field(default_factory=list)
    quality: ReportQualitySnapshot | None = None
    claims: list[Claim] = Field(default_factory=list)
    sources: list[ScoredSource] = Field(default_factory=list)
    sub_topics: list[SubTopic] = Field(default_factory=list)
    errors: list[ResearchError] = Field(default_factory=list)
    error_count: int = Field(default=0, ge=0)
    error_groups: dict[str, list[ResearchError]] = Field(default_factory=dict)
    evidence_unit_count: int = Field(default=0, ge=0)
    unrecorded_statement_count: int = Field(default=0, ge=0)
    fingerprint: str = ""

    @property
    def open_targets(self) -> list[CriticTarget]:
        return [target for target in self.targets if target.open]

    @property
    def statement_ids(self) -> list[str]:
        return [statement.statement_id for statement in self.statements]

    @property
    def claim_cluster_ids(self) -> list[str]:
        seen: list[str] = []
        for statement in self.statements:
            for cluster_id in statement.claim_cluster_ids:
                if cluster_id not in seen:
                    seen.append(cluster_id)
        return seen

    @property
    def target_ids(self) -> list[str]:
        return [target.target_id for target in self.targets]

    @property
    def evidence_ids(self) -> list[str]:
        return [
            item.evidence_id
            for batch in self.evidence_batches
            for item in batch.items
        ]

    def statement(self, statement_id: str) -> ReportStatement | None:
        return next(
            (
                statement
                for statement in self.statements
                if statement.statement_id == statement_id
            ),
            None,
        )

    def targets_by_coverage_id(self, coverage_id: str) -> list[str]:
        return [
            target.target_id
            for target in self.targets
            if target.coverage_id == coverage_id
        ]


def critic_packet_fingerprint(packet: CriticPacket) -> str:
    """The stable digest of the exact packet one review is opened on.

    Twelve hex characters over the packet's canonical JSON, with its own
    ``fingerprint`` field excluded — a hand-written subset was the wrong
    identity, because *every* field of this packet is rendered into the request
    or into the review's material: an evidence item's ``source_url`` is the
    independence signal a reader checks, a target's ``support_policy`` is the
    obligation's own rule, ``answered_dimensions`` is what the report claims to
    have answered, and ``hard_checks`` is the deterministic verdict. Hashing a
    subset meant any of those could change while the fingerprint stayed put, and
    the repair guard would then wave through a review of different material.

    The canonical dump is sorted and separator-normalized before hashing, so two
    identical packets always produce one digest and no field can be reordered
    into a different identity.
    """
    payload = packet.model_dump(mode="json", exclude={"fingerprint"})
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:PACKET_FINGERPRINT_CHARS]


def _cluster_badge(cluster: ClaimCluster) -> str:
    """The one badge a cluster's recorded verdicts agree on, or a blank.

    A cluster whose members disagree has no single badge: ``verified_pair``
    beside a ``source_supported`` verdict would let a review read the weaker
    evidence as the stronger one, so an ambiguous cluster reports no badge and
    the label says exactly that.
    """
    badges = {
        badge
        for badge in cluster.verdict_evidence_status.values()
        if badge in EVIDENCE_BADGE_LABELS
    }
    return badges.pop() if len(badges) == 1 else ""


def _batch_evidence(
    items: Sequence[CriticEvidenceItem],
) -> list[CriticEvidenceBatch]:
    """Fill bounded batches in order, never dropping or re-cutting an item."""
    batches: list[CriticEvidenceBatch] = []
    current: list[CriticEvidenceItem] = []
    current_chars = 0

    def rendered_chars(item: CriticEvidenceItem) -> int:
        return len(item.excerpt) + len(item.source_title) + len(item.locator) + 64

    def flush() -> None:
        nonlocal current, current_chars
        if not current:
            return
        batches.append(
            CriticEvidenceBatch(
                batch_id=f"batch-{len(batches) + 1:02d}",
                items=list(current),
                chars=max(1, current_chars),
            )
        )
        current = []
        current_chars = 0

    for item in items:
        size = rendered_chars(item)
        if current and current_chars + size > CRITIC_EVIDENCE_BATCH_CHARS:
            flush()
        current.append(item)
        current_chars += size
    flush()
    return batches


def _unrecorded_statement_count(composition: ReportComposition | None) -> int:
    """Rendered reader prose blocks with no statement record.

    A composition guarantees one record per rendered statement, so this is
    zero for a current pass. A report with no composition at all is the honest
    non-zero case: the report exists as Markdown and nothing behind it can be
    looked up, which is itself a defect and is reported as a hard check rather
    than as a gap this module invents.
    """
    if composition is None:
        return 1
    rendered = len(composition.summary) + len(composition.constraints)
    rendered += sum(len(section.points) for section in composition.sections)
    rendered += len(composition.answer_rows)
    recorded = len(composition.statements)
    return max(0, rendered - recorded)


def build_critic_packet(
    state: ResearchState,
    composition: ReportComposition | None = None,
) -> CriticPacket:
    """Build the one packet a review reads, from the exact candidate.

    ``composition`` defaults to ``state.composition``. The report text comes
    from ``state.report`` verbatim; the statements, targets, and evidence come
    from the composition, which is why a gap can only ever cite a record the
    packet actually carries.
    """
    if composition is None:
        composition = state.composition
    report = state.report or ""

    statements = list(composition.statements) if composition is not None else []
    clusters = dict(composition.claim_clusters) if composition is not None else {}
    units = dict(composition.evidence_units) if composition is not None else {}
    sub_topics = list(composition.sub_topics) if composition is not None else (
        list(state.sub_topics)
    )

    cited_by: dict[str, list[str]] = {}
    for statement in statements:
        for evidence_id in statement.evidence_ids:
            cited_by.setdefault(evidence_id, []).append(statement.statement_id)

    badge_by_cluster = {
        cluster_id: _cluster_badge(cluster)
        for cluster_id, cluster in clusters.items()
    }
    # One physical passage can corroborate one statement and contest another.
    # Taking the first badge in statement order made the review's view of that
    # passage depend on the order the statements happened to render in, and it
    # could print "independently corroborated" beside a cluster that contests
    # the same read. Any disagreement reports no badge, exactly as
    # ``_cluster_badge`` treats disagreeing verdicts one level up.
    badges_by_evidence: dict[str, set[str]] = {}
    for statement in statements:
        for cluster_id in statement.claim_cluster_ids:
            badge = badge_by_cluster.get(cluster_id, "")
            if not badge:
                continue
            for evidence_id in statement.evidence_ids:
                badges_by_evidence.setdefault(evidence_id, set()).add(badge)
    badge_by_evidence: dict[str, str] = {
        evidence_id: (next(iter(badges)) if len(badges) == 1 else "")
        for evidence_id, badges in badges_by_evidence.items()
    }

    cited_ids: list[str] = []
    for statement in statements:
        for evidence_id in statement.evidence_ids:
            if evidence_id not in cited_ids:
                cited_ids.append(evidence_id)
    ordered_ids = [
        *cited_ids,
        *(evidence_id for evidence_id in units if evidence_id not in cited_ids),
    ]
    retained_ids = ordered_ids
    omitted_ids: list[str] = []
    items = [
        CriticEvidenceItem.from_unit(
            units[evidence_id],
            badge=badge_by_evidence.get(evidence_id, ""),
            cited_by_statement_ids=cited_by.get(evidence_id, ()),
        )
        for evidence_id in retained_ids
        if evidence_id in units
    ]

    targets: list[CriticTarget] = []
    for topic in sub_topics:
        for target in topic.evidence_targets:
            # One statement answers an obligation, and the same rule decides it
            # here as everywhere else: pooling every mentioning statement's
            # dimensions let a ``context`` statement, or one failing the
            # support policy, close a target the deterministic gate refuses —
            # the Critic was told an obligation was met that the run would go
            # on to report as open. The ids stay as diagnostic detail, read off
            # the statement that actually answers.
            answering = answering_statement_for(state, target)
            targets.append(
                CriticTarget(
                    target_id=target.target_id,
                    coverage_id=target.coverage_id,
                    coverage_title=topic.title,
                    question=target.question,
                    required_dimensions=list(target.required_dimensions),
                    required=target.required,
                    critical=target.critical,
                    support_policy=target.support_policy,
                    answered=answering is not None,
                    answered_dimension_ids=(
                        [] if answering is None else list(answering.answered_dimensions)
                    ),
                    answered_by_statement_ids=(
                        [] if answering is None else [answering.statement_id]
                    ),
                )
            )

    unrecorded = _unrecorded_statement_count(composition)
    hard_checks = list(state.quality.hard_failures) if state.quality else []
    if composition is None:
        hard_checks.append(
            "the report carries no typed composition, so no reader statement "
            "record exists to tie a finding to its evidence"
        )
    if unrecorded:
        hard_checks.append(
            f"{unrecorded} reader prose block(s) carry no statement record; a "
            "gap may not be raised against prose no record can resolve"
        )
    missing_evidence = [
        evidence_id
        for statement in statements
        for evidence_id in statement.evidence_ids
        if evidence_id not in units
    ]
    if missing_evidence:
        hard_checks.append(
            f"{len(set(missing_evidence))} statement evidence id(s) are not in "
            "the read registry"
        )

    errors = list(state.errors)
    packet = CriticPacket(
        question=state.original_question,
        answer_contract=state.answer_contract,
        reader_content=report,
        reader_sections=_split_reader_report(report),
        statements=statements,
        targets=targets,
        evidence_batches=_batch_evidence(items),
        omitted_evidence_ids=omitted_ids,
        hard_checks=hard_checks,
        quality=state.quality,
        claims=merge_claim_snapshot([], state.verified_claims),
        sources=merge_source_snapshot([], state.evaluated_sources),
        sub_topics=sub_topics,
        errors=errors,
        error_count=len(errors),
        error_groups=_group_errors_by_agent_stage(errors),
        evidence_unit_count=len(units),
        unrecorded_statement_count=unrecorded,
    )
    # Stamped last, from the packet that will actually be sent: the digest
    # covers every field above, so it cannot be computed before they exist.
    return packet.model_copy(
        update={"fingerprint": critic_packet_fingerprint(packet)}
    )


class CritiqueTask(AgentTask):
    """An ``AgentTask`` bound to the packet it reviews.

    Carrying the packet on the task is what lets ``review`` route without the
    agent holding mutable state across await points — the same reason
    ``ClaimTask`` exists — and it is what makes the repair's fingerprint check
    meaningful: the packet the review was opened on travels with the review.

    The report, claim, source, and quality fields are the pre-Task-8 shape,
    kept for a caller that builds a task by hand (a fixture, or a test of the
    request itself). They are never both authoritative: ``packet`` wins
    wherever it is present, and ``packet_for_task`` assembles one from these
    fields only when it is not.
    """

    packet: CriticPacket | None = None
    report: str = ""
    iteration: int = Field(default=0, ge=0)
    max_iterations: int = Field(default=1, ge=1)
    claims: list[Claim] = []
    sources: list[ScoredSource] = []
    sub_topics: list[SubTopic] = []
    error_count: int = Field(default=0, ge=0)
    quality: ReportQualitySnapshot | None = None
    report_sections: dict[str, str] = {}
    errors: list[ResearchError] = []
    error_groups: dict[str, list[ResearchError]] = {}


def packet_for_task(task: CritiqueTask) -> CriticPacket:
    """The packet this task reviews, assembled from its own fields if needed.

    The compatibility half of ``CritiqueTask``: a task built by hand carries
    no composition, so its packet has no statements, no read evidence, and the
    hard check that says so. Nothing is invented for it — an empty statement
    registry is the honest value for a caller that supplied no records.
    """
    if task.packet is not None:
        return task.packet
    errors = list(task.errors)
    targets = [
        CriticTarget(
            target_id=target.target_id,
            coverage_id=target.coverage_id,
            question=target.question,
            required_dimensions=list(target.required_dimensions),
            required=target.required,
            critical=target.critical,
            support_policy=target.support_policy,
        )
        for topic in task.sub_topics
        for target in topic.evidence_targets
    ]
    hard_checks: list[str] = []
    if not task.report.strip():
        hard_checks.append("no reader report was supplied for this review")
    hard_checks.append(
        "the task carries no typed composition, so no reader statement record "
        "exists to tie a finding to its evidence"
    )
    if task.quality:
        hard_checks.extend(task.quality.hard_failures)
    packet = CriticPacket(
        question=task.instruction,
        reader_content=task.report,
        reader_sections=task.report_sections or _split_reader_report(task.report),
        targets=targets,
        hard_checks=hard_checks,
        quality=task.quality,
        claims=list(task.claims),
        sources=list(task.sources),
        sub_topics=list(task.sub_topics),
        errors=errors,
        error_count=task.error_count or len(errors),
        error_groups=task.error_groups or _group_errors_by_agent_stage(errors),
        unrecorded_statement_count=1 if task.report.strip() else 0,
    )
    return packet.model_copy(
        update={"fingerprint": critic_packet_fingerprint(packet)}
    )


def _render_planned_sub_topic(sub_topic: SubTopic) -> str:
    """One planned sub-topic as ``- <coverage_id>: <title>``.

    The id comes first so a model naming a ``coverage_id`` has one unambiguous
    token per topic, and the title stays beside it so the gap it writes can be
    recognised by a reader. A gap's scope is resolved against the target
    inventory below; this list is what the ids in that inventory belong to.
    """
    return f"- {sub_topic.coverage_id}: {sub_topic.title}"


def _render_sub_topics(packet: CriticPacket) -> str:
    """The planned sub-topics, or the honest absence of a plan."""
    if not packet.sub_topics:
        return "(no sub-topic was planned for this pass)"
    return "\n".join(
        _render_planned_sub_topic(sub_topic) for sub_topic in packet.sub_topics
    )


def _render_reader_statement(statement: ReportStatement) -> str:
    """One reader statement as the review reads it: id, mode, and its links.

    The id comes first because it is the token a gap has to copy, and the
    evidence ids travel beside the text so a review can tell an attributed
    sentence from a corroborated one without consulting anything else.
    """
    evidence = ", ".join(statement.evidence_ids) or "none"
    targets = ", ".join(statement.target_ids) or "none"
    dimensions = ", ".join(statement.answered_dimensions) or "none"
    lines = [
        f"- {statement.statement_id} [{statement.mode}]: {statement.text}",
        f"  clusters: {', '.join(statement.claim_cluster_ids) or 'none'}",
        f"  evidence: {evidence}",
        f"  targets: {targets}",
        f"  answered dimensions: {dimensions}",
    ]
    if statement.basis:
        lines.append(f"  basis: {statement.basis}")
    return "\n".join(lines)


def _render_target(target: CriticTarget) -> str:
    """One evidence obligation, with what the report already answered."""
    status = "OPEN" if target.open else "answered"
    flags = ", ".join(
        flag
        for flag, on in (
            ("required", target.required),
            ("critical", target.critical),
        )
        if on
    )
    title = f' "{target.coverage_title}"' if target.coverage_title else ""
    dimensions = ", ".join(target.required_dimensions) or "none"
    answered = ", ".join(target.answered_dimension_ids) or "none"
    answering = ", ".join(target.answered_by_statement_ids) or "no statement"
    return "\n".join(
        [
            f"- {target.target_id} ({target.coverage_id}{title}) "
            f"[{status}; {flags or 'optional'}]",
            f"  obligation: {target.question}",
            f"  required dimensions: {dimensions}",
            f"  answered dimensions: {answered}",
            f"  answered by: {answering}",
            f"  support policy: {target.support_policy}",
        ]
    )


def _render_evidence_item(item: CriticEvidenceItem) -> str:
    """One read excerpt: its id, its badge, and where it came from."""
    cited = ", ".join(item.cited_by_statement_ids) or "no statement"
    return "\n".join(
        [
            f"- {item.evidence_id} [{item.badge_label}] read={item.read_id} "
            f"locator={item.locator}",
            f"  source: {item.source_title} — {item.source_url}",
            f"  cited by: {cited}",
            f"  excerpt: {item.excerpt}",
        ]
    )


def _render_evidence_batches(packet: CriticPacket) -> str:
    """Every evidence batch under its own heading, with its own budget."""
    if not packet.evidence_batches:
        registry = (
            f"{packet.evidence_unit_count} registered read excerpt(s) exist, "
            "but none of them is cited by a reader statement"
            if packet.evidence_unit_count
            else "no read excerpt was registered for this pass"
        )
        lines = [f"({registry})"]
    else:
        lines = []
        for batch in packet.evidence_batches:
            lines.append(f"## {batch.batch_id}")
            lines.extend(_render_evidence_item(item) for item in batch.items)
    if packet.omitted_evidence_ids:
        lines.append(
            "## Omitted from this request\n"
            f"{len(packet.omitted_evidence_ids)} further registered excerpt(s) "
            "were not selected for this packet because the packet's unit bound "
            "is reached; their ids are recorded rather than dropped: "
            f"{', '.join(packet.omitted_evidence_ids)}"
        )
    return "\n".join(lines)


def _render_statements(packet: CriticPacket) -> str:
    """Every reader statement, or the honest absence of any record."""
    if not packet.statements:
        return (
            "No reader statement record exists for this report, so a gap may "
            "not cite a statement id. Raising a defect against prose no record "
            "can resolve is itself the defect."
        )
    return "\n".join(
        _render_reader_statement(statement) for statement in packet.statements
    )


def _render_targets(packet: CriticPacket) -> str:
    """The target inventory, open obligations first so they cannot be missed."""
    if not packet.targets:
        return (
            "No evidence target was recorded for this pass; the only scope a "
            f'gap may name is "{QUESTION_TARGET_ID}".'
        )
    ordered = sorted(packet.targets, key=lambda target: not target.open)
    lines = [
        _render_target(target) for target in ordered
    ]
    lines.append(
        f'An original-question omission names "{QUESTION_TARGET_ID}" in '
        "target_ids and repairs by extend_plan; it never invents a target id."
    )
    return "\n".join(lines)


def _render_hard_checks(packet: CriticPacket) -> str:
    """The deterministic integrity facts, stated as facts rather than scores."""
    if not packet.hard_checks:
        return (
            "(no deterministic hard check failed for this pass)"
        )
    return "\n".join(f"- {check}" for check in packet.hard_checks)


def _render_answer_contract(contract: AnswerContract | None) -> str:
    """The frozen answer form, or the honest absence of one."""
    if contract is None:
        return "(no frozen answer contract was recorded for this pass)"
    return json.dumps(
        contract.model_dump(mode="json"), ensure_ascii=False, sort_keys=True
    )


def clamp_score(value: int) -> int:
    """Pin a model score into the ``CriticScore`` range.

    Retained as part of the exported agent helper surface; the review path no
    longer calls it. ``CritiqueDraft`` refuses an out-of-range score and the
    reply is repaired, because pinning a provider value into the band turns a
    malformed review into an acceptance.
    """
    return min(MAX_CRITIC_SCORE, max(MIN_CRITIC_SCORE, int(value)))


def normalize_notes(
    values: Sequence[str],
    *,
    limit: int = DEFAULT_MAX_NOTES,
) -> list[str]:
    """Collapse, de-duplicate, and cap one model-supplied note list."""
    if limit < 1:
        raise ValueError("limit must be at least 1")
    notes: list[str] = []
    for value in values:
        note = " ".join(value.split())
        if note and note not in notes:
            notes.append(note)
    return notes[:limit]


def normalize_gaps(
    values: Sequence[str | CritiqueGapDraft],
    *,
    known_coverage_ids: Collection[str] = (),
    packet: CriticPacket | None = None,
    limit: int = DEFAULT_MAX_NOTES,
) -> list[CritiqueGap]:
    """Normalize provider gaps into actionable, bounded, id-stamped defects.

    Two rules are inherited from the reviewed Task 7 boundary, and both keep
    the same shape: an id the plan cannot answer is *not obeyed*, and titles
    and problem text are never consulted when deciding what a gap targets, so
    a similarly named topic cannot receive another topic's gap.

    The whole-answer fallback is applied **only** to a gap that declared a
    scope which did not resolve — an id the packet cannot answer, the shape
    Task 7 already handled. A gap that declared *no* scope at all is left
    exactly as it came, so it fails the contract and takes the repair path:
    "the report is not good enough" is not an actionable defect, and quietly
    scoping it to the whole answer would record a vague complaint as a
    material obligation. The single exception is the legacy *string* gap,
    which predates the typed contract and named nothing because there was
    nothing to name.

    Scope is resolved locally wherever the packet can resolve it: a known
    ``coverage_id`` contributes the targets planned under it, which is what a
    model means when it names a sub-topic and asks for acquisition. Statement
    and cluster ids resolve only against the packet's own registries — a gap
    may not be raised against a record that does not exist.

    A gap the contract still refuses raises ``CritiqueContractViolation``,
    which is the agent's cue to repair it exactly as it repairs a malformed
    reply; nothing here silently drops or rewrites a defect. The ``limit`` is
    the same bound: a review carrying more than ``limit`` distinct gaps is
    refused rather than cut, because the tail of a gap list is where the one
    material defect can sit behind ten editorial ones, and the cut left no
    trace in the critique for a later reader to notice.
    """
    if limit < 1:
        raise ValueError("limit must be at least 1")
    known_statement_ids = set(packet.statement_ids) if packet else set()
    known_cluster_ids = set(packet.claim_cluster_ids) if packet else set()
    known_target_ids = set(packet.target_ids) if packet else set()
    # The packet knows which sub-topics it carries, so a caller that supplied
    # one no longer has to pass the ids as well: two sources of the same
    # knowledge are two sources that can disagree.
    coverage_ids = set(known_coverage_ids)
    if packet is not None:
        coverage_ids.update(
            target.coverage_id for target in packet.targets
        )
        coverage_ids.update(
            sub_topic.coverage_id for sub_topic in packet.sub_topics
        )
    gaps: list[CritiqueGap] = []
    kept: dict[tuple[object, ...], int] = {}
    for value in values:
        draft = (
            CritiqueGapDraft(
                coverage_id=None,
                target_ids=[QUESTION_TARGET_ID],
                kind="coverage",
                severity="major",
                repair_action="acquire",
                problem=value,
            )
            if isinstance(value, str)
            else value
        )
        problem = " ".join(draft.problem.split())
        if not problem:
            # Defence in depth behind ``CritiqueGapDraft.problem``'s
            # ``min_length``: a gap whose text says nothing is not a gap, and
            # silently dropping it deleted a material defect from the review
            # while the score still counted as an acceptance.
            raise CritiqueContractViolation(
                "gap problem must be non-blank",
                gap_index=len(gaps),
            )
        declared_scope = bool(
            draft.coverage_id
            or draft.target_ids
            or draft.statement_ids
            or draft.claim_cluster_ids
        )
        coverage_id = draft.coverage_id or None
        if coverage_id is not None:
            coverage_id = coverage_id.strip() or None
        if coverage_id not in coverage_ids:
            coverage_id = None
        targets = [
            target_id.strip()
            for target_id in draft.target_ids
            if target_id.strip()
        ]
        if packet is not None:
            targets = [
                target_id
                for target_id in targets
                if target_id in known_target_ids
                or target_id == QUESTION_TARGET_ID
            ]
            if coverage_id is not None and not targets:
                targets = packet.targets_by_coverage_id(coverage_id)
        statements = _known_ids(
            draft.statement_ids, known_statement_ids, packet
        )
        clusters = _known_ids(draft.claim_cluster_ids, known_cluster_ids, packet)
        if declared_scope and not (
            targets or statements or clusters or coverage_id
        ):
            # The gap named ids this packet cannot resolve. Substituting the
            # whole-answer sentinel would turn a hallucinated id into a
            # material obligation about the question, and the sentinel means
            # one specific thing (an original-question omission, paired with
            # ``extend_plan`` by the prompt) — it is not a recovery value. A
            # reply that names records nobody can look up is repaired instead.
            raise CritiqueContractViolation(
                "the gap declared scope ids that do not resolve in this packet",
                gap_index=len(gaps),
            )
        queries = tuple(normalize_notes(draft.recommended_queries))
        violation = gap_contract_problem(
            kind=draft.kind,
            severity=draft.severity,
            repair_action=draft.repair_action,
            coverage_id=coverage_id,
            target_ids=targets,
            statement_ids=statements,
            claim_cluster_ids=clusters,
            recommended_queries=queries,
        )
        if violation is not None:
            raise CritiqueContractViolation(
                violation, gap_index=len(gaps)
            )
        gap = CritiqueGap(
            gap_id=f"gap-{len(gaps) + 1:02d}",
            coverage_id=coverage_id,
            target_ids=targets,
            claim_cluster_ids=clusters,
            statement_ids=statements,
            kind=draft.kind,
            severity=draft.severity,
            repair_action=draft.repair_action,
            problem=problem,
            recommended_queries=list(queries),
        )
        # The same problem restated with a different label is one defect, not
        # two: the identity is the scope, the text, **and what the repair
        # does**. The action and the kind are part of it because they are the
        # routing decision — two gaps that differ only in ``repair_action`` are
        # two nodes' work, and collapsing them kept whichever was more severe,
        # silently adopting one node's repair for the other's defect. Within
        # one action and one scope the most severe reading wins, and an equally
        # severe one carrying more queries is kept because it is the actionable
        # version of the same complaint.
        identity = (
            coverage_id,
            tuple(targets),
            tuple(statements),
            tuple(clusters),
            draft.kind,
            draft.repair_action,
            problem,
        )
        previous = kept.get(identity)
        if previous is None:
            kept[identity] = len(gaps)
            gaps.append(gap)
            continue
        incumbent = gaps[previous]
        if _severity_rank(gap.severity) > _severity_rank(incumbent.severity) or (
            _severity_rank(gap.severity) == _severity_rank(incumbent.severity)
            and len(gap.recommended_queries) > len(incumbent.recommended_queries)
        ):
            gaps[previous] = gap.model_copy(
                update={"gap_id": incumbent.gap_id}
            )
    if len(gaps) > limit:
        # Defence in depth for a draft that never went through the schema: the
        # bound has to refuse the reply, never trim it, or the material gap
        # behind a run of minor ones disappears without a record.
        raise CritiqueContractViolation(
            f"review contains more than {limit} distinct gaps",
            gap_index=limit,
        )
    return gaps


def _severity_rank(severity: str) -> int:
    """``critical`` outranks ``major`` outranks ``minor``; anything else is last."""
    order = {"critical": 3, "major": 2, "minor": 1}
    return order.get(severity, 0)


def _known_ids(
    values: Sequence[str],
    known: Collection[str],
    packet: CriticPacket | None,
) -> list[str]:
    """The cited ids that exist, in order.

    With no packet there is no registry to check against, so the ids are kept
    as they came: a caller reviewing a report outside a packet has nothing to
    validate against, and dropping them would silently discard the only
    targeting information the gap carries.
    """
    retained: list[str] = []
    for value in values:
        item = value.strip()
        if not item or item in retained:
            continue
        if packet is not None and item not in known:
            continue
        retained.append(item)
    return retained


def _gap_is_material(gap: object) -> bool:
    """Whether one gap must be closed before the report is accepted.

    ``minor`` is the one non-material severity. A legacy string gap carries no
    severity and stays material: the pre-Task-8 contract asked for a gap only
    when closing it would change the answer.
    """
    return getattr(gap, "severity", None) != "minor"


def route_decision(
    *,
    score: int,
    gaps: Sequence[CritiqueGap],
    unsupported_claims: Sequence[str],
    iteration: int,
    max_iterations: int,
    has_report: bool,
) -> tuple[bool, str]:
    """Decide routing locally, in a fixed precedence.

    The iteration bound comes first and beats every quality signal. After
    that: a missing report is the most concrete thing to fix, then the
    score threshold, then *material* gaps, then unsupported claims.

    Materiality is severity, not existence. A minor defect is a real finding
    the reader should see, and the historical rule that every listed gap buys
    a whole research pass is what made one wording observation as expensive as
    a missing answer. A gap that declares no severity — a legacy string, or a
    snapshot written before severities existed — stays material, because that
    contract only ever listed gaps worth another pass.
    """
    if iteration >= max_iterations:
        return False, "max_iterations_reached"
    if not has_report:
        return True, "missing_report"
    if score < ACCEPTANCE_SCORE:
        return True, "low_score"
    if [gap for gap in gaps if _gap_is_material(gap)]:
        return True, "critical_gaps"
    if unsupported_claims:
        return True, "unsupported_claims"
    return False, "accepted_quality"


def _rationale(model_rationale: str, *, reason: str) -> str:
    """Extend the model's rationale with the routing reason this run used."""
    text = " ".join(model_rationale.split())
    explanation = ROUTING_REASONS[reason]
    body = f"{text} {explanation}" if text else explanation
    return body[:_RATIONALE_CHARS].rstrip()


def build_critique(
    draft: CritiqueDraft,
    *,
    iteration: int,
    max_iterations: int,
    known_coverage_ids: Collection[str] = (),
    packet: CriticPacket | None = None,
) -> tuple[Critique, str]:
    """Stamp one model review into a validated ``Critique`` and its route.

    ``packet`` is what a gap's ids are validated against. The top-level query
    list is the union of the model's own list and every acquisition gap's
    queries, de-duplicated in order: a query a gap carries is a query the next
    pass must run, and the two lists must not be able to disagree about it.
    """
    score = draft.score
    gaps = normalize_gaps(
        draft.gaps, known_coverage_ids=known_coverage_ids, packet=packet
    )
    unsupported = normalize_notes(draft.unsupported_claims)
    queries = normalize_notes(
        [
            *draft.recommended_queries,
            *(query for gap in gaps for query in gap.recommended_queries),
        ]
    )
    should_continue, reason = route_decision(
        score=score,
        gaps=gaps,
        unsupported_claims=unsupported,
        iteration=iteration,
        max_iterations=max_iterations,
        has_report=True,
    )
    return (
        Critique(
            score=score,
            gaps=gaps,
            unsupported_claims=unsupported,
            recommended_queries=queries,
            should_continue=should_continue,
            rationale=_rationale(draft.rationale, reason=reason),
        ),
        reason,
    )


def fallback_critique(
    *,
    reason: str,
    iteration: int,
    max_iterations: int,
) -> tuple[Critique, str]:
    """Record a review that could not be made, with no invented score.

    A provider outage never buys another research cycle: an outage says
    nothing about the report, and a retry would almost certainly repeat it
    at cost. A missing report does buy one — there is something concrete to
    fix — unless the iteration bound already forbids it.

    ``review_status`` separates the two. An outage produced **no review**, so
    it is recorded ``failed`` and the graph refuses to accept the unreviewed
    report; leaving the default ``reviewed`` made an outage byte-identical to a
    clean acceptance, because the stopped critique's floor score and empty gap
    list are exactly what an accepted one looks like. A missing report is a
    different thing: the policy decision not to review is a real judgement about
    a missing artifact, the gap it records says what is missing, and the run is
    allowed to buy another pass — so that one stays ``reviewed``.
    """
    if reason not in CRITIQUE_FALLBACK_REASONS:
        raise ValueError(f"unknown fallback reason: {reason}")
    if reason == "provider_unavailable":
        should_continue = False
        route = (
            "max_iterations_reached"
            if iteration >= max_iterations
            else reason
        )
        gaps: list[CritiqueGap] = []
    else:
        should_continue, route = route_decision(
            score=MIN_CRITIC_SCORE,
            gaps=[],
            unsupported_claims=[],
            iteration=iteration,
            max_iterations=max_iterations,
            has_report=False,
        )
        gaps = [
            CritiqueGap(
                gap_id="gap-01",
                coverage_id=None,
                target_ids=[QUESTION_TARGET_ID],
                kind="presentation",
                severity="critical",
                repair_action="synthesize",
                problem="No report was available to review.",
                recommended_queries=[],
            )
        ]
    sentences = [ROUTING_REASONS[reason]]
    if route != reason:
        sentences.append(ROUTING_REASONS[route])
    return (
        Critique(
            score=MIN_CRITIC_SCORE,
            gaps=gaps,
            unsupported_claims=[],
            recommended_queries=[],
            should_continue=should_continue,
            rationale=" ".join(sentences),
            review_status=(
                "failed" if reason == "provider_unavailable" else "reviewed"
            ),
        ),
        route,
    )


def failed_critique(
    *,
    iteration: int,
    max_iterations: int,
) -> tuple[Critique, str]:
    """Record that no review exists, without inventing a judgement.

    The reply never validated, so nothing about the report was judged. The
    score is the floor and ``review_status`` says why, the gap list is empty
    *and* the run does not continue — an exhausted repair must never read as
    an acceptance, which is what a high score with no gaps would mean, and
    must never read as a low score, which would claim the report was judged
    poor. Routing stops for the same reason a provider outage does: a schema
    failure says nothing about the report, and another research cycle would
    not fix a malformed reply.
    """
    route = (
        "max_iterations_reached" if iteration >= max_iterations else "review_failed"
    )
    sentences = [ROUTING_REASONS["review_failed"]]
    if route != "review_failed":
        sentences.append(ROUTING_REASONS[route])
    return (
        Critique(
            score=MIN_CRITIC_SCORE,
            gaps=[],
            unsupported_claims=[],
            recommended_queries=[],
            should_continue=False,
            rationale=" ".join(sentences),
            review_status="failed",
        ),
        route,
    )


def _group_errors_by_agent_stage(
    errors: Sequence[ResearchError],
) -> dict[str, list[ResearchError]]:
    """Group typed errors by their safe agent and operation identifiers."""
    groups: dict[str, list[ResearchError]] = {}
    for error in errors:
        operation = error.details.get("operation")
        stage = (
            operation.strip()
            if isinstance(operation, str) and operation.strip()
            else error.error_type
        )
        key = f"{error.source} / {stage}"
        groups.setdefault(key, []).append(error)
    return groups


def _render_quality_snapshot(
    quality: ReportQualitySnapshot | None,
) -> str:
    """Render deterministic quality fields without reducing them to a score."""
    if quality is None:
        return "(no structured quality snapshot was recorded)"
    return json.dumps(
        quality.model_dump(mode="json"), ensure_ascii=False, sort_keys=True
    )


def _render_error_groups(packet: CriticPacket) -> str:
    """Render all typed errors grouped by agent and operation/stage."""
    groups = packet.error_groups or _group_errors_by_agent_stage(packet.errors)
    if not groups:
        return (
            f"{packet.error_count} error(s) were recorded during this pass; "
            "no typed error details were supplied."
        )
    lines = [
        f"{packet.error_count or sum(len(rows) for rows in groups.values())} "
        "error(s) were recorded during this pass."
    ]
    for group in sorted(groups):
        rows = groups[group]
        lines.append(f"- {group}: {len(rows)} error(s)")
        for error in rows:
            severity = "recoverable" if error.recoverable else "fatal"
            coverage_id = error.details.get("coverage_id")
            coverage = (
                f" coverage_id={coverage_id}"
                if isinstance(coverage_id, str) and coverage_id.strip()
                else ""
            )
            lines.append(
                f"  - {error.error_type} ({severity}){coverage}: "
                f"{error.message}"
            )
    return "\n".join(lines)


def _render_critic_claims(claims: Sequence[Claim]) -> str:
    """Render every checked claim, whole, for the Critic's own request.

    The shared ``render_claim_digest`` is built for prompts that need a bounded
    display: it slices to a count and collapses each claim to 240 characters.
    Both are wrong here. The packet fingerprint covers every checked claim, so a
    claim this section omits is inside the review's authority and outside its
    view, and a claim whose own text is cut can hide the half that contradicts
    the report. Nothing in Task 8's review material is summarized.
    """
    lines: list[str] = []
    for position, claim in enumerate(claims, start=1):
        urls = ", ".join(claim.source_urls)
        lines.append(
            f"{position}. [{claim.verdict} {claim.confidence:.2f}] "
            f"{claim.text} ({urls})"
        )
    return "\n".join(lines) or "(no claims were checked)"


def critique_messages(
    task: CritiqueTask,
    run: ReActRun | None = None,
) -> list[ChatMessage]:
    """Build the messages that request one structured review.

    The request renders the packet and nothing else: the frozen question and
    answer contract, every reader section whole, every reader statement with
    its ids, the target inventory with what is still open, the batched read
    excerpts, the deterministic quality snapshot and hard checks, the canonical
    claim verdicts, and the cited source assessments. ``run`` is accepted and
    ignored — the packet carries the evidence — so a caller that still passes
    the finished run keeps working, and no ReAct transcript can reach the
    review.

    Nothing here truncates the report: every reader section is carried whole,
    and a section larger than ``CRITIC_REPORT_CHARS`` says so in its heading
    rather than losing its end. Nor is any other review material bounded: every
    checked claim is rendered whole, every cited source is listed, and every
    registered excerpt is batched. There is no count argument to pass, because a
    count bound here removed information the packet's own fingerprint covers.
    """
    del run
    packet = packet_for_task(task)
    canonical_claims = merge_claim_snapshot([], packet.claims)
    canonical_sources = merge_source_snapshot([], packet.sources)
    # ``render_source_quality`` bounds its display to 36 rows because other
    # prompts legitimately want that; the review does not. The 37th source can
    # be the weak or unscored one behind a load-bearing statement, and it used
    # to disappear without even an omission marker.
    source_quality = render_source_quality(
        canonical_sources, max_sources=max(1, len(canonical_sources))
    )
    sections = [
        f"# Research question\n{packet.question}",
        _fingerprint_section(packet),
        (
            "# Answer contract\n"
            f"{_render_answer_contract(packet.answer_contract)}"
        ),
        (
            "# Reader content\n"
            "The reader report is split into fenced sections below, one per "
            "reader section, each carried in full. Review every one of them; "
            "its own headings belong to the report rather than to this "
            "request. This is the complete candidate, not a prefix."
        ),
        *_render_balanced_report_sections(
            packet.reader_content,
            report_sections=packet.reader_sections,
        ),
        (
            "# Reader statements\n"
            "Every substantive sentence the report prints, as its record: the "
            "id, the reader mode, the claim clusters, the selected evidence "
            "ids, and the targets and dimensions it answers. A gap may cite a "
            "statement id from this list and no other.\n"
            f"{_render_statements(packet)}"
        ),
        (
            "# Sub-topics planned\n"
            f"{_render_sub_topics(packet)}"
        ),
        (
            "# Evidence targets\n"
            "The obligations this pass owed, open ones first, with the "
            "dimensions each already answers.\n"
            f"{_render_targets(packet)}"
        ),
        (
            "# Evidence — batched read excerpts\n"
            "Exact passages of successful reads this run registered. An "
            "excerpt is evidence; a search result, a snippet, or a memory "
            "recall is not, and none of them appears here.\n"
            f"{_render_evidence_batches(packet)}"
        ),
        (
            "# Deterministic quality snapshot\n"
            f"{_render_quality_snapshot(packet.quality)}"
        ),
        (
            "# Hard checks\n"
            "Deterministic integrity results. A failed check is a fact about "
            "the candidate, not a score and not a verdict.\n"
            f"{_render_hard_checks(packet)}"
        ),
        (
            "# Claim verdicts — canonical checked claims\n"
            "Every checked claim, whole: the verdict, the confidence, the "
            "claim's own text, and the sources it cites. A claim this request "
            "does not render is a claim the review cannot judge.\n"
            f"{_render_critic_claims(canonical_claims)}"
        ),
        (
            "# Source quality — cited-source assessments\n"
            "Every source this report cites, with the assessment recorded for "
            "it. A source this request does not list is a source the review "
            "cannot weigh.\n"
            f"{source_quality}"
        ),
        (
            "# Recorded problems by agent/stage\n"
            f"{_render_error_groups(packet)}"
        ),
        f"# Response contract\n{CRITIQUE_INSTRUCTION}",
        f"# How to choose the score\n{_CRITIQUE_SCORE_BANDS}",
        (
            "# Reply format\n"
            "Return exactly one JSON object with these five fields and no "
            "others, with no text before or after it. Two complete examples, "
            "one for a weak report and one for a strong one, showing the scale "
            "in use:\n"
            f"Weak report, score {_LOW_EXAMPLE_SCORE}:\n"
            f"{_CRITIQUE_LOW_EXAMPLE_JSON}\n"
            f"Strong report, score {_HIGH_EXAMPLE_SCORE}:\n"
            f"{_CRITIQUE_HIGH_EXAMPLE_JSON}"
        ),
    ]
    return [
        ChatMessage(role="developer", content=CRITIC_REVIEW_SYSTEM_PROMPT),
        ChatMessage(role="user", content="\n\n".join(sections)),
    ]


def _fingerprint_section(packet: CriticPacket) -> str:
    """Attest which exact packet this request is a review of.

    The line travels in the request so the repair that follows can be shown to
    be about the same text, and it is one bounded digest: nothing about the
    report, the evidence, or the model's reply is added to the request by
    recording it. It sits near the top rather than at the end, because the
    reply format has to be the last thing the model reads.
    """
    return (
        "# Packet fingerprint\n"
        f"Packet fingerprint: {packet.fingerprint}\n"
        "This review, and any repair of it, is of exactly this packet: the "
        "reader content, the statement records, the targets, and the evidence "
        "above and below are the whole of what is being judged."
    )


def critique_repair_messages(
    task: CritiqueTask,
    error: StructuredOutputError | CritiqueContractViolation,
    run: ReActRun | None = None,
) -> list[ChatMessage]:
    """Build the one repair request for an unusable review reply.

    The repair re-asks the *same* packet, so the messages are the original
    request plus the bounded, provider-output-free diagnostics the local
    validation produced: field paths taken from the validation error's own
    locations and a stable category. The rejected payload is never echoed back
    and never logged — it is provider text, and the whole point of the
    diagnostic record is that a schema failure can be described without it.

    A reply can be unusable two ways — malformed for the schema, or
    well-formed and outside the gap contract — and both are described the same
    way, because from the model's side the fix is the same: return the object
    again, with the reported field corrected.
    """
    messages = critique_messages(task, run)
    diagnostics = reply_diagnostics(error)
    lines = [
        f"- {validation_summary(diagnostic)}" for diagnostic in diagnostics
    ] or ["- no bounded diagnostic was available"]
    return [
        messages[0],
        ChatMessage(
            role="user",
            content="\n\n".join(
                [
                    messages[1].content,
                    (
                        "# Repair request\n"
                        "The previous reply to this exact request was not valid "
                        "for the requested schema. Return the same five-field "
                        "JSON object again, corrected. The only thing wrong "
                        "with it was its shape; the report, the evidence, and "
                        "the packet are unchanged, and their fingerprint is "
                        "the one above.\n" + "\n".join(lines)
                    ),
                    f"# Repair instructions\n{CRITIQUE_REPAIR_INSTRUCTION}",
                ]
            ),
        ),
    ]


def schema_diagnostics(
    error: StructuredOutputError,
) -> tuple[StructuredValidationDiagnostic, ...]:
    """The bounded, provider-content-free diagnostics of one schema failure."""
    return tuple(error.validation_diagnostics)


def schema_error_from_validation(error: ValidationError) -> StructuredOutputError:
    """Wrap a local validation failure as the structured error the agent routes.

    The provider raises ``StructuredOutputError`` for a reply its schema
    rejected. A ``ValidationError`` reaching the agent means the reply got
    past the schema and failed a contract validator instead — a failure with
    the same meaning and the same handling, so it is given the same shape
    rather than a second, parallel path.
    """
    return StructuredOutputError(
        "structured output was rejected by the local review contract",
        diagnostics=[
            validation_diagnostic(error, attempt=1, schema=CritiqueDraft)
        ],
    )


def reply_diagnostics(
    error: StructuredOutputError | CritiqueContractViolation,
) -> tuple[StructuredValidationDiagnostic, ...]:
    """The bounded diagnostics of whichever way a reply was unusable."""
    if isinstance(error, CritiqueContractViolation):
        return (
            StructuredValidationDiagnostic(
                attempt=1,
                field_paths=list(error.field_paths()),
                category="other_schema",
            ),
        )
    return schema_diagnostics(error)


def current_packet(
    task: CritiqueTask, state: ResearchState | None
) -> CriticPacket:
    """The packet a repair would be sent, rebuilt from the live state.

    Rebuilding is what makes the fingerprint guard mean something: the packet
    the review was opened on travels with the task, so comparing *that* object
    against its own fingerprint can never fail, while a packet rebuilt from
    the state the run is actually holding reflects any change made to the
    report or the evidence in between. Without a state — ``finalize``'s hook,
    or a direct call in a test — the task's own packet is the only one there
    is, and it is by construction the one that was reviewed.
    """
    if state is None:
        return packet_for_task(task)
    return build_critic_packet(state, state.composition)


def _diagnostic_details(
    diagnostics: Sequence[StructuredValidationDiagnostic],
    *,
    attempts: int,
    fingerprint: str,
) -> dict[str, object]:
    """The bounded schema record: categories, field paths, and the attempt count."""
    categories: list[str] = []
    paths: list[str] = []
    for diagnostic in diagnostics:
        category = diagnostic.category
        if isinstance(category, str) and category and category not in categories:
            categories.append(category)
        for path in diagnostic.field_paths:
            if path not in paths:
                paths.append(path)
    return {
        "operation": "critic_report_review",
        "attempts": attempts,
        "schema_categories": categories,
        "schema_field_paths": paths,
        "packet_fingerprint": fingerprint,
    }


def critique_schema_error(
    diagnostics: Sequence[StructuredValidationDiagnostic],
    *,
    attempts: int,
    fingerprint: str,
) -> ResearchError:
    """Record that the review reply never validated, without its payload.

    Non-recoverable: no review of this report exists. Recorded here rather
    than in ``agent_provider_failure_details`` because this is not a provider
    outage — the provider answered, and what it answered could not be read.
    """
    return agent_error(
        agent_name=CRITIC_NAME,
        error_type="critic_review_schema_error",
        message=(
            f"The model's review did not match the required schema after "
            f"{attempts} attempt(s); the report was not judged and the run was "
            "ended rather than accepting an unreviewed report."
        ),
        recoverable=False,
        details=_diagnostic_details(
            diagnostics, attempts=attempts, fingerprint=fingerprint
        ),
    )


def critique_repaired(
    diagnostics: Sequence[StructuredValidationDiagnostic],
    *,
    attempts: int,
    fingerprint: str,
) -> ResearchError:
    """Record that a malformed review was repaired into a usable one.

    Recoverable: the repair produced a validated review, so the pass is
    reviewable. The record exists because a repaired reply's finish reason is
    ``stop``, exactly like a clean one, and the bounded diagnostics are the
    only trace that the first attempt was rejected.
    """
    return agent_error(
        agent_name=CRITIC_NAME,
        error_type="critic_review_repaired",
        message=(
            "The model's first review reply did not match the required schema; "
            "one repair against the same packet produced a valid review."
        ),
        recoverable=True,
        details=_diagnostic_details(
            diagnostics, attempts=attempts, fingerprint=fingerprint
        ),
    )


class CritiqueRepairRefused(RuntimeError):
    """A repair would not have reviewed the packet the review was opened on."""


class CritiqueContractViolation(RuntimeError):
    """A reply the provider schema accepted does not satisfy the gap contract.

    The second boundary of the same rule. ``CritiqueGapDraft`` refuses a
    violating gap while the reply is being parsed, which is the ordinary path;
    this is the typed failure for a draft that never went through the schema —
    a caller that built one by hand, or a transport that returns objects
    without validating them. It exists so that failure has a name the agent can
    catch and route to the repair path, instead of surfacing as a bare
    ``ValueError`` from deep inside a model validator.
    """

    def __init__(self, problem: str, *, gap_index: int) -> None:
        super().__init__(problem)
        self.problem = problem
        self.gap_index = gap_index

    def field_paths(self) -> tuple[str, ...]:
        """The bounded schema path of the offending gap, provider-free."""
        return (f"gaps.{self.gap_index}",)


def repair_target(
    packet: CriticPacket, *, reviewed_fingerprint: str
) -> CriticPacket:
    """The packet a repair may be built from, or a refusal.

    A repair re-asks the same model about the same text, with the first
    request's authority. If the packet that would be sent no longer carries
    the fingerprint the review was opened on — a caller that rebuilt it after
    the report or the evidence changed — the repair would be a *second*,
    different review wearing the first one's authority, so it is refused and
    the caller records an explicit failed review instead.
    """
    if not reviewed_fingerprint:
        raise CritiqueRepairRefused(
            "no packet fingerprint was recorded for this review, so a repair "
            "cannot be shown to be about the same packet"
        )
    if packet.fingerprint != reviewed_fingerprint:
        raise CritiqueRepairRefused(
            "the report/evidence packet changed between the review and its "
            "repair"
        )
    return packet


def critique_provider_error(error: Exception) -> ResearchError:
    """Record that the review call could not reach the provider.

    Non-recoverable: no review of this report exists. The run still ends
    with a routing decision, because a graph with no critique cannot route
    at all.
    """
    return agent_error(
        agent_name=CRITIC_NAME,
        error_type="critic_review_provider_error",
        message=(
            "The model provider failed while the report was reviewed; the "
            "research pass was ended rather than repeated."
        ),
        recoverable=False,
        details=agent_provider_failure_details(CRITIC_REVIEW_OPERATION, error),
    )


def review_output_limit_retry(
    error: Exception,
    *,
    reasoning_effort: str,
    max_tokens: int,
    outcome: str,
) -> ResearchError:
    """Record that a truncated review call was re-asked at another effort.

    The retry is a second paid request, so it is recorded even when it works:
    without the record, "this review took two calls at two efforts" is
    unrecoverable from the artifacts, and a run that spent an extra call looks
    exactly like one that did not. Recoverable either way — a retry that
    recovers produced the review, and a retry that truncates leaves the run an
    honest statement that no review exists.

    The call, the effort, the budget it kept, and what the retry returned are in
    the *message* rather than only in ``details``. Details are published only
    for the error types whose projection this project has vetted, and a reader
    who cannot see the effort cannot tell a retry from a repeat.
    """
    if outcome not in REVIEW_RETRY_OUTCOMES:
        raise ValueError(f"unknown retry outcome: {outcome!r}")
    return agent_error(
        agent_name=CRITIC_NAME,
        error_type="critic_review_output_limit_retry",
        message=(
            f"The {CRITIC_REVIEW_OPERATION} review call was truncated by the "
            f"output limit; it was re-asked once at reasoning_effort "
            f"{reasoning_effort} with the same {max_tokens}-token output "
            "budget, and "
            + (
                "the retry returned a reply."
                if outcome == "answered"
                else "the retry was truncated as well, so no review exists "
                "from it."
            )
        ),
        recoverable=True,
        details=agent_provider_failure_details(
            CRITIC_REVIEW_OPERATION,
            error,
            attempt=2,
            reasoning_effort=reasoning_effort,
            max_tokens=max_tokens,
            outcome=outcome,
        ),
    )


def review_unavailable(error: Exception, *, max_tokens: int) -> ResearchError:
    """Record that no review of this report exists, without ending the run.

    Recoverable by construction: the run continues to publication with the
    report it has and no judgement of it. Nothing about the report is claimed —
    the critique is absent rather than floored, so the graph cannot accept an
    unreviewed report and cannot route a refinement from it.
    """
    return agent_error(
        agent_name=CRITIC_NAME,
        error_type="critic_review_unavailable",
        message=(
            "The report was never judged: both review calls were truncated by "
            "the output limit, so this run publishes its report unscored "
            "rather than ending."
        ),
        recoverable=True,
        details=agent_provider_failure_details(
            CRITIC_REVIEW_OPERATION,
            error,
            truncated_calls=2,
            max_tokens=max_tokens,
        ),
    )


def missing_report_error() -> ResearchError:
    """Warn that there was no report to review."""
    return agent_error(
        agent_name=CRITIC_NAME,
        error_type="critic_missing_report",
        message="No report was available to review.",
    )


def critique_started_event(
    *,
    iteration: int,
    max_iterations: int,
    claim_count: int,
    has_report: bool,
) -> ResearchEvent:
    """Announce that the review began, before any provider call."""
    return agent_event(
        agent_name=CRITIC_NAME,
        event_type="critic.critique.started",
        message="Report review started.",
        metadata={
            "iteration": iteration,
            "max_iterations": max_iterations,
            "claim_count": claim_count,
            "has_report": has_report,
        },
    )


def critique_completed_event(
    critique: Critique,
    run: ReActRun,
    *,
    reason: str,
    iteration: int,
    max_iterations: int,
) -> ResearchEvent:
    """Report the score, the counts, and the routing recommendation.

    ``reason`` is a ``ROUTING_REASONS`` key, never provider text, so a
    consumer can group runs by *why* they continued or stopped rather than
    parsing a rationale.
    """
    return agent_event(
        agent_name=CRITIC_NAME,
        event_type="critic.critique.completed",
        message="Report review complete.",
        metadata={
            "score": critique.score,
            "review_status": critique.review_status,
            "gap_count": len(critique.gaps),
            "unsupported_claim_count": len(critique.unsupported_claims),
            "recommended_query_count": len(critique.recommended_queries),
            "should_continue": critique.should_continue,
            "reason": reason,
            "iteration": iteration,
            "max_iterations": max_iterations,
            "tool_calls": run.tool_calls,
            "stop_reason": run.stop_reason,
        },
    )


def critique_unavailable_event(
    *,
    reason: str,
    iteration: int,
    max_iterations: int,
    tool_calls: int,
    stop_reason: str,
) -> ResearchEvent:
    """Report that the review produced no judgement, and why.

    The counterpart of ``critique_completed_event`` for the one path that ends
    with no critique at all. There is no score, no gap count, and no
    recommendation, so this event carries the enumerated reason and the loop's
    own facts instead of null-valued fields a consumer could read as a
    judgement. ``reason`` is a ``ROUTING_REASONS`` key, never provider text.
    """
    return agent_event(
        agent_name=CRITIC_NAME,
        event_type="critic.critique.unavailable",
        message="Report review produced no judgement.",
        metadata={
            "reason": reason,
            "iteration": iteration,
            "max_iterations": max_iterations,
            "tool_calls": tool_calls,
            "stop_reason": stop_reason,
        },
    )


class CriticAgent(BaseAgent[Critique]):
    """Review the exact candidate and recommend a route.

    No tools, and the frozen ``prompt_version`` says so: the historical
    spot-check loop could search but could not open a page, so its snippets
    could not establish anything about support. What replaced it is the packet
    — the complete reader content, every statement record, and the registered
    read excerpts — so the only request this agent makes is the structured
    review and, if that reply is malformed, one repair of it.
    """

    name = CRITIC_NAME
    description = "Judge the report and recommend whether research continues."
    allowed_tools: ClassVar[tuple[str, ...]] = ()
    prompt_version: ClassVar[str] = "critic-2"
    """The prompt and reply contract this agent's requests are versioned under.

    Re-pinned with Task 8, deliberately: the system prompt lost every tool
    instruction, the response contract gained the typed gap object, and both
    examples were rewritten, so a Task 7-era request and a Task 8 one are
    different requests whose artifacts must not compare equal. The call
    fingerprint already includes this value, which is why the version is the
    one place the change has to be recorded rather than a comment.
    """

    def __init__(
        self,
        *,
        provider: AgentCompleter,
        tracker: Tracker,
        scratchpad: ScratchpadMemory,
        tools: Sequence[BaseTool] = (),
        config: AgentRuntimeConfig | None = None,
        model_profile: EffectiveModelConfig | None = None,
    ) -> None:
        super().__init__(
            provider=provider,
            tracker=tracker,
            scratchpad=scratchpad,
            tools=tools,
            config=config,
            model_profile=model_profile,
        )

    @property
    def output_schema(self) -> type[Critique]:
        """The validated critique. Never sent to the provider.

        ``review`` asks for ``CritiqueDraft`` instead, because ``Critique``
        carries ``CriticScore`` bounds and a non-blank rationale that do not
        survive strict JSON schema conversion. Do not route this agent
        through ``complete_output``.
        """
        return Critique

    def system_prompt(self, task: AgentTask) -> str:
        del task
        return CRITIC_SYSTEM_PROMPT

    def build_task(self, state: ResearchState) -> CritiqueTask:
        """Bind this review to the exact packet it judges."""
        packet = build_critic_packet(state, state.composition)
        return CritiqueTask(
            instruction=state.original_question,
            packet=packet,
            report=packet.reader_content,
            iteration=state.iteration,
            max_iterations=state.max_iterations,
            claims=list(packet.claims),
            sources=list(packet.sources),
            sub_topics=list(packet.sub_topics),
            error_count=packet.error_count,
            quality=packet.quality,
            report_sections=dict(packet.reader_sections),
            errors=list(packet.errors),
            error_groups=dict(packet.error_groups),
        )

    async def review(
        self,
        task: CritiqueTask,
        run: ReActRun | None = None,
        *,
        state: ResearchState | None = None,
    ) -> tuple[Critique | None, str, list[ResearchError], bool]:
        """Judge one candidate from its packet.

        Returns ``(critique, reason, errors, provider_failed)``. No provider
        call is made when there is no report, so a score is never invented
        over an empty review. Any unusable reply — malformed for the schema, or
        well-formed but outside the gap contract — gets exactly one repair,
        which re-reads the packet this review was opened on; when that too
        fails, the review is recorded as failed with its bounded diagnostics
        rather than scored.

        An output-limit truncation is the one failure a different request can
        fix, so it is re-asked once at a lower effort under the same output
        budget. If that is truncated too, ``critique`` is ``None``: no
        judgement of this report exists, and the run continues without one
        instead of ending. A floor-scored fallback critique would be
        indistinguishable from an accepted one — the same score floor and the
        same empty gap list — so the absence is what the graph routes on.

        ``state`` is the state this review was built from. The repair path uses
        it to rebuild the packet and prove the repair is about the same text,
        so an in-flight change to the report or the evidence refuses the repair
        instead of silently reviewing something else.
        """
        packet = packet_for_task(task)
        if not packet.reader_content.strip():
            critique, reason = fallback_critique(
                reason="missing_report",
                iteration=task.iteration,
                max_iterations=task.max_iterations,
            )
            return critique, reason, [missing_report_error()], False

        opened = packet.fingerprint
        messages = critique_messages(task, run)
        truncations: list[ProviderOutputLimitError] = []
        for position, effort in enumerate(REVIEW_ATTEMPT_EFFORTS):
            last_attempt = position + 1 == len(REVIEW_ATTEMPT_EFFORTS)
            try:
                draft = await self._complete_review(
                    messages, reasoning_effort=effort
                )
                critique, reason = build_critique(
                    draft,
                    iteration=task.iteration,
                    max_iterations=task.max_iterations,
                    packet=packet,
                )
            except (StructuredOutputError, CritiqueContractViolation) as error:
                repaired = await self._repair_review(
                    task,
                    error,
                    run,
                    state=state,
                    reviewed_fingerprint=opened,
                )
                return self._with_retry_records(repaired, truncations)
            except ValidationError as error:
                # A reply that never went through the schema — the provider
                # bound objects, or a caller built a draft by hand. Same route
                # as a malformed one, with the same bounded diagnostic.
                repaired = await self._repair_review(
                    task,
                    schema_error_from_validation(error),
                    run,
                    state=state,
                    reviewed_fingerprint=opened,
                )
                return self._with_retry_records(repaired, truncations)
            except ProviderOutputLimitError as error:
                truncations.append(error)
                if last_attempt:
                    return self._unavailable_review(task, truncations)
                continue
            except ProviderError as error:
                critique, reason = fallback_critique(
                    reason="provider_unavailable",
                    iteration=task.iteration,
                    max_iterations=task.max_iterations,
                )
                return critique, reason, [critique_provider_error(error)], True
            return (
                critique,
                reason,
                self._retry_records(truncations, "answered"),
                False,
            )
        raise AssertionError("a review attempt must produce a critique or a route")

    def _retry_records(
        self,
        truncations: Sequence[ProviderOutputLimitError],
        outcome: str,
    ) -> list[ResearchError]:
        """The retry record for a review that was re-asked, or nothing.

        Empty when the first attempt was not truncated, so an ordinary review
        adds nothing to the run's records. Reached by every exit that can follow
        a retry — the review, a reply the repair then had to fix, and a second
        truncation — so the retry is a fact in the artifacts whichever way it
        went.
        """
        if not truncations:
            return []
        return [
            review_output_limit_retry(
                truncations[0],
                reasoning_effort=OUTPUT_LIMIT_RETRY_EFFORT,
                max_tokens=self.config.critic_review_max_tokens,
                outcome=outcome,
            )
        ]

    def _with_retry_records(
        self,
        reviewed: tuple[Critique, str, list[ResearchError], bool],
        truncations: Sequence[ProviderOutputLimitError],
    ) -> tuple[Critique, str, list[ResearchError], bool]:
        """A repaired review's own result, with the retry record in front.

        The retry happened before the repair did, so it belongs first in the
        list a reader walks in order — and it must survive the repair, or a
        retry that led somewhere is a call nobody can see. The outcome is
        ``answered``: the retry's reply arrived and the repair is what did the
        rest, which the repair's own record states.
        """
        critique, reason, errors, provider_failed = reviewed
        return (
            critique,
            reason,
            [*self._retry_records(truncations, "answered"), *errors],
            provider_failed,
        )

    def _unavailable_review(
        self,
        task: CritiqueTask,
        truncations: Sequence[ProviderOutputLimitError],
    ) -> tuple[None, str, list[ResearchError], bool]:
        """Continue without a review, and record that none exists.

        The output-limit rule's last step, reached only when the retry was
        truncated too. The report is complete and publishable and the run does
        not end: what is missing is a judgement of it, and the graph already
        has that state — no critique, nothing acceptable, nothing to route a
        refinement from. This is deliberately *not* the provider-failure
        fallback: that one records a failed review with a floor score, which
        the graph turns into ``critique_failed`` and a failed run, and it is
        the right reading of an outage that says nothing about the request.
        Two truncations of the same request are a statement about the request,
        and the honest response is to publish the report unscored.
        """
        cap = self.config.critic_review_max_tokens
        return (
            None,
            "review_unavailable",
            [
                *self._retry_records(truncations, "truncated"),
                review_unavailable(truncations[-1], max_tokens=cap),
            ],
            True,
        )

    async def _complete_review(
        self,
        messages: Sequence[ChatMessage],
        *,
        reasoning_effort: str | None = None,
    ) -> CritiqueDraft:
        """One structured review request under this operation's output budget.

        The reply is validated here whatever its Python type is. A provider that
        returns a payload returns a dict; an adapter that returns objects — or a
        test double — returns something whose type is right and whose fields may
        never have been checked, because ``model_construct`` and its relatives
        skip every validator. Re-validating the dumped object is what makes "one
        trustworthy validation boundary" true of the *values* rather than only
        of the transport, and it costs one pass over a small payload.

        ``reasoning_effort`` is the retry's own setting and is ``None`` on the
        first attempt, which keeps the request each ordinary review sends
        exactly as it was. The output budget is the operation's configured cap
        on both attempts: the rule is one retry, never a larger cap.
        """
        self.fingerprint_call(
            "CritiqueDraft",
            output_limit=self.config.critic_review_max_tokens,
            reasoning_effort=reasoning_effort,
        )
        reply = await self.provider.complete_structured(
            messages,
            CritiqueDraft,
            agent_name=self.name,
            max_tokens=self.config.critic_review_max_tokens,
            reasoning_effort=reasoning_effort,
        )
        payload = (
            reply.model_dump(mode="python")
            if isinstance(reply, CritiqueDraft)
            else reply
        )
        return CritiqueDraft.model_validate(payload)

    async def _repair_review(
        self,
        task: CritiqueTask,
        error: StructuredOutputError | CritiqueContractViolation,
        run: ReActRun | None,
        *,
        state: ResearchState | None = None,
        reviewed_fingerprint: str = "",
    ) -> tuple[Critique, str, list[ResearchError], bool]:
        """Re-ask the same packet once, or record an explicit failed review."""
        diagnostics = reply_diagnostics(error)
        packet = packet_for_task(task)
        try:
            repair_target(
                current_packet(task, state),
                reviewed_fingerprint=reviewed_fingerprint,
            )
        except CritiqueRepairRefused as refusal:
            critique, reason = failed_critique(
                iteration=task.iteration,
                max_iterations=task.max_iterations,
            )
            return (
                critique,
                reason,
                [
                    critique_schema_error(
                        diagnostics,
                        attempts=1,
                        fingerprint=reviewed_fingerprint,
                    ),
                    agent_error(
                        agent_name=CRITIC_NAME,
                        error_type="critic_review_repair_refused",
                        message=str(refusal),
                        recoverable=False,
                        details={"operation": CRITIC_REVIEW_OPERATION},
                    ),
                ],
                # No provider call failed here: the repair was refused locally,
                # so the run summary must not report a provider error.
                False,
            )

        try:
            draft = await self._complete_review(
                critique_repair_messages(task, error, run)
            )
            critique, reason = build_critique(
                draft,
                iteration=task.iteration,
                max_iterations=task.max_iterations,
                packet=packet,
            )
        except CritiqueContractViolation as violation:
            # The repair arrived and still broke the contract: one repair is
            # the whole allowance, so this is an explicit failed review.
            return self._exhausted_review(
                task,
                [*diagnostics, *reply_diagnostics(violation)],
                fingerprint=reviewed_fingerprint,
                extra=[],
            )
        except ValidationError as failure:
            wrapped = schema_error_from_validation(failure)
            return self._exhausted_review(
                task,
                [*diagnostics, *reply_diagnostics(wrapped)],
                fingerprint=reviewed_fingerprint,
                extra=[],
            )
        except ProviderError as failure:
            # The repair itself can fail two ways: the second reply was
            # malformed too, or the provider went away mid-repair. Both mean no
            # review exists, and both are recorded: the bounded schema record
            # (with every attempt's diagnostics) always, and the provider
            # failure when that is what happened, so the ledger does not blame
            # the schema for an outage.
            repair_diagnostics = (
                [*diagnostics, *reply_diagnostics(failure)]
                if isinstance(failure, StructuredOutputError)
                else list(diagnostics)
            )
            extra = (
                []
                if isinstance(failure, StructuredOutputError)
                else [critique_provider_error(failure)]
            )
            return self._exhausted_review(
                task,
                repair_diagnostics,
                fingerprint=reviewed_fingerprint,
                extra=extra,
            )

        return (
            critique,
            reason,
            [
                critique_repaired(
                    diagnostics,
                    attempts=CRITIC_REVIEW_ATTEMPTS,
                    fingerprint=reviewed_fingerprint,
                )
            ],
            False,
        )

    def _exhausted_review(
        self,
        task: CritiqueTask,
        diagnostics: Sequence[StructuredValidationDiagnostic],
        *,
        fingerprint: str,
        extra: Sequence[ResearchError],
    ) -> tuple[Critique, str, list[ResearchError], bool]:
        """The one repair is spent: record a failed review, never a score.

        ``provider_failed`` is ``True``: the review aborted because no usable
        reply ever arrived, and a caller reading ``react.succeeded`` must not
        see ``finished`` over that. A refusal *before* any repair request is
        the one local case, and it returns ``False`` from its own branch.
        """
        critique, reason = failed_critique(
            iteration=task.iteration,
            max_iterations=task.max_iterations,
        )
        return (
            critique,
            reason,
            [
                critique_schema_error(
                    diagnostics,
                    attempts=CRITIC_REVIEW_ATTEMPTS,
                    fingerprint=fingerprint,
                ),
                *extra,
            ],
            True,
        )

    async def finalize(
        self,
        task: AgentTask,
        run: ReActRun,
    ) -> Critique | None:
        """Adapt ``review`` to the ``BaseAgent`` hook.

        ``run`` calls ``review`` directly so it can keep the routing reason
        and the errors this hook signature has nowhere to return.
        """
        if not isinstance(task, CritiqueTask):
            raise AgentConfigurationError(
                "CriticAgent.finalize requires a CritiqueTask"
            )
        critique, _, _, _ = await self.review(task, run)
        return critique

    def state_update(
        self,
        result: Critique | None,
        run: ReActRun,
    ) -> ResearchStateUpdate:
        """The critique and errors only. ``run`` adds the progress events.

        The critique key is written even when there is no critique. An omitted
        key means "unchanged" to the state merge, so a review that produced
        nothing would leave an *earlier* pass's judgement in place — and the
        router reads that judgement, which is how an unavailable review would
        come back as an acceptance. Writing ``None`` is what makes "no
        judgement was made this pass" a fact about this pass.
        """
        return {"errors": list(run.errors), "critique": result}

    async def run(self, state: ResearchState) -> AgentRun[Critique]:
        """Review the packet, then score and route.

        No tool loop runs and none can: the agent declares no tools, so its
        toolset is empty and the only provider request in this method is the
        structured review. The synthesizer's own work is reviewed as this
        pass left it — the exact candidate, not an abridged earlier draft.
        """
        task = self.build_task(state)
        has_report = bool(task.report.strip())
        events: list[ResearchEvent] = [
            critique_started_event(
                iteration=task.iteration,
                max_iterations=task.max_iterations,
                claim_count=len(task.claims),
                has_report=has_report,
            )
        ]
        errors: list[ResearchError] = []

        async with self.tracker.agent_span(self.name) as span:
            react = ReActRun(agent_name=self.name, stop_reason="finished")
            critique, reason, review_errors, provider_failed = await self.review(
                task, react, state=state
            )
            errors.extend(review_errors)
            if provider_failed:
                # Mirror the loop-level provider_error path so a caller
                # reading react.succeeded never sees "finished" over an
                # abort that happened during the review.
                react = react.model_copy(update={"stop_reason": "provider_error"})
            react = react.model_copy(update={"errors": errors})
            events.append(
                (
                    critique_completed_event(
                        critique,
                        react,
                        reason=reason,
                        iteration=task.iteration,
                        max_iterations=task.max_iterations,
                    )
                    if critique is not None
                    else critique_unavailable_event(
                        reason=reason,
                        iteration=task.iteration,
                        max_iterations=task.max_iterations,
                        tool_calls=react.tool_calls,
                        stop_reason=react.stop_reason,
                    )
                )
            )
            span.set_outputs(
                {
                    "agent_name": self.name,
                    "score": None if critique is None else critique.score,
                    "review_status": (
                        "unavailable"
                        if critique is None
                        else critique.review_status
                    ),
                    "gap_count": 0 if critique is None else len(critique.gaps),
                    "should_continue": (
                        False if critique is None else critique.should_continue
                    ),
                    "reason": reason,
                    "packet_fingerprint": (
                        task.packet.fingerprint if task.packet else ""
                    ),
                    "tool_calls": react.tool_calls,
                    "stop_reason": react.stop_reason,
                }
            )

        return AgentRun(
            agent_name=self.name,
            result=critique,
            react=react,
            errors=errors,
            state_update={
                **self.state_update(critique, react),
                "events": events,
            },
            call_fingerprints=dict(self._call_fingerprints),
        )
