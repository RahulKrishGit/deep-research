"""The written report's deterministic gate set (spec §6.4, PD-10).

Nine gates over one pass's report and the verified findings behind it:
unresolved citations, uncited settled points, duplicate fact rows, a missing
as-of or scope, sentences the Statement Check never judged, required targets
neither answered nor listed as Not found, and the two missing artifacts.
Every reading is typed state or the typed composition -- nothing here
inspects Markdown.
"""

from __future__ import annotations

from collections.abc import Iterable

from deep_research.agents.identity import finding_fingerprint
from deep_research.agents.report import ReportComposition
from deep_research.agents.verified_facts import (
    _rows_share_a_subject,
    answered_target_ids,
    same_organisation,
    same_period,
)
from deep_research.utils.types import (
    EvidenceTarget,
    FactRow,
    ReportQualitySnapshot,
    ReportReview,
    ResearchState,
)


def _one_fact(left: FactRow, right: FactRow,
              targets: Iterable[EvidenceTarget]) -> bool:
    """Whether two rows would be one fact to ``fact_rows`` (F11, PD-9, D11).

    Invariant (F11): ``fact_rows()`` already merges same-fact rows, so this
    guards hand-built compositions and future producers, and it may only count a
    pair the rows path would have merged. Two of its rules keep rows apart, and
    both are mirrored here (the final review's P2-3 and I6):

    * a row that states no subject is not the same fact as a row that names one.
      ``same_subject`` treats no subject as compatible with any, which is what
      lets a subject-less figure join its fact's group, but as a duplicate test
      it would fail a clean report for two rows the rows path deliberately kept
      apart;
    * two rows answering different obligations are two facts, however equal
      their values -- the two parts of one question, not one fact stated twice.

    The subject term is asked over the same context the rows path builds
    (Task 5.6c fix round 1).
    """
    if left.kind != right.kind or left.value != right.value:
        return False
    if not same_period(left.period, right.period):
        return False
    if not same_organisation(left.organisation, right.organisation):
        return False
    if (left.subject is None) != (right.subject is None):
        return False
    if left.target_ids and right.target_ids and not set(left.target_ids) & set(right.target_ids):
        return False
    return _rows_share_a_subject(left, right, targets)


def compute_report_quality(
    state: ResearchState,
    composition: ReportComposition,
) -> ReportQualitySnapshot:
    """The written report's own gate set (spec §6.4, PD-10).

    ``state`` supplies the plan's targets, the verified findings and the two
    published artifacts; ``composition`` supplies the sentences that reached
    the reader and the rows the Key Facts table prints. No report prose is
    parsed, and nothing here is a judgement of the writing: the Statement
    Check and the Report Reviewer judge sentences and evidence, and this
    function only reads what they recorded.

    A required target is missing when no verified finding answers it
    (``verified_facts.answered_target_ids``), and accounted for when the
    reader report lists it under Not found: §2.3 requires every remaining
    obligation to be disclosed, not to be met.
    """
    targets = [t for topic in state.sub_topics for t in topic.evidence_targets]
    required = [t.target_id for t in targets if t.required]
    answered = answered_target_ids(state.verified_findings, targets)
    missing = [t for t in required if t not in answered]
    listed = {row.target_id for row in composition.not_found}
    unaccounted = [t for t in missing if t not in listed]
    by_id = {finding_fingerprint(f): f for f in composition.findings}
    points = [*composition.summary, *(p for s in composition.sections for p in s.points)]
    uncited = sum(1 for p in points if p.statement is None or not p.statement.finding_ids)
    unresolved = sum(1 for p in points if p.statement is not None and (
        any(i not in by_id for i in p.statement.finding_ids) or not p.source_urls))
    # §6.4, D8: every kept sentence was judged by the Statement Check, or its
    # batch failure is recorded. A kept sentence with neither is the gate.
    failure_recorded = any(
        error.error_type in {"evidence_verifier_statement_check_failed", "report_writer_statement_check_failed"}
        for error in composition.errors
    )
    judged = {"consistent", "corrected"}
    unjudged = [
        p.statement.statement_id for p in points
        if p.statement is not None
        and (verdict := composition.statement_verdicts.get(p.statement.statement_id)) not in judged
        and not (verdict == "unchecked" and failure_recorded)
    ]
    rows = composition.fact_rows
    duplicates = sum(1 for n, a in enumerate(rows) for b in rows[n + 1:]
                     if _one_fact(a, b, targets))
    statuses = [f.verification for f in state.verified_findings if f.verification is not None]
    failures = [name for name, failed in (
        ("unresolved_citations", unresolved > 0), ("uncited_settled_points", uncited > 0),
        ("duplicate_fact_rows", duplicates > 0), ("missing_as_of", not composition.as_of),
        ("missing_scope", not composition.scope), ("unjudged_sentences", bool(unjudged)),
        ("unaccounted_required_targets", bool(unaccounted)),
        ("missing_reader_report", not state.report), ("missing_evidence_ledger", not state.report_evidence),
    ) if failed]
    return ReportQualitySnapshot(
        required_target_ids=required, answered_target_ids=sorted(answered),
        missing_required_target_ids=missing, unaccounted_target_ids=unaccounted,
        verified_findings=sum(1 for v in statuses if v.status == "verified"),
        corrected_findings=sum(1 for v in statuses if v.status == "verified_corrected"),
        dropped_findings=sum(1 for v in statuses if v.status == "dropped"),
        context_unchecked_findings=sum(1 for v in statuses if v.context_unchecked),
        dropped_figures=sum(1 for v in statuses for r in v.figure_results if not r.kept),
        cited_findings=len({i for p in points if p.statement for i in p.statement.finding_ids}),
        cited_sources=len({u for p in points for u in p.source_urls}),
        duplicate_fact_rows=duplicates, uncited_settled_points=uncited,
        unresolved_citations=unresolved, unjudged_sentences=unjudged,
        refused_sentences=len(composition.rejected_points), hard_failures=failures,
        forecasts_without_release=sum(1 for row in rows if row.kind == "forecast" and not row.release),
    )


def review_status_fields(
    review: ReportReview | None,
) -> dict[str, object]:
    """The semantic-review fields a quality snapshot records beside its own.

    Kept here rather than in the review module so the snapshot's vocabulary is
    written in one place. A ``None`` review is recorded as an empty status and
    no score — never as a zero, which would read as a judgement that the report
    scored nothing rather than that no judgement was made.
    """

    if review is None:
        return {
            "semantic_review_status": "",
            "semantic_review_score": None,
            "semantic_review_fingerprint": "",
        }
    return {
        "semantic_review_status": review.status,
        "semantic_review_score": review.mean_score,
        "semantic_review_fingerprint": review.input_fingerprint,
    }


__all__ = [
    "compute_report_quality",
    "review_status_fields",
]
