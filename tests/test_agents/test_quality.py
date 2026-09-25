"""Spec §6.4 and PD-10: the deterministic gates over a written report.

Every fixture here is a report Task 3.4's ``compose_written_report`` actually
composed over ``tests.evidence_fakes`` findings, so each gate is exercised
against the shapes the pipeline produces rather than against a hand-built
composition. ``duplicate_fact_rows`` is the one gate with no fixture: the
writer's ``fact_rows()`` already merges same-fact rows, so the gate guards
hand-built compositions and future producers only (PD-10, F11).
"""

from __future__ import annotations

import asyncio
import re
import tempfile
from collections.abc import Sequence
from pathlib import Path
from unittest.mock import patch

import pytest

from deep_research.agents import (
    ReportQualitySnapshot as AgentReportQualitySnapshot,
)
from deep_research.agents import evidence_verifier
from deep_research.agents.evidence_verifier import (
    StatementCheckDraft,
    StatementVerdictDraft,
)
from deep_research.agents.quality import compute_report_quality
from deep_research.agents.report import ReportComposition
from deep_research.agents.report_writer import (
    REPORT_WRITER_NAME,
    ReportWriterAgent,
    ReportWriterDraft,
    WriterPointDraft,
    compose_written_report,
)
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import LangSmithRuntimeConfig, Tracker
from deep_research.providers import ProviderResponseError
from deep_research.utils import (
    ReportQualitySnapshot as UtilsReportQualitySnapshot,
)
from deep_research.utils.config import AgentRuntimeConfig
from deep_research.utils.types import (
    EvidenceTarget,
    FigureContext,
    FigureResult,
    Finding,
    FindingVerification,
    ResearchState,
    SubTopic,
)
from tests.agent_fakes import ScriptedCompleter
from tests.evidence_fakes import figure, make_finding, make_read, make_target
from tests.research_fakes import report_writer_tools

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

# One provider failure, shared: a batch the Statement Check could not judge.
PROVIDER_FAILURE = ProviderResponseError(
    "provider returned an HTTP error",
    retryable=True,
    failure_category="http",
    http_status_code=503,
    failure_origin="sdk",
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


def _draft() -> ReportWriterDraft:
    return ReportWriterDraft(
        executive_summary=[
            WriterPointDraft(text=ACTUAL_TEXT, finding_labels=["F01"]),
            WriterPointDraft(text=FORECAST_TEXT, finding_labels=["F02"]),
        ],
        sections=[],
    )


def _consistent_reply(messages: list, schema: type) -> StatementCheckDraft:
    """Answer every sentence of the request's own batch with "consistent"."""
    del schema
    return StatementCheckDraft(
        statements=[
            StatementVerdictDraft(
                label=label, verdict="consistent", reason="Matches the cited findings."
            )
            for label in re.findall(r"## (S\d+)", messages[1].content)
        ]
    )


_TOOLS_ROOT = tempfile.TemporaryDirectory(prefix="quality-writer-")


def _writer(completer: ScriptedCompleter) -> ReportWriterAgent:
    """The real writer, wired with the two tools it declares."""
    tracker = Tracker(
        LangSmithRuntimeConfig(
            tracing_enabled=False, project="quality-tests", api_key=None
        )
    )
    return ReportWriterAgent(
        provider=completer,
        tracker=tracker,
        scratchpad=ScratchpadMemory(
            session_id="session-quality",
            agent_name=REPORT_WRITER_NAME,
            max_entries=5,
        ),
        tools=report_writer_tools(tracker, output_root=Path(_TOOLS_ROOT.name)),
        config=AgentRuntimeConfig(max_iterations=1, tool_budget=0),
    )


def _compose(
    state: ResearchState,
    draft: ReportWriterDraft,
    completer: ScriptedCompleter,
) -> tuple[ResearchState, ReportComposition]:
    """Compose one pass through the writer, as its graph node does."""
    agent = _writer(completer)
    composition = asyncio.run(
        compose_written_report(
            agent.build_task(state), draft, provider=completer, fingerprint=None
        )
    )
    return state.model_copy(update={"composition": composition}), composition


def _clean_pair(
    *, findings: Sequence[Finding] | None = None, failed_batch: bool = False
) -> tuple[ResearchState, ReportComposition]:
    completer = ScriptedCompleter(
        outputs=[PROVIDER_FAILURE] if failed_batch else [_consistent_reply]
    )
    state = _state([ACTUAL, FORECAST] if findings is None else findings)
    return _compose(state, _draft(), completer)


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
    async def _raise(
        provider, items, *, question, fingerprint=None,
        batch_size=None, concurrency=None,
    ):
        # The two bounds are part of the call the real checker accepts
        # (PD-12); this stand-in fails the call whatever they are.
        del provider, items, question, fingerprint, batch_size, concurrency
        raise PROVIDER_FAILURE

    with patch.object(evidence_verifier, "check_statements", _raise):
        return _clean_pair()


def with_uncited_point() -> tuple[ResearchState, ReportComposition]:
    """The clean pair with one kept point whose statement cites no finding."""
    state, composition = _clean_pair()
    first, *rest = composition.summary
    uncited = first.model_copy(
        update={"statement": first.statement.model_copy(update={"finding_ids": []})}
    )
    return _relinked(state, composition, summary=[uncited, *rest])


def with_unknown_finding_id() -> tuple[ResearchState, ReportComposition]:
    """The clean pair with one kept point citing a finding no registry carries."""
    state, composition = _clean_pair()
    first, *rest = composition.summary
    dangling = first.model_copy(
        update={
            "statement": first.statement.model_copy(
                update={"finding_ids": ["finding-nobody-cited"]}
            )
        }
    )
    return _relinked(state, composition, summary=[dangling, *rest])


def missing_forecast_state(
    *, listed_not_found: bool
) -> tuple[ResearchState, ReportComposition]:
    """A pass whose second required target no verified finding answers.

    ``listed_not_found`` keeps the Not found row the writer built from the
    plan's own targets, or clears it: a missing obligation the report lists is
    accounted for, and one it does not list is the gate.
    """
    state, composition = _compose(
        _state([ACTUAL]),
        ReportWriterDraft(
            executive_summary=[WriterPointDraft(text=ACTUAL_TEXT, finding_labels=["F01"])],
            sections=[],
        ),
        ScriptedCompleter(outputs=[_consistent_reply]),
    )
    if listed_not_found:
        return state, composition
    return _relinked(state, composition, not_found=[])


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


def test_a_forecast_row_without_a_release_is_counted_not_failed() -> None:
    state, composition = with_composition()
    rows = list(composition.fact_rows)
    first = next(n for n, row in enumerate(rows) if row.kind == "forecast")
    rows[first] = rows[first].model_copy(update={"release": None})
    snapshot = compute_report_quality(*with_composition(fact_rows=rows))
    assert snapshot.forecasts_without_release == 1 and snapshot.hard_failures == []


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
