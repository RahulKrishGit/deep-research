"""The Synthesizer: turn checked evidence into the final Markdown report.

Like every other agent here, the provider is asked for a constraint-free
draft (``ReportDraft``) and never for a domain type. The report's skeleton —
citations, the verified-claim list, the uncertainty grouping, the
limitations block, and the source appendix — is rendered locally by
``agents.report`` from recorded evidence, so the required sections exist and
every verified claim is cited even when the model call fails.

This module runs no ReAct loop. Report generation is one structured call,
and ``write_document`` / ``save_to_memory`` are deterministic writes made
afterwards, not decisions handed to a model.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import Field, JsonValue

from deep_research.agents.prompts import (
    REPORT_INSTRUCTION,
    SYNTHESIZER_SYSTEM_PROMPT,
    AgentTask,
    render_claim_digest,
    render_finding_digest,
    render_source_quality,
)
from deep_research.agents.report import (
    ReportSection,
    assemble_report,
    build_citation_index,
    render_limitations,
)
from deep_research.agents.sources import normalize_source_url
from deep_research.agents.steps import summarize_text
from deep_research.providers import ChatMessage
from deep_research.utils.types import (
    Claim,
    ContractModel,
    Finding,
    ResearchState,
    ScoredSource,
)

SYNTHESIZER_NAME = "synthesizer"

DEFAULT_MAX_SECTIONS = 8
SYNTHESIS_FINDING_DIGEST = 40
SYNTHESIS_CLAIM_DIGEST = 40

# A claim must be verified *and* at least this confident before it is kept
# for future sessions. The spec says "high-confidence final findings"
# without defining either bound; this is the only definition this codebase
# can compute.
DEFAULT_MEMORY_CONFIDENCE = 0.7
DEFAULT_MAX_MEMORY_FINDINGS = 10

REPORT_SUMMARY_FALLBACK = (
    "No executive summary was written for this pass. The claims, sources, "
    "and limitations recorded below are the whole of what this research "
    "established."
)

_SECTION_BODY_CHARS = 4000
_GUIDANCE_CHARS = 200
# Characters kept verbatim in a report filename. Narrow on purpose:
# WriteDocumentTool rejects absolute paths and traversal segments, and a
# rejected write would lose the report.
_FILENAME_SAFE = frozenset("abcdefghijklmnopqrstuvwxyz0123456789-")


class ReportSectionDraft(ContractModel):
    """One model-written narrative section, before validation.

    Declares no ``Field`` constraints for the same reason as
    ``planner.SubTopicDraft``: it is converted to a strict OpenAI JSON
    schema.
    """

    title: str
    body: str
    source_urls: list[str]


class ReportDraft(ContractModel):
    """The provider-facing report schema for one synthesis pass."""

    executive_summary: str
    sections: list[ReportSectionDraft]
    uncertainty_notes: str


class SynthesisTask(AgentTask):
    """An ``AgentTask`` bound to the evidence its report is written from.

    Carrying the evidence on the task is what lets ``finalize(task, run)``
    compose a report without the agent holding mutable state across await
    points — the same reason ``SourceEvaluationTask`` exists.
    """

    session_id: str = Field(min_length=1)
    iteration: int = Field(default=0, ge=0)
    claims: list[Claim] = []
    sources: list[ScoredSource] = []
    findings: list[Finding] = []
    limitations: list[str] = []


class SynthesizedReport(ContractModel):
    """The report ``SynthesizerAgent`` produces, with its own counts.

    Never sent to the provider — ``ReportDraft`` is. Do not route this agent
    through ``complete_output``. ``path`` is ``None`` when the report was
    composed but could not be written to disk; ``markdown`` is authoritative
    either way.
    """

    markdown: str = ""
    path: str | None = None
    section_count: int = Field(default=0, ge=0)
    citation_count: int = Field(default=0, ge=0)
    source_count: int = Field(default=0, ge=0)
    saved_findings: int = Field(default=0, ge=0)


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
    elif any(source.low_confidence for source in state.evaluated_sources):
        reasons.append("low_confidence_sources")
    if not any(claim.verdict == "verified" for claim in state.verified_claims):
        reasons.append("no_verified_claims")
    if any(claim.verdict == "contradicted" for claim in state.verified_claims):
        reasons.append("contradicted_claims")
    return reasons


def report_filename(*, session_id: str, iteration: int) -> str:
    """Return a traversal-free ``.md`` filename for one report.

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


def _clamp_body(text: str, *, limit: int) -> str:
    """Clamp a section body without collapsing its paragraph breaks.

    ``summarize_text`` is deliberately not used here: it joins on
    whitespace, which would turn a multi-paragraph section into one line.
    """
    body = text.strip()
    if len(body) <= limit:
        return body
    return body[: limit - 3].rstrip() + "..."


def build_report_sections(
    draft: ReportDraft,
    *,
    known_urls: Sequence[str],
    max_sections: int,
) -> tuple[list[ReportSection], list[str]]:
    """Keep the sections that carry prose, naming everything dropped.

    A URL the model attached that nobody retrieved is dropped rather than
    cited: an invented citation is the one failure this project refuses to
    put in a report. Rejection reasons are generated here and never copied
    from provider output, so they are safe for ``ResearchError.details``.
    """
    if max_sections < 1:
        raise ValueError("max_sections must be at least 1")
    allowed = {normalize_source_url(url) for url in known_urls}
    sections: list[ReportSection] = []
    rejected: list[str] = []
    for position, item in enumerate(draft.sections, start=1):
        title = " ".join(item.title.split())
        body = item.body.strip()
        if not title or not body:
            rejected.append(f"section {position}: blank title or body")
            continue
        urls: list[str] = []
        dropped = 0
        for raw in item.source_urls:
            url = normalize_source_url(raw)
            if url in allowed:
                if url not in urls:
                    urls.append(url)
            else:
                dropped += 1
        if dropped:
            rejected.append(
                f"section {position}: {dropped} source url(s) not in evidence"
            )
        if len(sections) >= max_sections:
            rejected.append(f"section {position}: past the section cap")
            continue
        sections.append(
            ReportSection(
                title=title,
                body=_clamp_body(body, limit=_SECTION_BODY_CHARS),
                source_urls=urls,
            )
        )
    return sections, rejected


def high_confidence_claims(
    claims: Sequence[Claim],
    *,
    threshold: float,
) -> list[Claim]:
    """Verified claims confident enough to keep for future sessions."""
    return [
        claim
        for claim in claims
        if claim.verdict == "verified" and claim.confidence >= threshold
    ]


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
            f"- {summarize_text(gap, limit=_GUIDANCE_CHARS)}"
            for gap in critique.gaps
        )
    if critique.unsupported_claims:
        lines.append("Statements the reviewer found unsupported:")
        lines.extend(
            f"- {summarize_text(claim, limit=_GUIDANCE_CHARS)}"
            for claim in critique.unsupported_claims
        )
    return "\n".join(lines)


def compose_report(
    task: SynthesisTask,
    *,
    summary: str,
    sections: Sequence[ReportSection],
    uncertainty_notes: str,
    limitations: Sequence[str],
) -> SynthesizedReport:
    """Assemble the report and record the counts observability needs."""
    index = build_citation_index(task.sources, task.claims)
    markdown = assemble_report(
        question=task.instruction,
        summary=summary,
        sections=sections,
        claims=task.claims,
        sources=task.sources,
        index=index,
        limitations=limitations,
        uncertainty_notes=uncertainty_notes,
    )
    return SynthesizedReport(
        markdown=markdown,
        section_count=len(sections),
        citation_count=len(index),
        source_count=len(task.sources),
    )


def report_messages(
    task: SynthesisTask,
    *,
    finding_digest: int,
    claim_digest: int,
) -> list[ChatMessage]:
    """Build the messages that request one structured report draft."""
    sections = [f"## Research question\n{task.instruction}"]
    if task.guidance.strip():
        sections.append(f"## Context\n{task.guidance}")
    sections.extend(
        [
            (
                "## Verified and checked claims\n"
                f"{render_claim_digest(list(task.claims)[:claim_digest])}"
            ),
            (
                "## Retrieved findings\n"
                f"{render_finding_digest(list(task.findings)[:finding_digest])}"
            ),
            f"## Source quality\n{render_source_quality(task.sources)}",
            f"## Known limitations\n{render_limitations(task.limitations)}",
            f"## Response contract\n{REPORT_INSTRUCTION}",
        ]
    )
    return [
        ChatMessage(role="developer", content=SYNTHESIZER_SYSTEM_PROMPT),
        ChatMessage(role="user", content="\n\n".join(sections)),
    ]
