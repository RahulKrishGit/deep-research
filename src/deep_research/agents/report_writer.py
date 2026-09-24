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
from deep_research.agents.figures import quantities_in, same_quantity
from deep_research.agents.identity import finding_fingerprint
from deep_research.agents.planner import Clock, utc_now
from deep_research.agents.prompts import AgentTask, render_structured_reply_format
from deep_research.agents.report import (
    figure_label,
    render_finding_log,
    render_written_report,
    report_as_of,
    report_scope,
    written_citations,
)
from deep_research.agents.sources import publisher_identity
from deep_research.agents.steps import ReActRun
from deep_research.agents.verified_facts import (
    answered_target_ids,
    citable_findings,
    fact_rows,
    not_found_targets,
    release_text,
)
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
    FigureContext,
    NotFoundTarget,
    RejectedDraftPoint,
    ReportComposition,
    ReportPoint,
    ReportSection,
    ReportStatement,
    ResearchError,
    ResearchEvent,
    ResearchState,
    ResearchStateUpdate,
    ScoredSource,
    SubTopic,
)

REPORT_WRITER_NAME = "report_writer"
DEFAULT_MAX_SECTIONS = 4
MAX_POINT_CHARS = 600
_SECTION_TITLE_CHARS = 120
# F10: the first attempt runs at the resolved profile's effort (config.yaml
# model_overrides.report_writer, the one effort source); a truncated draft is
# asked once more at high, the synthesizer's measured retry.
_WRITER_ATTEMPT_EFFORTS: tuple[str | None, ...] = (None, OUTPUT_LIMIT_RETRY_EFFORT)

REPORT_WRITER_SYSTEM_PROMPT = (
    "You write the reader-facing prose of a research report from verified findings. "
    "Every finding you may cite is listed with a label (F01, F02, ...), its verbatim "
    "snippet, and each of its figures with the figure's verified period, kind (actual "
    "or forecast), organisation and reader label. Code builds the key facts table, the "
    "Not found list and the sources; you write the executive summary and a few short "
    "explanatory sections."
)

REPORT_WRITER_INSTRUCTION = (
    "Rules:\n"
    "- Cite by label only: every point lists in finding_labels the one to three labels it "
    "rests on. Never write a URL.\n"
    "- State a forecast with a forecast verb (\"projects\", \"expects\", \"forecasts\"), never "
    "as a completed outcome.\n"
    "- Use only the numbers and dates of the cited findings.\n"
    "- Use the scope words the finding states (\"utility-scale\", \"all segments\"), never "
    "the question's.\n"
    "- Name only organisations and publications the cited findings name.\n"
    "- For a figure one site relays from another organisation, name the organisation and "
    "the site (\"according to Wood Mackenzie, as reported by Utility Dive\").\n"
    "- The executive summary answers each part of the question directly, first: the actual "
    "figure the question asks for; then each organisation's latest forecast with its "
    "release; then later actuals, labelled as actuals. One point per fact; never state the "
    "same figure twice.\n"
    "- At most four sections, explaining segment basis, revisions, units or definitions, "
    "only as the findings state them.\n"
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
    max_iterations: int = 0          # Task 4.1 renames this max_extra_passes
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


class WrittenReport(ContractModel):
    markdown: str
    evidence_markdown: str
    composition: ReportComposition
    statement_count: int = Field(ge=0)
    citation_count: int = Field(ge=0)
    refused_count: int = Field(ge=0)


def finding_registry(findings: Sequence[Finding], targets: Sequence[EvidenceTarget]) -> list[tuple[str, Finding]]:
    """One label per citable finding: answers to required targets first, then the rest."""
    citable = citable_findings(findings)
    answered = answered_target_ids(citable, [t for t in targets if t.required])
    first = list(dict.fromkeys(fid for ids in answered.values() for fid in ids))
    rank = {fid: n for n, fid in enumerate(first)}
    ordered = sorted(citable, key=lambda f: rank.get(finding_fingerprint(f), len(rank)))
    return [(f"F{n:02d}", finding) for n, finding in enumerate(ordered, start=1)]


def _figure_label_for(finding: Finding, context: FigureContext) -> str:
    return figure_label(
        organisation=context.organisation, attribution=context.attribution,
        relay_host=publisher_identity(finding.source_url) if context.attribution == "relayed" else None,
        kind=context.kind, release=release_text(finding),
        unchecked=bool(finding.verification and finding.verification.context_unchecked),
    )


def registry_lines(label: str, finding: Finding) -> list[str]:
    lines = [f"## {label}: {finding.source_title} ({publisher_identity(finding.source_url)})",
             f"snippet: {finding.snippet or finding.content}"]
    number = 0
    for result in finding.verification.figure_results if finding.verification else []:
        if not result.kept or result.context is None:
            continue
        number += 1
        context = result.context
        lines.append(
            f"{label} | figure {number}: {result.figure.value} {result.figure.unit} | period "
            f"{context.period or 'not stated'} | kind {context.kind} | organisation "
            f"{context.organisation} | label: {_figure_label_for(finding, context)}"
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


def writer_messages(task: ReportWriterTask) -> list[ChatMessage]:
    targets = "\n".join(
        f"- {t.target_id}: {t.question} (" + _answering_labels(task, t.target_id) + ")"
        for t in task.targets if t.required
    ) or "(none)"
    registry = "\n\n".join("\n".join(registry_lines(label, f)) for label, f in task.registry) or "(none)"
    user = "\n\n".join([
        f"# Question\n{task.question}",
        f"# Required targets and the findings that answer them\n{targets}",
        f"# Verified findings\n{registry}",
        f"# Rules\n{REPORT_WRITER_INSTRUCTION}",
        "# Reply format\n" + render_structured_reply_format(_WRITER_REPLY_EXAMPLES),
    ])
    return [ChatMessage(role="developer", content=REPORT_WRITER_SYSTEM_PROMPT),
            ChatMessage(role="user", content=user)]


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
    by a code pattern.
    """
    by_label = dict(task.registry)
    ids = {label: finding_fingerprint(f) for label, f in task.registry}
    numbers = iter(range(1, 10_000))
    rejected: list[RejectedDraftPoint] = []

    def consider(point: WriterPointDraft, where: str) -> _Candidate | None:
        drafted = " ".join(point.text.split())
        wanted = [label.strip() for label in point.finding_labels]

        def refuse(reason: str) -> None:
            rejected.append(RejectedDraftPoint(
                where=where, text=drafted, finding_labels=list(point.finding_labels), reason=reason,
            ))

        if not drafted:
            refuse("empty text")
            return None
        if len(drafted) > MAX_POINT_CHARS:
            refuse(f"longer than {MAX_POINT_CHARS} characters")
            return None
        unknown = [label for label in wanted if label not in by_label]
        if unknown:
            refuse("unknown labels: " + ", ".join(unknown))
            return None
        if not wanted:
            refuse("cites no checked finding")
            return None
        return _Candidate(key=f"S{next(numbers):03d}", where=where, text=drafted,
                          finding_labels=wanted, findings=[by_label[label] for label in wanted])

    summary_candidates = [c for n, d in enumerate(draft.executive_summary if draft else [])
                          if (c := consider(d, f"summary[{n}]")) is not None]
    section_candidates: list[tuple[str, list[_Candidate]]] = []
    for s, section in enumerate((draft.sections if draft else [])[:DEFAULT_MAX_SECTIONS]):
        points = [c for n, d in enumerate(section.points)
                  if (c := consider(d, f"sections[{s}].points[{n}]")) is not None]
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
        from deep_research.agents.evidence_verifier import StatementCheckItem, check_statements
        items = [
            StatementCheckItem(label=c.key, text=c.text, findings=c.findings,
                               labels=[_finding_label(f) for f in c.findings])
            for c in all_candidates
        ]
        try:
            verdicts, check_errors = await check_statements(
                provider, items, question=task.question, fingerprint=fingerprint,
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
        stated = quantities_in(text)
        rows = {row.row_id for row in task.facts
                if (row.finding_id in cited_ids or cited_ids & set(row.duplicate_finding_ids))
                and any(same_quantity(r, s) for r in quantities_in(row.value) for s in stated)}
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

    return ReportComposition(
        question=task.question, session_id=task.session_id, iteration=task.iteration,
        max_iterations=task.max_iterations, as_of=task.as_of, scope=task.scope,
        sub_topics=list(task.sub_topics), sources=list(task.sources), findings=list(task.findings),
        summary=summary, sections=sections, rejected=[r.reason for r in rejected],
        rejected_points=rejected, fact_rows=list(task.facts), not_found=list(task.not_found),
        finding_labels={label: finding_id for label, finding_id in ids.items()},
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
        answered = answered_target_ids(citable_findings(findings), targets)
        return ReportWriterTask(
            instruction=state.original_question,
            session_id=state.session_id,
            iteration=state.iteration,
            max_iterations=state.max_iterations,
            question=state.original_question,
            as_of=report_as_of(findings=findings, reads=list(state.read_records.values())),
            scope=report_scope(state.sub_topics),
            generated_on=self._clock().date().isoformat(),
            sub_topics=list(state.sub_topics),
            targets=targets,
            findings=findings,
            sources=list(state.evaluated_sources),
            registry=finding_registry(findings, targets),
            facts=fact_rows(findings, targets),
            not_found=not_found_targets(state.sub_topics, answered, state.acquisition_state_by_target),
            answered=answered,
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
