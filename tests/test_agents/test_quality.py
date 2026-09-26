"""Spec §6.4 and PD-10: the deterministic gates over a written report.

Every fixture here builds a ``ReportComposition`` directly: the gates read
typed state and the typed composition, never the Report Writer agent's own
machinery, so a hand-built pass with the same shape exercises them exactly as
a real one would. ``duplicate_fact_rows`` is the one gate with no fixture: the
writer's ``fact_rows()`` already merges same-fact rows, so the gate guards
hand-built compositions and future producers only (PD-10, F11).
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from deep_research.agents import (
    ReportQualitySnapshot as AgentReportQualitySnapshot,
)
from deep_research.agents.identity import finding_fingerprint
from deep_research.agents.quality import compute_report_quality
from deep_research.agents.report import ReportComposition
from deep_research.utils import (
    ReportQualitySnapshot as UtilsReportQualitySnapshot,
)
from deep_research.utils.types import (
    EvidenceTarget,
    FactRow,
    FigureContext,
    FigureResult,
    Finding,
    FindingVerification,
    NotFoundTarget,
    ReportPoint,
    ReportStatement,
    ResearchError,
    ResearchState,
    SubTopic,
)
from tests.evidence_fakes import figure, make_finding, make_read, make_target

EIA = "U.S. Energy Information Administration"
ACTUAL_TARGET = "topic-01-target-01"
FORECAST_TARGET = "topic-02-target-01"
ACTUAL_TEXT = "Generators added 10.4 gigawatts (GW) of new battery storage capacity in 2024"
FORECAST_TEXT = "Battery storage capacity grows by 47% (14 GW) in 2025."


def _checked(
    url: str,
    text: str,
    value: str,
    unit: str,
    *,
    organisation: str,
    kind: str,
    period: str,
    target: str,
) -> Finding:
    """One verified finding: a kept figure, with its Context Check context."""
    read = make_read(text, url=url, title=f"{organisation} page")
    finding = make_finding(
        read,
        text,
        figures=[figure(value, unit, period, kind)],
        target_ids=[target],
        release_date="2025-03-12",
    )
    result = FigureResult(
        figure=finding.figures[0],
        matched=True,
        evidence_words=text,
        context=FigureContext(
            period=period,
            scope=None,
            attribution="own",
            organisation=organisation,
            kind=kind,
        ),
    )
    return finding.model_copy(
        update={
            "verification": FindingVerification(
                status="verified", figure_results=[result]
            )
        }
    )


ACTUAL = _checked(
    "https://www.eia.gov/todayinenergy/detail.php?id=64705",
    ACTUAL_TEXT,
    "10.4",
    "GW",
    organisation=EIA,
    kind="actual",
    period="2024",
    target=ACTUAL_TARGET,
)
FORECAST = _checked(
    "https://www.eia.gov/outlooks/steo/report/batteries.php",
    FORECAST_TEXT,
    "14",
    "GW",
    organisation=EIA,
    kind="forecast",
    period="2025",
    target=FORECAST_TARGET,
)


def _topic(index: int, target: EvidenceTarget | None = None) -> SubTopic:
    return SubTopic(
        coverage_id=f"topic-{index:02d}",
        title=f"Topic {index}",
        rationale="This topic matters to the answer.",
        search_queries=[f"topic {index} evidence"],
        success_criteria=["An evidence target answers the topic."],
        priority=index,
        evidence_targets=[] if target is None else [target],
    )


def _state(findings: Sequence[Finding]) -> ResearchState:
    """Five planned sub-topics, the first two carrying the required targets.

    Five on purpose: the retired broad-plan gate fired at five or more topics
    whose coverage fell below 80%, so a clean report over this plan proves
    that gate is gone (PD-10) instead of being a shape it never reached.
    """
    targets = [
        make_target(organisation=EIA),
        make_target(FORECAST_TARGET, kind="forecast", period="2025", organisation=EIA),
    ]
    topics = [
        _topic(index, target) for index, target in enumerate(targets, start=1)
    ]
    topics.extend(_topic(index) for index in range(3, 6))
    return ResearchState(
        session_id="session-quality",
        original_question=(
            "How much battery storage capacity was added in 2024, and what is "
            "forecast for 2025?"
        ),
        sub_topics=topics,
        verified_findings=list(findings),
        report="# Reader report",
        report_evidence="# Evidence ledger",
    )


def _fact_row_for(finding: Finding, *, row_id: str, target_id: str) -> FactRow:
    """One hand-built fact row, mirroring what ``fact_rows()`` would print for
    ``finding``'s own kept figure and its admitted release."""
    result = finding.verification.figure_results[0]
    context = result.context
    return FactRow(
        row_id=row_id,
        organisation=context.organisation,
        attribution=context.attribution,
        measure="battery storage power capacity added",
        period=context.period,
        value=f"{result.figure.value} {result.figure.unit}",
        kind=context.kind,
        release=f"released {finding.release_date}" if finding.release_date else None,
        finding_id=finding_fingerprint(finding),
        target_ids=[target_id],
    )


def _point_for(finding: Finding, *, statement_id: str, target_id: str) -> ReportPoint:
    text = finding.snippet or finding.content
    return ReportPoint(
        text=text,
        source_urls=[finding.source_url],
        statement=ReportStatement(
            statement_id=statement_id, text=text,
            finding_ids=[finding_fingerprint(finding)], target_ids=[target_id],
        ),
    )


def _clean_pair(
    *, findings: Sequence[Finding] | None = None, failed_batch: bool = False,
    error_type: str = "evidence_verifier_statement_check_failed",
) -> tuple[ResearchState, ReportComposition]:
    """A pass with nothing wrong in it.

    Every required target is answered by a verified finding, every kept
    sentence was judged by the Statement Check, as-of and scope are declared,
    both artifacts are written, and the Key Facts table carries a forecast row
    with its release (PD-24). ``findings`` only widens ``state.verified_
    findings`` (e.g. an extra quoted finding); the two cited, fact-rowed
    findings are always ACTUAL and FORECAST.
    """
    state = _state([ACTUAL, FORECAST] if findings is None else findings)
    if failed_batch:
        verdicts = {"S001": "unchecked", "S002": "unchecked"}
        errors = [ResearchError(error_type=error_type, source="report_writer",
                                message="The Statement Check batch failed.", recoverable=True)]
    else:
        verdicts = {"S001": "consistent", "S002": "consistent"}
        errors = []
    composition = ReportComposition(
        question=state.original_question, session_id=state.session_id,
        as_of="2026-09-25", scope="United States", sub_topics=state.sub_topics,
        findings=[ACTUAL, FORECAST],
        fact_rows=[
            _fact_row_for(ACTUAL, row_id="K001", target_id=ACTUAL_TARGET),
            _fact_row_for(FORECAST, row_id="K002", target_id=FORECAST_TARGET),
        ],
        summary=[
            _point_for(ACTUAL, statement_id="S001", target_id=ACTUAL_TARGET),
            _point_for(FORECAST, statement_id="S002", target_id=FORECAST_TARGET),
        ],
        statement_verdicts=verdicts,
        errors=errors,
    )
    return state.model_copy(update={"composition": composition}), composition


def _relinked(
    state: ResearchState,
    composition: ReportComposition,
    **overrides: object,
) -> tuple[ResearchState, ReportComposition]:
    """The pair, with named composition fields replaced on both halves."""
    updated = composition.model_copy(update=overrides) if overrides else composition
    return state.model_copy(update={"composition": updated}), updated


def clean_state() -> ResearchState:
    """A pass with nothing wrong in it.

    Every required target is answered by a verified finding, every kept
    sentence was judged by the Statement Check, as-of and scope are declared,
    both artifacts are written, and the Key Facts table carries a forecast row
    with its release (PD-24).
    """
    return _clean_pair()[0]


def with_composition(**overrides: object) -> tuple[ResearchState, ReportComposition]:
    """The clean pair, with the named composition fields replaced.

    ``model_copy`` skips validation on purpose: it is how a fixture writes the
    composition shape a defect needs without rebuilding the pass.
    """
    state, composition = _clean_pair()
    return _relinked(state, composition, **overrides)


def with_unjudged_sentence(
    *, recorded_failure: bool = False
) -> tuple[ResearchState, ReportComposition]:
    """The clean pair's kept sentences left unjudged.

    Without ``recorded_failure`` the composition carries no verdict for them
    at all — the defect ``unjudged_sentences`` exists for. With it they carry
    "unchecked" and the batch failure the writer records when its Statement
    Check call fails, which is §6.4's recorded-batch-failure case.
    """
    if recorded_failure:
        return _clean_pair(failed_batch=True)
    state, composition = _clean_pair()
    return _relinked(state, composition, statement_verdicts={})


def with_recorded_writer_failure() -> tuple[ResearchState, ReportComposition]:
    """The same recorded failure from the writer's own outer guard: the check
    call failed before it could return per-batch accounting, so the writer
    records ``report_writer_statement_check_failed`` and keeps every sentence.
    """
    return _clean_pair(failed_batch=True, error_type="report_writer_statement_check_failed")


def _extra_point(*, finding_ids: list[str]) -> ReportPoint:
    """One kept point beside the clean summary, judged consistent like the rest."""
    return ReportPoint(
        text="A point the section evidence carries.",
        source_urls=["https://example.test/section"],
        statement=ReportStatement(statement_id="S003",
                                  text="A point the section evidence carries.",
                                  finding_ids=finding_ids, target_ids=[]),
    )


def with_uncited_point() -> tuple[ResearchState, ReportComposition]:
    """The clean pair with one kept point whose statement cites no finding.

    The point is *added* to the clean summary rather than standing in for a
    statement that answers a required target: with the accounting rule (review
    F2) a target whose only statement is defected is unaccounted for as well,
    and this fixture keeps to its one gate.
    """
    state, composition = _clean_pair()
    verdicts = {**composition.statement_verdicts, "S003": "consistent"}
    return _relinked(state, composition,
                     summary=[*composition.summary, _extra_point(finding_ids=[])],
                     statement_verdicts=verdicts)


def with_unknown_finding_id() -> tuple[ResearchState, ReportComposition]:
    """The clean pair with one kept point citing a finding no registry carries.

    Added beside the clean summary for the same reason as ``with_uncited_point``:
    the required targets stay stated by the points that answer them.
    """
    state, composition = _clean_pair()
    verdicts = {**composition.statement_verdicts, "S003": "consistent"}
    return _relinked(state, composition,
                     summary=[*composition.summary,
                              _extra_point(finding_ids=["finding-nobody-cited"])],
                     statement_verdicts=verdicts)


def missing_forecast_state(
    *, listed_not_found: bool
) -> tuple[ResearchState, ReportComposition]:
    """A pass whose second required target no verified finding answers.

    ``listed_not_found`` keeps the Not found row the writer built from the
    plan's own targets, or clears it: a missing obligation the report lists is
    accounted for, and one it does not list is the gate.
    """
    state = _state([ACTUAL])
    composition = ReportComposition(
        question=state.original_question, session_id=state.session_id,
        as_of="2026-09-25", scope="United States", sub_topics=state.sub_topics,
        findings=[ACTUAL],
        fact_rows=[_fact_row_for(ACTUAL, row_id="K001", target_id=ACTUAL_TARGET)],
        summary=[_point_for(ACTUAL, statement_id="S001", target_id=ACTUAL_TARGET)],
        statement_verdicts={"S001": "consistent"},
        not_found=(
            [NotFoundTarget(
                target_id=FORECAST_TARGET,
                question="What does the EIA forecast for 2025?",
                searched=True,
            )]
            if listed_not_found else []
        ),
    )
    return state.model_copy(update={"composition": composition}), composition



def without_reader_report() -> tuple[ResearchState, ReportComposition]:
    state, composition = _clean_pair()
    return state.model_copy(
        update={"report": "", "composition": composition}
    ), composition


def without_evidence_ledger() -> tuple[ResearchState, ReportComposition]:
    state, composition = _clean_pair()
    return state.model_copy(
        update={"report_evidence": "", "composition": composition}
    ), composition


def without_as_of() -> tuple[ResearchState, ReportComposition]:
    return with_composition(as_of="")


def without_scope() -> tuple[ResearchState, ReportComposition]:
    return with_composition(scope="")


@pytest.mark.parametrize(
    ("gate", "build"),
    [
        pytest.param("unjudged_sentences", with_unjudged_sentence, id="unjudged_sentences"),
        pytest.param("uncited_settled_points", with_uncited_point, id="uncited_settled_points"),
        pytest.param("unresolved_citations", with_unknown_finding_id, id="unresolved_citations"),
        pytest.param("missing_as_of", without_as_of, id="missing_as_of"),
        pytest.param("missing_scope", without_scope, id="missing_scope"),
        pytest.param(
            "unaccounted_required_targets",
            lambda: missing_forecast_state(listed_not_found=False),
            id="unaccounted_required_targets",
        ),
        pytest.param("missing_reader_report", without_reader_report, id="missing_reader_report"),
        pytest.param(
            "missing_evidence_ledger", without_evidence_ledger, id="missing_evidence_ledger"
        ),
    ],
)
def test_each_gate_fires_on_its_own_defect(gate: str, build) -> None:
    """One defect, one gate: no retired name and no second gate beside it."""
    assert compute_report_quality(*build()).hard_failures == [gate]


def test_a_clean_written_report_has_no_hard_failure() -> None:
    snapshot = compute_report_quality(clean_state(), clean_state().composition)

    assert snapshot.hard_failures == [] and snapshot.missing_required_target_ids == []


def test_the_snapshot_records_what_the_pipeline_measured() -> None:
    state = clean_state()

    snapshot = compute_report_quality(state, state.composition)

    assert snapshot.required_target_ids == [ACTUAL_TARGET, FORECAST_TARGET]
    assert snapshot.answered_target_ids == [ACTUAL_TARGET, FORECAST_TARGET]
    assert snapshot.verified_findings == 2 and snapshot.dropped_findings == 0
    assert snapshot.cited_findings == 2 and snapshot.cited_sources == 2
    assert snapshot.forecasts_without_release == 0
    assert snapshot.refused_sentences == 0 and snapshot.unjudged_sentences == []


def test_the_snapshot_counts_quoted_findings_separately() -> None:
    """D21: a quoted finding (no figure; neither check judged it for
    relevance or attribution) is counted in its own bucket, not folded into
    verified_findings -- the published counts must not overstate what was
    actually checked."""
    quoted = make_finding(
        make_read(), "Generators added 10.4 gigawatts", target_ids=[]
    ).model_copy(update={"verification": FindingVerification(status="quoted")})
    state, composition = _clean_pair(findings=[ACTUAL, FORECAST, quoted])

    snapshot = compute_report_quality(state, composition)

    assert snapshot.verified_findings == 2
    assert snapshot.quoted_findings == 1



def test_answered_targets_come_from_the_findings_not_the_statement_metadata() -> None:
    """The claim-era statement check is not what answers a target (R2).

    Both kept statements here name no target at all, which the statement-level
    reading would score as "nothing is answered", and the two required targets
    are still answered: a verified finding answers them.
    """
    state, composition = _clean_pair()
    stripped = [
        point.model_copy(update={"statement": point.statement.model_copy(update={"target_ids": []})})
        for point in composition.summary
    ]
    state, composition = _relinked(state, composition, summary=stripped)

    snapshot = compute_report_quality(state, composition)

    assert snapshot.answered_target_ids == [ACTUAL_TARGET, FORECAST_TARGET]
    assert snapshot.hard_failures == []


def test_a_missing_required_target_is_missing_but_accounted_when_listed_not_found() -> None:
    state, composition = missing_forecast_state(listed_not_found=True)
    assert [row.target_id for row in composition.not_found] == [FORECAST_TARGET]
    snapshot = compute_report_quality(state, composition)
    assert snapshot.missing_required_target_ids == [FORECAST_TARGET]
    assert "unaccounted_required_targets" not in snapshot.hard_failures
    unlisted = compute_report_quality(*missing_forecast_state(listed_not_found=False))
    assert unlisted.unaccounted_target_ids == [FORECAST_TARGET]
    assert "unaccounted_required_targets" in unlisted.hard_failures


def test_a_forecast_finding_with_no_admitted_date_counts_without_a_release() -> None:
    """§11.2: the gate reads the forecast's finding for an admitted
    ``release_date``/``statement_date``, not the row's own ``release`` text --
    which can hold a vintage alone.
    """
    vintage_only = FORECAST.model_copy(update={"release_date": None, "statement_date": None})
    state, composition = _clean_pair()
    rows = [
        row.model_copy(update={"release": "January 2025 STEO"}) if row.kind == "forecast" else row
        for row in composition.fact_rows
    ]

    state, composition = _relinked(
        state, composition, fact_rows=rows, findings=[ACTUAL, vintage_only],
    )
    snapshot = compute_report_quality(state, composition)

    assert snapshot.forecasts_without_release == 1
    assert "forecasts_without_release" not in snapshot.hard_failures


def test_a_forecast_finding_with_an_admitted_date_counts_with_a_release() -> None:
    """The clean pair's forecast finding carries ``release_date``, so it is
    not counted even though nothing gates on it either way."""
    snapshot = compute_report_quality(*_clean_pair())
    assert snapshot.forecasts_without_release == 0



def test_a_kept_sentence_with_no_verdict_is_reported_by_statement_id() -> None:
    snapshot = compute_report_quality(*with_unjudged_sentence())
    assert snapshot.unjudged_sentences == ["S001", "S002"]
    assert "unjudged_sentences" in snapshot.hard_failures


@pytest.mark.parametrize(
    ("build", "error_type"),
    [
        pytest.param(
            lambda: with_unjudged_sentence(recorded_failure=True),
            "evidence_verifier_statement_check_failed",
            id="verifier-batch-failure",
        ),
        pytest.param(
            with_recorded_writer_failure,
            "report_writer_statement_check_failed",
            id="writer-guard-failure",
        ),
    ],
)
def test_a_recorded_batch_failure_keeps_the_sentence_but_is_not_a_gate_failure(
    build, error_type: str
) -> None:
    state, composition = build()
    assert composition.statement_verdicts == {"S001": "unchecked", "S002": "unchecked"}
    assert [error.error_type for error in composition.errors] == [error_type]
    snapshot = compute_report_quality(state, composition)
    assert snapshot.unjudged_sentences == [] and snapshot.hard_failures == []


def test_report_quality_snapshot_is_exported_from_typed_layers() -> None:
    assert AgentReportQualitySnapshot is UtilsReportQualitySnapshot


def test_two_rows_that_differ_only_by_subject_are_not_duplicates() -> None:
    state, composition = _clean_pair()
    row = composition.fact_rows[0]
    apart = [row.model_copy(update={"row_id": "K001", "subject": "Model A"}),
             row.model_copy(update={"row_id": "K002", "subject": "Model B"})]
    same = [row.model_copy(update={"row_id": "K001", "subject": "Model A"}),
            row.model_copy(update={"row_id": "K002", "subject": "Model A"})]
    assert compute_report_quality(*_relinked(state, composition, fact_rows=apart)).duplicate_fact_rows == 0
    assert compute_report_quality(*_relinked(state, composition, fact_rows=same)).duplicate_fact_rows == 1


def test_a_row_with_no_subject_is_not_a_duplicate_of_a_row_that_names_one() -> None:
    """One subject-less row beside a named one is two rows, not a duplicate pair.

    ``same_subject`` treats a row that states no subject as compatible with any
    -- which is what lets a subject-less figure join its fact's group -- but the
    gate must not read that as "the same fact": a named row and an unnamed one
    are the two rows ``fact_rows`` deliberately keeps apart, and counting them
    would fail a clean report on ``duplicate_fact_rows``.
    """
    state, composition = _clean_pair()
    row = composition.fact_rows[0]
    paired = [row.model_copy(update={"row_id": "K001", "subject": "Model A"}),
              row.model_copy(update={"row_id": "K002", "subject": None})]
    snapshot = compute_report_quality(*_relinked(state, composition, fact_rows=paired))
    assert snapshot.duplicate_fact_rows == 0
    assert snapshot.hard_failures == []


def test_two_rows_answering_different_obligations_are_not_duplicates() -> None:
    """I6: rows that answer different targets are two facts, however equal their values.

    ``fact_rows`` keeps a pair of figures apart exactly when each answers a
    different obligation, so the gate must not call that pair a duplicate: two
    equal values for two parts of one question are the answer, not a defect.
    """
    state, composition = _clean_pair()
    row = composition.fact_rows[0].model_copy(update={"subject": "Model A"})
    apart = [row.model_copy(update={"row_id": "K001", "target_ids": [ACTUAL_TARGET]}),
             row.model_copy(update={"row_id": "K002", "target_ids": [FORECAST_TARGET]})]
    same = [row.model_copy(update={"row_id": "K001", "target_ids": [ACTUAL_TARGET]}),
            row.model_copy(update={"row_id": "K002", "target_ids": [ACTUAL_TARGET]})]
    snapshot = compute_report_quality(*_relinked(state, composition, fact_rows=apart))
    assert snapshot.duplicate_fact_rows == 0 and snapshot.hard_failures == []
    assert compute_report_quality(*_relinked(
        state, composition, fact_rows=same)).duplicate_fact_rows == 1


def _unbound_dated_finding() -> Finding:
    """One verified figure the extraction bound to no target (the run's shape)."""
    text = "The obligations apply from 2 August 2025."
    read = make_read(text, url="https://example-relay.example/law/12",
                     title="Article 12: Registration | Example Act | Example Relay")
    finding = make_finding(read, text, figures=[figure("2 August 2025", "date", None, "actual")])
    finding = finding.model_copy(update={"related_sub_topic": "Topic 3"})
    result = FigureResult(
        figure=finding.figures[0], matched=True, evidence_words=text,
        context=FigureContext(period=None, scope=None, attribution="unattributed",
                              organisation="Example Relay", kind="actual"),
    )
    return finding.model_copy(update={
        "verification": FindingVerification(status="verified", figure_results=[result])})


def _stating_composition(state: ResearchState, findings: Sequence[Finding], *,
                         not_found: NotFoundTarget | None = None) -> ReportComposition:
    """A composition whose summary states the answers these findings carry.

    The gate reads the kept statements' own ``finding_ids`` (review F2), so a
    fixture states an answer by citing the finding that carries it -- exactly
    what the writer's packet asks the model to do.
    """
    return ReportComposition(
        question=state.original_question, session_id=state.session_id, as_of="2026-09-25",
        summary=[ReportPoint(
            text="The obligations apply from 2 August 2025.",
            source_urls=[f.source_url for f in findings],
            statement=ReportStatement(
                statement_id="S001", text="The obligations apply from 2 August 2025.",
                finding_ids=[finding_fingerprint(f) for f in findings], target_ids=[]),
        )],
        not_found=[] if not_found is None else [not_found],
    )


def test_an_unbound_extraction_answers_the_targets_of_its_own_sub_topic() -> None:
    """Improvement 1A on the run's shape: the live run's figures were all dates
    and every one carried an empty ``target_ids``, so this gate declared two
    obligations the report itself answered "Not found". The plan resolves an
    unbound finding through the sub-topic it names."""
    when = make_target("topic-03-target-01", question="From what date do the obligations apply?",
                       measure="application date", unit_dimension=None, period=None,
                       kind=None, geography=None)
    dated = _unbound_dated_finding()
    state = _state([dated]).model_copy(update={"sub_topics": [
        _topic(1, make_target(organisation=EIA)), _topic(2), _topic(3, when),
        _topic(4), _topic(5)]})
    composition = _stating_composition(state, [dated])

    snapshot = compute_report_quality(state, composition)
    assert "topic-03-target-01" in snapshot.answered_target_ids
    assert "topic-03-target-01" not in snapshot.missing_required_target_ids
    # Stated, so this target is accounted for (review F2's other half). The
    # plan's other required target has no finding at all, which is the pre-1A
    # case and not this test's subject.
    assert "topic-03-target-01" not in snapshot.unaccounted_target_ids

    # Answered but never stated: the obligation is neither missing (a finding
    # answers it) nor silent -- it is an unaccounted answer and a hard failure.
    unstated = compute_report_quality(state, _stating_composition(state, []))
    assert "topic-03-target-01" not in unstated.missing_required_target_ids
    assert "topic-03-target-01" in unstated.unaccounted_target_ids
    assert "unaccounted_required_targets" in unstated.hard_failures
    # Disclosed under Not found instead of stated: the other honest reading.
    listed = compute_report_quality(state, _stating_composition(
        state, [], not_found=NotFoundTarget(target_id="topic-03-target-01",
                                            question="From what date do the obligations apply?")))
    assert "topic-03-target-01" not in listed.unaccounted_target_ids

    # The same run with a finding that names another sub-topic is the run's own
    # observation: the obligation it answers is declared missing.
    blind = state.model_copy(update={
        "verified_findings": [dated.model_copy(update={"related_sub_topic": "Another topic"})]})
    assert "topic-03-target-01" in compute_report_quality(
        blind, composition).missing_required_target_ids


def test_an_answer_the_extraction_bound_is_accounted_for_by_being_answered() -> None:
    """The bound on F2's rule: a target the *extraction* bound to a finding whose
    sentence the Statement Check refused stays accounted for by being answered.

    That is the pre-1A reading, and it is what the controlled scenarios
    (`report-scope-corrected-to-all-segments`, `report-relay-labelled-as-relay`,
    `validated-cache-reuse`) publish: their own expectation is an accepted report
    whose refused sentence is gone. The rule's job is the fallback answer, which
    no extraction ever read for this target.
    """
    bound = _unbound_dated_finding().model_copy(
        update={"target_ids": ["topic-03-target-01"]})
    assert bound.verification is not None
    when = make_target("topic-03-target-01", question="From what date do the obligations apply?",
                       measure="application date", unit_dimension=None, period=None,
                       kind=None, geography=None)
    state = _state([bound]).model_copy(update={"sub_topics": [
        _topic(1, make_target(organisation=EIA)), _topic(2), _topic(3, when),
        _topic(4), _topic(5)]})

    snapshot = compute_report_quality(state, _stating_composition(state, []))

    # The plan's other required target has no finding at all, which is a
    # different failure; this target is the one the rule is about.
    assert "topic-03-target-01" not in snapshot.unaccounted_target_ids
    assert "topic-03-target-01" not in snapshot.missing_required_target_ids
