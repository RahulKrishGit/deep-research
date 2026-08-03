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

from deep_research.agents.base import AgentRun, BaseAgent, StructuredCompleter
from deep_research.agents.errors import AgentConfigurationError, agent_error
from deep_research.agents.events import agent_event
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
from deep_research.agents.steps import ReActRun, summarize_text
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import Tracker
from deep_research.providers import ChatMessage, OpenAIProviderError
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


# Enumerated, project-generated reasons a composed report never reached
# disk. Never provider or filesystem text: these reach
# ResearchError.details.
WRITE_FAILURE_REASONS = {
    "tool_failed": "The write_document tool reported a failure.",
    "malformed_result": "The write_document tool returned no usable path.",
}


def report_provider_error(error: Exception) -> ResearchError:
    """Record that the report call could not reach the provider.

    Non-recoverable, mirroring ``researcher.extraction_provider_error``: no
    prose exists for this pass. The report is still composed and still
    saved — the skeleton is rendered from recorded evidence — so this is a
    quality failure, not a lost report.
    """
    return agent_error(
        agent_name=SYNTHESIZER_NAME,
        error_type="synthesizer_report_provider_error",
        message=(
            "The model provider failed while the report was written; the "
            "report was assembled from recorded evidence alone."
        ),
        recoverable=False,
        details={"exception_type": type(error).__name__},
    )


def invalid_section_error(rejected: Sequence[str]) -> ResearchError:
    """Warn that some drafted sections or citations were dropped."""
    return agent_error(
        agent_name=SYNTHESIZER_NAME,
        error_type="synthesizer_invalid_section",
        message=(
            "Some drafted report sections were malformed or cited sources "
            "that were never retrieved."
        ),
        details={"rejected": list(rejected)},
    )


def report_not_written_error(*, reason: str) -> ResearchError:
    """Warn that the composed report could not be written to disk.

    Recoverable: ``state.report`` still carries the Markdown, so nothing is
    lost but the artifact.
    """
    explanation = WRITE_FAILURE_REASONS.get(reason)
    if explanation is None:
        raise ValueError(f"unknown write failure reason: {reason}")
    return agent_error(
        agent_name=SYNTHESIZER_NAME,
        error_type="synthesizer_report_not_written",
        message=(
            f"{explanation} The report is still available in research state."
        ),
        details={"reason": reason},
    )


def memory_save_error(*, failures: int, attempted: int) -> ResearchError:
    """Warn that some high-confidence claims were not kept for later."""
    return agent_error(
        agent_name=SYNTHESIZER_NAME,
        error_type="synthesizer_memory_save_failed",
        message=(
            "Some high-confidence claims could not be saved to long-term "
            "memory; this report is unaffected."
        ),
        details={"failures": failures, "attempted": attempted},
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
    """Report the counts the spec requires of this agent.

    ``limitations`` carries enumerated ``LIMITATION_REASONS`` keys, never
    prose, so a consumer can group on them.
    """
    return agent_event(
        agent_name=SYNTHESIZER_NAME,
        event_type="synthesizer.synthesis.completed",
        message="Report synthesis complete.",
        metadata={
            "section_count": report.section_count,
            "citation_count": report.citation_count,
            "source_appendix_count": report.source_count,
            "output_path": report.path,
            "saved_findings": report.saved_findings,
            "report_chars": len(report.markdown),
            "claim_count": claim_count,
            "limitations": list(limitations),
        },
    )


class SynthesizerAgent(BaseAgent[SynthesizedReport]):
    """Compose the final report, write it, and keep what is worth keeping.

    Runs no ReAct loop: the report is one structured call, and both tool
    calls are unconditional consequences of having produced it. ``run`` is
    overridden for the same reason ``SourceEvaluatorAgent`` overrides it —
    the shared single-loop ``BaseAgent.run`` cannot express this shape.
    """

    name = SYNTHESIZER_NAME
    description = "Write the final report from verified claims and sources."
    allowed_tools = ("write_document", "save_to_memory")

    def __init__(
        self,
        *,
        provider: StructuredCompleter,
        tracker: Tracker,
        scratchpad: ScratchpadMemory,
        tools: Sequence[BaseTool] = (),
        config: AgentRuntimeConfig | None = None,
        max_sections: int = DEFAULT_MAX_SECTIONS,
        finding_digest: int = SYNTHESIS_FINDING_DIGEST,
        claim_digest: int = SYNTHESIS_CLAIM_DIGEST,
        memory_confidence: float = DEFAULT_MEMORY_CONFIDENCE,
        max_memory_findings: int = DEFAULT_MAX_MEMORY_FINDINGS,
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
        if not 0.0 <= memory_confidence <= 1.0:
            raise ValueError("memory_confidence must be in [0.0, 1.0]")
        if max_memory_findings < 0:
            raise ValueError("max_memory_findings must not be negative")
        self._max_sections = max_sections
        self._finding_digest = finding_digest
        self._claim_digest = claim_digest
        self._memory_confidence = memory_confidence
        self._max_memory_findings = max_memory_findings

    @property
    def output_schema(self) -> type[SynthesizedReport]:
        """The composed report. Never sent to the provider.

        ``draft_report`` asks for ``ReportDraft`` instead, because the
        report record carries constraints that do not survive strict JSON
        schema conversion. Do not route this agent through
        ``complete_output``.
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
            claims=list(state.verified_claims),
            sources=list(state.evaluated_sources),
            findings=list(state.raw_findings),
            limitations=limitation_reasons(state),
        )

    def _require_tool(self, name: str) -> BaseTool:
        tool = self.toolset.get(name)
        if tool is None:
            raise AgentConfigurationError(f"{name} was not injected")
        return tool

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
        except OpenAIProviderError as error:
            return None, [report_provider_error(error)], True
        return draft, [], False

    async def write_report(
        self,
        task: SynthesisTask,
        markdown: str,
    ) -> tuple[str | None, list[ResearchError]]:
        """Write the composed report, tolerating a failing filesystem."""
        tool = self._require_tool("write_document")
        result = await tool.execute(
            filename=report_filename(
                session_id=task.session_id, iteration=task.iteration
            ),
            content=markdown,
        )
        if not result.success:
            return None, [report_not_written_error(reason="tool_failed")]
        data = result.data
        path = data.get("path") if isinstance(data, dict) else None
        if not isinstance(path, str) or not path:
            return None, [report_not_written_error(reason="malformed_result")]
        return path, []

    async def save_findings(
        self,
        task: SynthesisTask,
    ) -> tuple[int, list[ResearchError]]:
        """Keep the confident, verified claims for future sessions."""
        selected = high_confidence_claims(
            task.claims, threshold=self._memory_confidence
        )[: self._max_memory_findings]
        if not selected:
            return 0, []
        tool = self._require_tool("save_to_memory")
        saved = 0
        failures = 0
        for claim in selected:
            content, metadata = memory_payload(
                claim, session_id=task.session_id
            )
            result = await tool.execute(content=content, metadata=metadata)
            if result.success:
                saved += 1
            else:
                failures += 1
        errors = (
            [memory_save_error(failures=failures, attempted=len(selected))]
            if failures
            else []
        )
        return saved, errors

    async def finalize(
        self,
        task: AgentTask,
        run: ReActRun,
    ) -> SynthesizedReport | None:
        """Adapt drafting and composition to the ``BaseAgent`` hook.

        ``run`` calls the pieces directly so it can keep the errors this
        hook signature has nowhere to return, and so the writes happen once.
        """
        del run
        if not isinstance(task, SynthesisTask):
            raise AgentConfigurationError(
                "SynthesizerAgent.finalize requires a SynthesisTask"
            )
        draft, _, provider_failed = await self.draft_report(task)
        summary, sections, notes, limitations, _ = self._compose_inputs(
            task, draft, provider_failed=provider_failed
        )
        return compose_report(
            task,
            summary=summary,
            sections=sections,
            uncertainty_notes=notes,
            limitations=limitations,
        )

    def _compose_inputs(
        self,
        task: SynthesisTask,
        draft: ReportDraft | None,
        *,
        provider_failed: bool,
    ) -> tuple[str, list[ReportSection], str, list[str], list[ResearchError]]:
        """Turn a draft (or its absence) into everything ``compose_report`` needs."""
        limitations = list(task.limitations)
        if provider_failed:
            limitations.append("report_generation_failed")
        if draft is None:
            return REPORT_SUMMARY_FALLBACK, [], "", limitations, []
        known = [
            citation.url
            for citation in build_citation_index(task.sources, task.claims)
        ]
        sections, rejected = build_report_sections(
            draft, known_urls=known, max_sections=self._max_sections
        )
        errors = [invalid_section_error(rejected)] if rejected else []
        summary = draft.executive_summary.strip() or REPORT_SUMMARY_FALLBACK
        return summary, sections, draft.uncertainty_notes, limitations, errors

    def state_update(
        self,
        result: SynthesizedReport | None,
        run: ReActRun,
    ) -> ResearchStateUpdate:
        """The report and errors only. ``run`` adds the progress events."""
        update: ResearchStateUpdate = {"errors": list(run.errors)}
        if result is not None:
            update["report"] = result.markdown
        return update

    async def run(self, state: ResearchState) -> AgentRun[SynthesizedReport]:
        """Draft, compose, write, and keep — recording every count.

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
            (
                summary,
                sections,
                notes,
                limitations,
                section_errors,
            ) = self._compose_inputs(task, draft, provider_failed=provider_failed)
            errors.extend(section_errors)
            report = compose_report(
                task,
                summary=summary,
                sections=sections,
                uncertainty_notes=notes,
                limitations=limitations,
            )
            path, write_errors = await self.write_report(task, report.markdown)
            errors.extend(write_errors)
            saved, memory_errors = await self.save_findings(task)
            errors.extend(memory_errors)
            report = report.model_copy(
                update={"path": path, "saved_findings": saved}
            )
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
                    "output_path": report.path,
                    "saved_findings": report.saved_findings,
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
