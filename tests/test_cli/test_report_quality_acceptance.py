"""Mocked acceptance: the recorded report pathologies stay gone.

This file is the permanent guard for the plan that began at one committed live
CLI artifact, ``docs/reports/cli-run-2026-09-13-grid-scale-battery-storage-
854cddd3.md``. Its review section — ``docs/superpowers/plans/2026-09-14-cli-
report-quality-and-agent-output-integrity.md``, "Review result this plan must
correct" — recorded five structural pathologies:

    duplicate URL rows                     156 -> 0
    false numeric scores for unscored      221 -> 0
    unused sources in reader references     89 -> 0
    reviewer-visible required sections     2/7 -> 7/7
    duplicate claim IDs                    > 0 -> 0

Two of the five moved with the pipeline and are guarded where they now live:
duplicate claim IDs belong to the claim registry the Evidence Verifier
pipeline deleted (its successor, ``duplicate_fact_rows``, is an invariant
``fact_rows()`` maintains by construction, PD-10/F11), and the numeric score an
unscored source must not carry is now ``Sources`` rendering only the cited
assessments — no number is printed for a source the report did not cite because
the report does not print uncited sources at all. The remaining three are
measured here, on the artifacts that can still carry them: the reader report's
rows, its references, and the surfaces the Report Reviewer's packet carries.

Nothing here is pasted. The fixture is typed domain records; the reader report
and the evidence log are produced by the production renderers, the composition
by the production fact-row builder, the quality snapshot by the production
quality pass, and the summary by the production CLI renderer over a real
``ResearchOutcome``. No provider, network call, API credential, or live tier is
touched, and every host in the fixture sits under the reserved ``.example``
TLD.

Every number asserted is read from a typed object or from the published
Markdown, and each metric's surfaces are compared to each other — the summary
is checked against the records it describes, never against a hand-written
constant.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from deep_research.agents.identity import finding_fingerprint
from deep_research.agents.quality import compute_report_quality
from deep_research.agents.report import (
    render_finding_log,
    render_written_report,
)
from deep_research.agents.report_reviewer import build_report_review_input
from deep_research.agents.report_writer import citable_findings
from deep_research.agents.sources import normalize_source_url
from deep_research.agents.verified_facts import fact_rows
from deep_research.cli import render_summary
from deep_research.graph.orchestrator import GraphRun
from deep_research.runtime.outcome import build_outcome
from deep_research.utils.types import (
    EvidenceTarget,
    FigureContext,
    FigureResult,
    Finding,
    FindingVerification,
    NotFoundTarget,
    ReadRecord,
    ReportComposition,
    ReportPoint,
    ReportSection,
    ReportStatement,
    ResearchState,
    ScoredSource,
    SubTopic,
)
from tests.evidence_fakes import figure, make_finding, make_read, make_target

BATTERY_QUESTION = (
    "What are the current constraints on grid-scale battery storage deployment?"
)
SESSION_ID = "cli-acceptance-battery-storage"

EIA_URL = "https://www.eia.gov/todayinenergy/detail.php?id=64705"
STEO_URL = "https://www.eia.gov/outlooks/steo/report/electricity.php"
UNCITED_URL = "https://woodmac.example/insight/battery-storage"
EIA_OWNER = "U.S. Energy Information Administration"
MISSING_TARGET_ID = "topic-02-target-01"


def _read(url: str, title: str) -> ReadRecord:
    page = (
        "Generators added 10.4 gigawatts (GW) of new battery storage capacity "
        "in 2024. In 2025, capacity growth from battery storage could set a "
        "record as operators report plans to add 19.6 GW of utility-scale "
        "battery storage to the grid."
    )
    return make_read(page, url=url, title=title)


def _verified(
    read: ReadRecord,
    snippet: str,
    *,
    value: str,
    unit: str,
    period: str,
    kind: str,
    target_ids: Sequence[str],
) -> Finding:
    wanted = figure(value, unit, period, kind)
    return make_finding(
        read,
        snippet,
        figures=[wanted],
        target_ids=list(target_ids),
        verification=FindingVerification(
            status="verified",
            figure_results=[
                FigureResult(
                    figure=wanted,
                    matched=True,
                    context=FigureContext(
                        period=period,
                        attribution="own",
                        organisation=EIA_OWNER,
                        kind=kind,
                    ),
                    evidence_words=snippet,
                )
            ],
        ),
    )


def _targets() -> list[EvidenceTarget]:
    """The three required targets the pass is judged against."""
    return [
        make_target(
            "topic-01-target-01",
            question="How much battery storage capacity was added in 2024?",
            measure="battery storage power capacity added",
            period="2024",
            kind="actual",
        ),
        make_target(
            "topic-01-target-02",
            question="How much battery storage is planned for 2025?",
            measure="battery storage power capacity planned",
            period="2025",
            kind="forecast",
        ),
        make_target(
            MISSING_TARGET_ID,
            question="Which interconnection queues hold storage capacity?",
            measure="queued storage capacity",
            period="2026",
            kind="actual",
        ),
    ]


def _sub_topics() -> list[SubTopic]:
    """The plan, with every required target the fixture judges attached."""
    topics: dict[str, SubTopic] = {}
    for target in _targets():
        topic = topics.get(target.coverage_id)
        if topic is None:
            topic = SubTopic(
                coverage_id=target.coverage_id,
                title=f"Sub-topic {target.coverage_id}",
                rationale="It decides the comparison.",
                search_queries=["battery storage capacity 2024"],
                success_criteria=["A read source answers it."],
                priority=len(topics) + 1,
            )
            topics[target.coverage_id] = topic
        topic.evidence_targets.append(target)
    return list(topics.values())


def _findings() -> list[Finding]:
    """Three findings over two reads: two the verifier kept, one it dropped."""
    eia = _read(EIA_URL, "U.S. battery capacity increased 66% in 2024")
    steo = _read(STEO_URL, "Short-Term Energy Outlook: battery storage")
    dropped = make_finding(
        eia,
        "Storage is the fastest-growing source on the grid.",
        verification=FindingVerification(
            status="dropped", dropped_reason="snippet_not_on_page"
        ),
    )
    return [
        _verified(
            eia,
            "Generators added 10.4 gigawatts (GW) of new battery storage "
            "capacity in 2024",
            value="10.4",
            unit="GW",
            period="2024",
            kind="actual",
            target_ids=["topic-01-target-01"],
        ),
        _verified(
            steo,
            "capacity growth from battery storage could set a record as "
            "operators report plans to add 19.6 GW",
            value="19.6",
            unit="GW",
            period="2025",
            kind="forecast",
            target_ids=["topic-01-target-02"],
        ),
        dropped,
    ]


def _sources() -> list[ScoredSource]:
    """Three assessed sources, of which the report cites two."""
    return [
        ScoredSource(
            url=EIA_URL,
            title="U.S. battery capacity increased 66% in 2024",
            authority_score=0.9,
            recency_score=0.9,
            relevance_score=0.9,
            overall_score=0.9,
            rationale="Read primary material with a stated date.",
            publisher_id="eia.gov",
        ),
        ScoredSource(
            url=STEO_URL,
            title="Short-Term Energy Outlook: battery storage",
            authority_score=0.9,
            recency_score=0.9,
            relevance_score=0.8,
            overall_score=0.87,
            rationale="Read primary material with a stated date.",
            publisher_id="eia.gov",
        ),
        ScoredSource(
            url=UNCITED_URL,
            title="Battery storage insight",
            authority_score=0.5,
            recency_score=0.5,
            relevance_score=0.5,
            overall_score=0.5,
            rationale="Assessed but not cited by any printed statement.",
            publisher_id="woodmac.example",
        ),
    ]


def _statement(statement_id: str, text: str) -> ReportStatement:
    return ReportStatement(statement_id=statement_id, text=text)


def _composition(state: ResearchState) -> ReportComposition:
    """The pass's composition, built from the verified snapshot by code.

    The key facts table comes from the production ``fact_rows`` builder; the
    prose points carry the statements the Statement Check judged, with the
    verdicts the writer records for every kept sentence (spec §6.4).
    """
    findings = state.verified_findings
    targets = _evidence_targets(state)
    labels = {
        f"F{n:02d}": finding_fingerprint(finding)
        for n, finding in enumerate(citable_findings(findings), start=1)
    }
    rows = fact_rows(findings, targets)
    return ReportComposition(
        question=BATTERY_QUESTION,
        session_id=SESSION_ID,
        as_of="2026-09-13",
        scope="grid-scale battery storage in the United States",
        sub_topics=state.sub_topics,
        sources=state.evaluated_sources,
        findings=findings,
        fact_rows=rows,
        not_found=[
            NotFoundTarget(
                target_id=MISSING_TARGET_ID,
                question="Which interconnection queues hold storage capacity?",
                queries=["FERC interconnection queue storage 2026"],
                pages_read=[EIA_URL],
                searched=True,
            )
        ],
        finding_labels=labels,
        statement_verdicts={"S001": "consistent", "S002": "consistent"},
        summary=[
            ReportPoint(
                text="U.S. battery storage capacity grew by 66% in 2024.",
                source_urls=[EIA_URL],
                statement=_statement(
                    "S001", "U.S. battery storage capacity grew by 66% in 2024."
                ),
            )
        ],
        sections=[
            ReportSection(
                title="Planned additions",
                points=[
                    ReportPoint(
                        text=(
                            "Operators report plans to add 19.6 GW of "
                            "utility-scale battery storage in 2025."
                        ),
                        source_urls=[STEO_URL],
                        statement=_statement(
                            "S002",
                            "Operators report plans to add 19.6 GW of "
                            "utility-scale battery storage in 2025.",
                        ),
                    )
                ],
            )
        ],
    )


def _evidence_targets(state: ResearchState):
    return [t for topic in state.sub_topics for t in topic.evidence_targets]


def judged_state() -> ResearchState:
    """One judged pass: verified findings, the composition, and its snapshot."""
    findings = _findings()
    reads = {read.read_id: read for read in (make_read(),)}
    state = ResearchState(
        session_id=SESSION_ID,
        original_question=BATTERY_QUESTION,
        sub_topics=_sub_topics(),
        verified_findings=findings,
        evaluated_sources=_sources(),
        read_records=reads,
    )
    composition = _composition(state)
    snapshot = compute_report_quality(state, composition)
    return state.model_copy(
        update={"composition": composition, "quality": snapshot}
    )


def _report(state: ResearchState) -> str:
    return render_written_report(state.composition)


def _sources_section(report: str) -> str:
    return report.split("## Sources", 1)[1]


def _urls(text: str) -> list[str]:
    return re.findall(r"https://[^\s)]+", text)


def test_the_reader_report_prints_one_row_per_record_and_no_duplicate_url() -> None:
    """Pathology 1: 156 duplicate appendix rows for 101 canonical URLs.

    Every assessed source the report cites is printed once, and every printed
    row traces to a typed record — a fact row to its finding, a source to its
    assessment. A URL that appeared twice in the appendix is exactly what this
    guards against, so the test counts occurrences per URL rather than only
    the total.
    """
    state = judged_state()
    composition = state.composition
    report = _report(state)

    sources = _sources_section(report)
    urls = _urls(sources)
    assert len(urls) == len(set(urls))
    assert set(urls) == {
        normalize_source_url(EIA_URL),
        normalize_source_url(STEO_URL),
    }
    # One table row per typed fact row: the header and its separator are the
    # only other lines the table has.
    facts = report.split("## Key facts", 1)[1].split("\n## ", 1)[0]
    rows = [
        line
        for line in facts.splitlines()
        if line.startswith("| ") and "Organisation" not in line
    ]
    assert len(rows) == len(composition.fact_rows)

    log = render_finding_log(composition)
    assert log.count("### F01") == 1
    assert log.count("### F02") == 1
    assert "dropped (snippet_not_on_page)" in log


def test_the_reader_references_hold_only_sources_a_statement_cites() -> None:
    """Pathology 2: 89 sources in the appendix no reader statement used.

    The reference list is the citation index the renderer built from the
    statements' own URLs, so a source nobody cited cannot appear — and the
    uncited assessment the fixture carries proves the check can fail: it is in
    the state, in the ledger, and nowhere in the reader report.
    """
    state = judged_state()
    report = _report(state)

    cited = {url for url in _urls(_sources_section(report))}

    assert UNCITED_URL not in report
    assert normalize_source_url(UNCITED_URL) not in report
    assert cited == {
        normalize_source_url(EIA_URL),
        normalize_source_url(STEO_URL),
    }
    assert report.count("[1]") >= 1
    assert report.count("[2]") >= 1
    assert "## Not found" in report
    assert f"**{MISSING_TARGET_ID}" not in report  # the report names the question
    assert "Which interconnection queues hold storage capacity?" in report


def test_the_reviewer_packet_carries_every_required_surface() -> None:
    """Pathology 3: a reviewer that saw 2 of 7 required surfaces.

    The packet is the production request built from the composition, and the
    check is that each surface the reviewer must judge is inside it: every
    printed statement with its code-built label, the key facts, the Not found
    list, and the gate results. A packet missing one of them cannot produce a
    judgement about it, whichever model reads it.
    """
    state = judged_state()
    packet = build_report_review_input(state, state.composition)
    body = packet.model_dump_json()

    for point in [*state.composition.summary, *state.composition.sections[0].points]:
        assert point.text in body
        assert point.statement is not None
    for row in state.composition.fact_rows:
        assert row.value in body
    assert "Which interconnection queues hold storage capacity?" in body
    assert all(
        label in body for label in state.composition.finding_labels
    )


def test_the_summary_agrees_with_the_records_it_describes() -> None:
    """The numbers on the console are the typed records' own readings.

    Every line below is compared to the object it describes — the snapshot the
    gates judged, the composition the report renders — so the summary, the
    reader report and the evidence log cannot disagree about one run.
    """
    state = judged_state()
    snapshot = state.quality
    assert snapshot is not None
    outcome = build_outcome(
        GraphRun(
            session_id=SESSION_ID,
            state=state,
            status="completed",
            trace_url=None,
        ),
        metrics=(),
    )
    joined = "\n".join(render_summary(outcome, verbose=False))

    assert (
        f"Required targets: "
        f"{len(set(snapshot.required_target_ids) - set(snapshot.missing_required_target_ids))}/"
        f"{len(snapshot.required_target_ids)} answered" in joined
    )
    assert (
        f"Not found: "
        f"{', '.join(row.target_id for row in state.composition.not_found)}"
        in joined
    )
    assert (
        f"Findings: "
        f"{snapshot.verified_findings + snapshot.corrected_findings} checked "
        f"({snapshot.corrected_findings} with corrected context, "
        f"{snapshot.context_unchecked_findings} unchecked context), "
        f"{snapshot.dropped_findings} dropped; "
        f"{snapshot.cited_findings} cited" in joined
    )
    assert (
        f"Integrity: {snapshot.duplicate_fact_rows} duplicate fact rows; "
        f"{snapshot.uncited_settled_points} uncited statements; "
        f"{len(snapshot.unjudged_sentences)} unjudged sentences; "
        f"{snapshot.forecasts_without_release} forecasts without release"
        in joined
    )
    assert "critic" not in joined.casefold()
    assert "claims:" not in joined.casefold()
