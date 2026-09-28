"""E1: the evidence view's JSON, built from the run's own ``ReportComposition``.

Every derivation reuses the evidence log's own helpers — the label pairing,
the figure text, the release, the page owner — so the JSON can never
disagree with the Markdown the same run composed. The two private helpers
are imported the way ``agents/report_reviewer.py`` imports them.
"""

from __future__ import annotations

from deep_research.agents.identity import finding_fingerprint
from deep_research.agents.report import _figure_value_text, _finding_registry_pairs
from deep_research.agents.sources import (
    latest_scored_sources,
    normalize_source_url,
    publisher_identity,
)
from deep_research.agents.verified_facts import release_text
from deep_research.api.models import (
    EvidenceFigureResponse,
    EvidenceFindingResponse,
    EvidenceNotFoundResponse,
    EvidenceRefusedResponse,
    EvidenceResponse,
    EvidenceSourceResponse,
)
from deep_research.runtime.outcome import ResearchOutcome
from deep_research.utils.types import (
    FigureResult,
    Finding,
    ReportComposition,
    ScoredSource,
)


def build_evidence_response(outcome: ResearchOutcome) -> EvidenceResponse:
    """The E1 body for a finished run; raises when the run composed nothing."""
    composition = outcome.composition
    if composition is None:
        raise ValueError("the outcome carries no composition")
    return evidence_from_composition(composition)


def evidence_from_composition(composition: ReportComposition) -> EvidenceResponse:
    # The cited set, exactly as ``compute_report_quality`` sums it (quality.py:95-97, :155).
    points = [*composition.summary, *(p for s in composition.sections for p in s.points)]
    cited = {i for p in points if p.statement for i in p.statement.finding_ids}
    # Last append-ordered score per normalised URL, as the report reads sources.
    sources = {
        normalize_source_url(s.url): s for s in latest_scored_sources(composition.sources)
    }
    # ``release`` as the log prints it: the finding's own, else its fact row's (report.py:1912-1913, :1962).
    row_release = {
        fid: row.release
        for row in composition.fact_rows
        for fid in (row.finding_id, *row.duplicate_finding_ids)
    }
    findings: list[EvidenceFindingResponse] = []
    unlabelled = 0
    for label, finding in _finding_registry_pairs(composition):
        finding_id = finding_fingerprint(finding)
        if label is None:
            unlabelled += 1
            label = f"X{unlabelled:02d}"
        findings.append(
            _finding_response(
                label,
                finding,
                cited=finding_id in cited,
                source=sources.get(normalize_source_url(finding.source_url)),
                passage=composition.statement_passages.get(finding_id),
                release=release_text(finding) or row_release.get(finding_id),
            )
        )
    return EvidenceResponse(
        session_id=composition.session_id,
        iteration=composition.iteration,
        findings=findings,
        not_found=[
            EvidenceNotFoundResponse(
                target_id=t.target_id,
                question=t.question,
                queries=list(t.queries),
                pages_read=list(t.pages_read),
                searched=t.searched,
            )
            for t in composition.not_found
        ],
        refused=[
            EvidenceRefusedResponse(
                where=r.where, text=r.text, reason=r.reason, finding_labels=list(r.finding_labels)
            )
            for r in composition.rejected_points
        ],
    )


def _finding_response(
    label: str,
    finding: Finding,
    *,
    cited: bool,
    source: ScoredSource | None,
    passage: str | None,
    release: str | None,
) -> EvidenceFindingResponse:
    verification = finding.verification
    results: list[FigureResult] = list(verification.figure_results) if verification else []
    kept_contexts = [r.context for r in results if r.kept and r.context is not None]
    organisation = (
        kept_contexts[0].organisation if kept_contexts else publisher_identity(finding.source_url)
    )
    return EvidenceFindingResponse(
        label=label,
        status=verification.status if verification else None,
        dropped_reason=verification.dropped_reason if verification else None,
        context_unchecked=verification.context_unchecked if verification else False,
        cited=cited,
        target_ids=list(finding.target_ids),
        content=finding.content,
        snippet=finding.snippet,
        passage=passage,
        source=EvidenceSourceResponse(
            url=finding.source_url,
            title=finding.source_title,
            organisation=organisation,
            evaluation_status=source.evaluation_status if source else None,
            low_confidence=source.low_confidence if source else False,
            authority_score=source.authority_score if source else None,
            recency_score=source.recency_score if source else None,
            relevance_score=source.relevance_score if source else None,
            overall_score=source.overall_score if source else None,
        ),
        figures=[_figure_response(r, release) for r in results],
    )


def _figure_response(result: FigureResult, release: str | None) -> EvidenceFigureResponse:
    context = result.context if result.kept else None
    return EvidenceFigureResponse(
        value=_figure_value_text(result.figure),
        kept=result.kept,
        period=context.period if context else None,
        scope=context.scope if context else None,
        organisation=context.organisation if context else None,
        attribution=context.attribution if context else None,
        kind=context.kind if context else None,
        release=release if context else None,
        evidence_words=result.evidence_words,
        corrected=result.corrected,
        dropped_reason=result.dropped_reason,
        reason=result.reason,
    )
