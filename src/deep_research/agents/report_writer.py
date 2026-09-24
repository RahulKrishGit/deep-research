"""The Report Writer (spec §6): prose from verified findings only, cited by label.

The model writes the executive summary and a few short sections, citing
findings by the labels one registry stamps. Code builds everything else -- the
key facts table, duplicates and revisions, the Not found list, the labels and
the sources -- and checks every sentence against the cited findings' verified
fields before it can reach the reader (§6.2).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from pydantic import Field, JsonValue, ValidationError

from deep_research.agents.base import (
    OUTPUT_LIMIT_RETRY_EFFORT,
    AgentCompleter,
    AgentRun,
    BaseAgent,
)
from deep_research.agents.errors import AgentConfigurationError, agent_error
from deep_research.agents.events import agent_event
from deep_research.agents.evidence import cosmetic_text
from deep_research.agents.figures import dates_in, quantities_in, same_quantity, without_dates
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
    VerifiedFigure,
    answered_target_ids,
    citable_findings,
    fact_rows,
    not_found_targets,
    release_text,
    untraced_numbers,
    verified_figures,
)
from deep_research.agents.wording import (
    clause_around,
    hardened_modality,
    hedge_forecast,
    page_modal,
    stated_role,
    stated_scopes,
    stated_years,
    unattested_names,
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

# R4: grid-scale and utility-scale name the same segment (verified_facts folds
# them the same way for target answering); a sentence in either spelling is
# supported by a finding stated in the other, and neither narrows the other.
_SCOPE_EQUIVALENTS: dict[str, str] = {"grid-scale": "utility-scale"}

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
    "- Every number you write must be a figure of a finding the point cites, with its unit "
    "as listed (\"10.4 GW\"). Do not add, subtract, convert or round figures.\n"
    "- Name only organisations, dates and scopes that the cited findings' figures, labels or "
    "snippets state.\n"
    "- Use the scope words the finding states (\"utility-scale\", \"all segments\"), never "
    "the question's.\n"
    "- State an actual as what happened (\"added\", \"installed\"). State a forecast as a "
    "forecast of its organisation (\"EIA expects\", \"Wood Mackenzie projects\") and give its "
    "release when the label shows one.\n"
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
    geographies: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class PointCheck:
    reasons: tuple[str, ...]
    forecast_as_fact: bool      # True: the one hedge rewrite may repair it (F3)
    organisation: str | None
    marker: str                 # the page's own modal for the rewrite, or ""


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


def _attested_corpus(cited: Sequence[Finding], geographies: Sequence[str],
                     sources: Sequence[ScoredSource] = ()) -> str:
    parts = list(geographies)
    urls = {finding.source_url for finding in cited}
    for source in sources:   # PD-25: the Source Evaluator's validated title and issuer
        if source.url in urls:
            issuer = source.identity_anchors.get("issuer")
            parts += [source.title, *(issuer if isinstance(issuer, list) else [issuer or ""])]
    for finding in cited:
        parts += [finding.snippet or "", finding.source_title, publisher_identity(finding.source_url),
                  release_text(finding) or "", finding.attributed_issuer or ""]
        for result in finding.verification.figure_results if finding.verification else []:
            if result.kept and result.context is not None:
                parts += [result.context.organisation, result.context.period or "",
                          result.context.scope or "", result.evidence_words or ""]
    return "\n".join(part for part in parts if part)


def _canonical_scopes(text: str) -> set[str]:
    """R4: ``stated_scopes`` folded through the grid-scale/utility-scale equivalence."""
    return {_SCOPE_EQUIVALENTS.get(term, term) for term in stated_scopes(text)}


def check_point(text: str, cited: Sequence[Finding], *, geographies: Sequence[str],
                sources: Sequence[ScoredSource] = ()) -> PointCheck:
    """§6.2's guards for one sentence against the findings it cites."""
    if not cited:
        return PointCheck(("cites no checked finding",), False, None, "")
    reasons: list[str] = []
    untraced = untraced_numbers(text, cited)       # dates are not numbers (F2)
    if untraced:
        reasons.append("numbers not among the cited figures: " + ", ".join(untraced))
    corpus = _attested_corpus(cited, geographies, sources)
    names = unattested_names(text, corpus.casefold(), corpus)
    if names:
        reasons.append("names the cited findings do not carry: " + ", ".join(names))
    # F2: a date is checked whole against the findings (a release the writer
    # copies from a label is there); the years rule reads the rest of the text.
    folded = cosmetic_text(corpus)
    dates = [date for date in dates_in(text) if cosmetic_text(date) not in folded]
    if dates:
        reasons.append("dates the cited findings do not carry: " + ", ".join(dates))
    years = [year for year in stated_years(without_dates(text)) if year not in corpus]
    if years:
        reasons.append("years the cited findings do not carry: " + ", ".join(years))
    normalised = cosmetic_text(text)
    stated = quantities_in(text)
    figures = [f for f in verified_figures(cited) if f.quantity is not None]
    stated_figures = [(q, f) for q in stated for f in figures if same_quantity(q, f.quantity)]
    scope_sources = [f"{f.context.scope or ''} {_evidence_words(f)}" for _, f in stated_figures] or [corpus]
    attested_scopes = _canonical_scopes(" ".join(scope_sources))
    unsupported = [s for s in stated_scopes(text) if _SCOPE_EQUIVALENTS.get(s, s) not in attested_scopes]
    if unsupported:
        reasons.append("scope not carried by the cited figures: " + ", ".join(unsupported))
    as_fact: list[str] = []
    for quantity, figure in stated_figures:
        role = stated_role(clause_around(normalised, quantity.start))
        if figure.context.kind == "forecast" and role == "actual":
            as_fact.append(figure.context.organisation)
        elif figure.context.kind == "actual" and role == "forecast":
            reasons.append("an actual stated as a forecast")
        elif role == "mixed":
            reasons.append("one clause reads as both forecast and outcome")
    hardened = hardened_modality(text, corpus)
    forecasts = [f for _, f in stated_figures if f.context.kind == "forecast"]
    # F3: the one rewrite repairs a forecast stated as fact or with hardened
    # modality, and only when nothing else is wrong with the sentence.
    repairable = bool(forecasts) and bool(as_fact or hardened) and not reasons
    if as_fact:
        reasons.append("a forecast stated as fact")
    if hardened:
        reasons.append(f"asserts with '{hardened}' what the page hedges")
    if repairable:
        organisation = as_fact[0] if as_fact else forecasts[0].context.organisation
        marker = page_modal(" ".join(_evidence_words(f) for f in forecasts))
        return PointCheck(tuple(reasons), True, organisation, marker)
    return PointCheck(tuple(reasons), False, None, "")


def _evidence_words(figure: VerifiedFigure) -> str:
    results = figure.finding.verification.figure_results if figure.finding.verification else []
    words = results[figure.index].evidence_words if figure.index < len(results) else None
    return words or (figure.finding.snippet or "")


def compose_written_report(task: ReportWriterTask, draft: ReportWriterDraft | None) -> ReportComposition:
    """Check every drafted point, rewrite a forecast-as-fact once, and compose (§6.1-6.2)."""
    by_label = dict(task.registry)
    ids = {label: finding_fingerprint(f) for label, f in task.registry}
    rejected: list[RejectedDraftPoint] = []
    stated_rows: set[str] = set()
    numbers = iter(range(1, 10_000))

    def build(point: WriterPointDraft, where: str, summary: bool) -> ReportPoint | None:
        text = " ".join(point.text.split())[:MAX_POINT_CHARS]
        wanted = [label.strip() for label in point.finding_labels]

        def refuse(reason: str) -> None:
            rejected.append(RejectedDraftPoint(where=where, text=text, finding_labels=list(point.finding_labels), reason=reason))

        if not text:
            return refuse("empty text")
        unknown = [label for label in wanted if label not in by_label]
        if unknown:
            return refuse("unknown labels: " + ", ".join(unknown))
        cited = [by_label[label] for label in wanted]
        check = check_point(text, cited, geographies=task.geographies, sources=task.sources)
        if check.forecast_as_fact and check.organisation:
            text = hedge_forecast(text, check.organisation, marker=check.marker)
            # F3: the rewrite is the one-time, defined fix for exactly a
            # forecast stated as fact and a hardened modality; the clause the
            # rewritten "according to ...'s forecast" text (or a restored
            # page modal such as "could") lands in is not always the clause
            # the quantity itself sits in (a comma can split them), so
            # re-deriving those two reasons from scratch on the rewritten
            # text can spuriously re-flag the very defect the rewrite just
            # repaired. Only a reason the rewrite itself introduced -- one
            # outside the two the repair targets -- still refuses it.
            reasons = tuple(
                reason for reason in check_point(text, cited, geographies=task.geographies,
                                                 sources=task.sources).reasons
                if not reason.startswith(("a forecast stated as fact", "asserts with '"))
            )
        else:
            reasons = check.reasons
        if reasons:
            return refuse("; ".join(reasons))
        cited_ids = {ids[label] for label in wanted}
        stated = quantities_in(text)
        rows = {row.row_id for row in task.facts
                if (row.finding_id in cited_ids or cited_ids & set(row.duplicate_finding_ids))
                and any(same_quantity(r, s) for r in quantities_in(row.value) for s in stated)}
        if summary and rows and rows <= stated_rows:
            return refuse("restates " + ", ".join(sorted(rows)))
        stated_rows.update(rows)
        own_first = sorted(cited, key=lambda f: 0 if any(
            r.context is not None and r.context.attribution == "own"
            for r in (f.verification.figure_results if f.verification else [])) else 1)
        statement = ReportStatement(
            statement_id=f"S{next(numbers):03d}", text=text, finding_ids=[ids[label] for label in wanted],
            target_ids=sorted({t for t, fids in task.answered.items() if cited_ids & set(fids)}),
        )
        return ReportPoint(text=text, source_urls=list(dict.fromkeys(f.source_url for f in own_first)),
                           statement=statement)

    summary = [p for n, d in enumerate(draft.executive_summary if draft else [])
               if (p := build(d, f"summary[{n}]", True)) is not None]
    sections: list[ReportSection] = []
    for s, section in enumerate((draft.sections if draft else [])[:DEFAULT_MAX_SECTIONS]):
        points = [p for n, d in enumerate(section.points)
                  if (p := build(d, f"sections[{s}].points[{n}]", False)) is not None]
        title = " ".join(section.title.split())[:_SECTION_TITLE_CHARS]
        if points and title:
            sections.append(ReportSection(title=title, points=points))
    return ReportComposition(
        question=task.question, session_id=task.session_id, iteration=task.iteration,
        max_iterations=task.max_iterations, as_of=task.as_of, scope=task.scope,
        sub_topics=list(task.sub_topics), sources=list(task.sources), findings=list(task.findings),
        summary=summary, sections=sections, rejected=[r.reason for r in rejected],
        rejected_points=rejected, fact_rows=list(task.facts), not_found=list(task.not_found),
        finding_labels={label: finding_id for label, finding_id in ids.items()},
        generated_on=task.generated_on,
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
        geographies = list(dict.fromkeys(target.geography for target in targets if target.geography))
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
            geographies=geographies,
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

    def _compose_result(self, task: ReportWriterTask, draft: ReportWriterDraft | None) -> WrittenReport:
        composition = compose_written_report(task, draft)
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
        return self._compose_result(task, draft)

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
            result = self._compose_result(task, draft)
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
