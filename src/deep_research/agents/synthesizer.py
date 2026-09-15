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

from collections.abc import Mapping, Sequence

from pydantic import Field, JsonValue

from deep_research.agents.base import AgentCompleter, AgentRun, BaseAgent
from deep_research.agents.errors import (
    AgentConfigurationError,
    agent_error,
    agent_provider_failure_details,
)
from deep_research.agents.events import agent_event
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
    reader_citations,
    render_evidence_ledger,
    render_limitations,
    render_reader_report,
    report_as_of,
    report_scope,
)
from deep_research.agents.sources import normalize_source_url
from deep_research.agents.steps import ReActRun, summarize_text
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import Tracker
from deep_research.providers import ChatMessage, ProviderError
from deep_research.tools.base import BaseTool
from deep_research.utils.config import AgentRuntimeConfig
from deep_research.utils.types import (
    Claim,
    ContractModel,
    Finding,
    ResearchError,
    ResearchEvent,
    ResearchState,
    ResearchStateUpdate,
    ScoredSource,
    SubTopic,
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

_POINT_CHARS = 600
_SECTION_TITLE_CHARS = 120
_GUIDANCE_CHARS = 200

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
    """

    text: str
    claim_ids: list[str]
    source_urls: list[str]


class ConstraintDraft(ContractModel):
    """One ranked constraint row, before validation.

    A constraint is a claim-linked point plus the two decision columns the
    reader report prints. ``deployment_mechanism`` and ``geography`` are
    provider-attested prose in the Task 6 contract: no typed evidence field
    locally proves their semantic contents. The local validator therefore
    checks only the row's claim and source links, while the prompt requires
    ``not stated`` when the supplied evidence does not say.
    """

    constraint: str
    deployment_mechanism: str
    geography: str
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
        '["https://evidence.example.test/report"]}],"ranked_constraints":'
        '[{"constraint":"Charge for road use inside the measured zone.",'
        '"deployment_mechanism":"area licence with camera enforcement",'
        '"geography":"not stated","claim_ids":["C001"],"source_urls":'
        '["https://evidence.example.test/report"]}],"sections":[{"title":'
        '"Measured result","points":[{"text":"The example study reports the '
        'measured result and its stated limits.","claim_ids":["C001"],'
        '"source_urls":["https://evidence.example.test/report"]}]}],'
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
    """

    session_id: str = Field(min_length=1)
    iteration: int = Field(default=0, ge=0)
    max_iterations: int = Field(default=0, ge=0)
    as_of: str = ""
    scope: str = ""
    sub_topics: list[SubTopic] = []
    claims: list[Claim] = []
    sources: list[ScoredSource] = []
    findings: list[Finding] = []
    limitations: list[str] = []
    errors: list[ResearchError] = []
    # The exact bounded registry shown to the provider.  The full ``claims``
    # snapshot remains available for the evidence ledger, but settled draft
    # points may resolve labels only through this prompt-visible subset.
    claim_packet: list[tuple[str, Claim]] | None = None


class SynthesizedReport(ContractModel):
    """The two composed artifacts, with their own counts.

    Never sent to the provider — ``ReportDraft`` is. Do not route this agent
    through ``complete_output``. ``markdown`` and ``evidence_markdown`` are
    authoritative; ``path`` and ``evidence_path`` name the files the terminal
    finalizer publishes, so ``path`` stays ``None`` until publication exists
    and synthesis itself writes nothing at all.
    """

    markdown: str = ""
    path: str | None = None
    evidence_markdown: str = ""
    evidence_path: str | None = None
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
    # fits.  This includes labels, verdict syntax, rendered text truncation,
    # URLs, coverage, separators, and the omission notice.
    for size in range(maximum, -1, -1):
        packet = ranked[:size]
        omitted = len(ranked) - size
        rendered = render_report_claim_packet(packet, omitted=omitted)
        if len(rendered) <= budget_chars:
            return packet, omitted

    # The minimum fallback check above makes this defensive return unreachable.
    return [], len(ranked)


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


def _build_point(
    *,
    text: str,
    labels: Sequence[str],
    urls: Sequence[str],
    approved: Mapping[str, Claim],
    where: str,
    rejected: list[str],
) -> ReportPoint | None:
    """Validate one drafted point against the checked-claim registry.

    Returns ``None`` and appends a project-generated reason when the point
    cannot be printed: no text, no known checked claim, no source URL, or a
    URL the claims it names do not carry. Reasons never quote provider text,
    so they are safe for ``ResearchError.details`` and for the ledger.
    """
    if not text.strip():
        rejected.append(f"{where}: blank statement")
        return None
    claims, unknown = _resolve_claims(labels, approved=approved)
    if not claims:
        rejected.append(f"{where}: no known checked claim")
        return None
    if unknown:
        rejected.append(f"{where}: {unknown} claim id(s) outside the registry")
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
        rejected.append(f"{where}: {invented} source url(s) not on those claims")
        return None
    if not accepted:
        rejected.append(f"{where}: no source url for a settled statement")
        return None
    return ReportPoint(
        text=summarize_text(text, limit=_POINT_CHARS),
        claim_ids=[claim.claim_id for claim in claims],
        source_urls=accepted,
    )


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


def _build_constraint(
    draft: ConstraintDraft,
    *,
    approved: Mapping[str, Claim],
    where: str,
    rejected: list[str],
) -> ReportConstraint | None:
    point = _build_point(
        text=draft.constraint,
        labels=draft.claim_ids,
        urls=draft.source_urls,
        approved=approved,
        where=where,
        rejected=rejected,
    )
    if point is None:
        return None
    return ReportConstraint(
        text=point.text,
        claim_ids=point.claim_ids,
        source_urls=point.source_urls,
        deployment_mechanism=_optional_text(
            draft.deployment_mechanism, limit=_SECTION_TITLE_CHARS
        ),
        geography=_optional_text(draft.geography, limit=_SECTION_TITLE_CHARS),
    )


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
    drafted statements were refused and why.
    """
    if max_sections < 1:
        raise ValueError("max_sections must be at least 1")
    prompt_registry = (
        task.claim_packet
        if task.claim_packet is not None
        else claim_registry(task.claims)
    )
    approved = {label: claim for label, claim in prompt_registry}
    rejected: list[str] = []
    summary: list[ReportPoint] = []
    constraints: list[ReportConstraint] = []
    sections: list[ReportSection] = []
    notes: list[str] = []
    if draft is not None:
        seen: set[tuple[str, tuple[str, ...], tuple[str, ...]]] = set()
        summary = _build_points(
            draft.executive_summary,
            approved=approved,
            where="executive summary",
            rejected=rejected,
            seen=seen,
        )
        constraints = _build_constraints(
            draft.ranked_constraints, approved=approved, rejected=rejected
        )
        if len(draft.sections) > max_sections:
            rejected.append(
                f"{len(draft.sections) - max_sections} section(s) past the "
                "section cap"
            )
        for position, item in enumerate(draft.sections[:max_sections], start=1):
            title = " ".join(item.title.split())
            if not title:
                rejected.append(f"section {position}: blank title")
                continue
            points = _build_points(
                item.points,
                approved=approved,
                where=f"section {position}",
                rejected=rejected,
                seen=seen,
            )
            if not points:
                rejected.append(f"section {position}: no printable point")
                continue
            sections.append(ReportSection(title=title, points=points))
        notes = [
            summarize_text(note, limit=_POINT_CHARS)
            for note in draft.uncertainty_notes
            if note.strip()
        ]
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
        uncertainty_notes=notes,
        rejected=rejected,
    )
    return composition, rejected


def _build_points(
    drafts: Sequence[ReportPointDraft],
    *,
    approved: Mapping[str, Claim],
    where: str,
    rejected: list[str],
    seen: set[tuple[str, tuple[str, ...], tuple[str, ...]]],
) -> list[ReportPoint]:
    """Validate a list of drafted points, refusing repeats of an earlier one."""
    points: list[ReportPoint] = []
    for position, item in enumerate(drafts, start=1):
        point = _build_point(
            text=item.text,
            labels=item.claim_ids,
            urls=item.source_urls,
            approved=approved,
            where=f"{where} point {position}",
            rejected=rejected,
        )
        if point is None:
            continue
        key = (
            point.text,
            tuple(point.claim_ids),
            tuple(point.source_urls),
        )
        if key in seen:
            rejected.append(f"{where} point {position}: repeats an earlier point")
            continue
        seen.add(key)
        points.append(point)
    return points


def _build_constraints(
    drafts: Sequence[ConstraintDraft],
    *,
    approved: Mapping[str, Claim],
    rejected: list[str],
) -> list[ReportConstraint]:
    rows: list[ReportConstraint] = []
    seen: set[tuple[str, tuple[str, ...], tuple[str, ...]]] = set()
    for position, item in enumerate(drafts, start=1):
        row = _build_constraint(
            item,
            approved=approved,
            where=f"constraint {position}",
            rejected=rejected,
        )
        if row is None:
            continue
        key = (row.text, tuple(row.claim_ids), tuple(row.source_urls))
        if key in seen:
            rejected.append(f"constraint {position}: repeats an earlier row")
            continue
        seen.add(key)
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
            (
                "# Checked claims to cite\n"
                f"{render_report_claim_packet(packet, omitted=omitted)}"
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
        max_sections: int = DEFAULT_MAX_SECTIONS,
        finding_digest: int = SYNTHESIS_FINDING_DIGEST,
        claim_digest: int = SYNTHESIS_CLAIM_DIGEST,
    ) -> None:
        super().__init__(
            provider=provider,
            tracker=tracker,
            scratchpad=scratchpad,
            tools=tools,
            config=config,
        )
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
                findings=state.raw_findings, events=state.events
            ),
            scope=report_scope(state.sub_topics),
            sub_topics=list(state.sub_topics),
            claims=list(state.verified_claims),
            sources=list(state.evaluated_sources),
            findings=list(state.raw_findings),
            limitations=limitation_reasons(state),
            errors=list(state.errors),
        )

    async def draft_report(
        self,
        task: SynthesisTask,
    ) -> tuple[ReportDraft | None, list[ResearchError], bool]:
        """Ask the model for the report's prose.

        Makes no provider call when there is no evidence at all, so the
        writing step can never invent a report out of nothing. The third
        element is ``True`` only when the call itself failed.
        """
        if not task.claims and not task.sources and not task.findings:
            return None, [no_evidence_error()], False
        try:
            draft = await self.provider.complete_structured(
                report_messages(
                    task,
                    finding_digest=self._finding_digest,
                    claim_digest=self._claim_digest,
                ),
                ReportDraft,
                agent_name=self.name,
            )
        except ProviderError as error:
            return None, [report_provider_error(error)], True
        return draft, [], False

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
        finalizer publishes, and it is the only writer.
        """
        update: ResearchStateUpdate = {"errors": list(run.errors)}
        if result is not None:
            update["report"] = result.markdown
            update["report_evidence"] = result.evidence_markdown
            update["evidence_path"] = result.evidence_path
            update["unique_source_count"] = result.unique_source_count
            update["unique_claim_count"] = result.unique_claim_count
        return update

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
            report, compose_errors = self.compose(
                task, draft, provider_failed=provider_failed
            )
            errors.extend(compose_errors)
            events.append(
                synthesis_completed_event(
                    report,
                    limitations=list(task.limitations)
                    + (["report_generation_failed"] if provider_failed else []),
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
        )
