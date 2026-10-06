"""The written report's deterministic gate set.

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
from deep_research.agents.report import ReportComposition, answered_not_stated_targets
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
    """Whether two rows would be one fact to ``fact_rows``.

    Invariant: ``fact_rows()`` already merges same-fact rows, so this
    guards hand-built compositions and future producers, and it may only count a
    pair the rows path would have merged. Two of its rules keep rows apart, and
    both are mirrored here:

    * a row that states no subject is not the same fact as a row that names one.
      ``same_subject`` treats no subject as compatible with any, which is what
      lets a subject-less figure join its fact's group, but as a duplicate test
      it would fail a clean report for two rows the rows path deliberately kept
      apart;
    * two rows answering different obligations are two facts, however equal
      their values -- the two parts of one question, not one fact stated twice.

    The subject term is asked over the same context the rows path builds.
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
    """The written report's own gate set.

    ``state`` supplies the plan's targets, the verified findings and the two
    published artifacts; ``composition`` supplies the sentences that reached
    the reader and the rows the Key Facts table prints. No report prose is
    parsed, and nothing here is a judgement of the writing: the Statement
    Check and the Report Reviewer judge sentences and evidence, and this
    function only reads what they recorded.

    A required target is missing when no verified finding answers it
    (``verified_facts.answered_target_ids``), and accounted for when the
    reader report lists it under Not found: every remaining
    obligation must be disclosed, not met.
    """
    targets = [t for topic in state.sub_topics for t in topic.evidence_targets]
    required = [t.target_id for t in targets if t.required]
    # The plan goes in with the findings: an extraction that
    # bound no target is answered through the sub-topic it names, which is what
    # stops the gate declaring an obligation the report itself answers.
    answered = answered_target_ids(state.verified_findings, targets,
                                   sub_topics=state.sub_topics)
    missing = [t for t in required if t not in answered]
    listed = {row.target_id for row in composition.not_found} | set(
        answered_not_stated_targets(composition)
    )
    by_id = {finding_fingerprint(f): f for f in composition.findings}
    points = [*composition.summary, *(p for s in composition.sections for p in s.points)]
    stated = {identifier for p in points if p.statement is not None
              for identifier in p.statement.finding_ids}
    # An answered target is not yet *accounted for*, whichever way it was
    # answered. With the fallback an unbound finding answers a target of its own
    # sub-topic; an explicit binding answers it directly. Either way,
    # nothing else forces that answer to reach the reader -- a required
    # question could be neither stated nor disclosed while every other gate
    # passed, whether because the writer never bound it (the fallback case)
    # or because the part that would have stated it vanished (a redraft
    # that carried the part over unchanged, or a part whose every drafted
    # point was refused). Once the renderer's "We found sources on these..."
    # group discloses such a target (``answered_not_stated_targets``, the
    # same helper folded into ``listed`` above), it is no longer a silent
    # gap and the gate stops flagging it: a required target counts as
    # accounted for when a kept statement cites one of its answering
    # findings, or when the report lists it under Not found, or when the
    # report discloses it as answered-but-unstated.
    unaccounted = [
        t for t in required
        if t not in listed and (t not in answered or not set(answered[t]) & stated)
    ]
    uncited = sum(1 for p in points if p.statement is None or not p.statement.finding_ids)
    unresolved = sum(1 for p in points if p.statement is not None and (
        any(i not in by_id for i in p.statement.finding_ids) or not p.source_urls))
    # Every kept sentence was judged by the Statement Check, or its
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
    finding_by_id = {finding_fingerprint(f): f for f in composition.findings}
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
        quoted_findings=sum(1 for v in statuses if v.status == "quoted"),
        dropped_findings=sum(1 for v in statuses if v.status == "dropped"),
        context_unchecked_findings=sum(1 for v in statuses if v.context_unchecked),
        dropped_figures=sum(1 for v in statuses for r in v.figure_results if not r.kept),
        cited_findings=len({i for p in points if p.statement for i in p.statement.finding_ids}),
        cited_sources=len({u for p in points for u in p.source_urls}),
        duplicate_fact_rows=duplicates, uncited_settled_points=uncited,
        unresolved_citations=unresolved, unjudged_sentences=unjudged,
        refused_sentences=len(composition.rejected_points), hard_failures=failures,
        forecasts_without_release=sum(
            1 for row in rows
            if row.kind == "forecast"
            and not (
                (finding := finding_by_id.get(row.finding_id)) is not None
                and (finding.release_date or finding.statement_date)
            )
        ),
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
