"""Tests for the three published artifacts: the written report, the finding log
and the quality JSON.

The written report is what a decision-maker reads: one cited summary, the Key
facts table, the findings sections, and the required targets nothing answered.
The finding log is the same pass read closely: every finding with its snippet
and its verification, every dropped figure, and every refused sentence in full.
The quality record is the replay surface over both: the verified findings, the
gate snapshot as the type records it, the reviewer's own judgement, and the
hashes of the two Markdown documents published beside it.

Nothing here performs I/O -- the two Markdown artifacts and the record are pure
functions of the composition handed to them (and, for the record, the state and
the review) -- so all three are asserted directly.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import tempfile
from pathlib import Path

import pytest

from deep_research.agents.evidence_verifier import (
    StatementCheckDraft,
    StatementVerdictDraft,
)
from deep_research.agents.identity import finding_fingerprint
from deep_research.agents.quality import compute_report_quality
from deep_research.agents.report import (
    QUALITY_RECORD_TEXT_CHARS,
    Citation,
    ReportComposition,
    ReportPoint,
    ReportSection,
    canonical_sources,
    citation_markers,
    render_citations,
    render_finding_log,
    render_quality_json,
    render_quality_record,
    render_written_report,
    report_as_of,
    report_scope,
    written_citations,
)
from deep_research.agents.report_writer import (
    REPORT_WRITER_NAME,
    ReportWriterAgent,
    ReportWriterDraft,
    WriterPointDraft,
    WriterSectionDraft,
    compose_written_report,
)
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import LangSmithRuntimeConfig, Tracker
from deep_research.utils.config import AgentRuntimeConfig
from deep_research.utils.types import (
    LEGACY_QUALITY_CONTRACT_VERSION,
    QUALITY_CONTRACT_VERSION,
    REVIEW_DIMENSIONS,
    FactRow,
    FigureContext,
    FigureResult,
    Finding,
    FindingVerification,
    NotFoundTarget,
    RejectedDraftPoint,
    ReportReview,
    ReportStatement,
    ResearchError,
    ResearchState,
    ReviewDefect,
    ScoredSource,
    SubTopic,
)
from tests.agent_fakes import ScriptedCompleter
from tests.evidence_fakes import figure, make_finding, make_read, make_target
from tests.research_fakes import report_writer_tools

EXTRACTED_AT = "2026-08-01T12:00:00+00:00"
SOURCE_URL = "https://example.org/a"
OTHER_URL = "https://other.test/b"

EIA = "U.S. Energy Information Administration"
EIA_URL = "https://www.eia.gov/todayinenergy/detail.php?id=64705"
STEO_URL = "https://ent.news/2025/1/940.pdf"
BATTERY_QUESTION = (
    "How much battery storage capacity was added in the United States in 2024, "
    "and how much is expected in 2025?"
)


def _source(
    *,
    url: str = SOURCE_URL,
    title: str = "QEC 2025",
    overall: float | None = 0.76,
) -> ScoredSource:
    return ScoredSource(
        url=url,
        title=title,
        authority_score=0.8,
        recency_score=0.7,
        relevance_score=0.9,
        overall_score=overall,
        rationale="Peer-reviewed and corroborated.",
    )


# --- the pathological record set ---------------------------------------------

PATHOLOGICAL_CANONICAL_SOURCES = 101
PATHOLOGICAL_SOURCE_RECORDS = 257


def _pathological_sources() -> list[ScoredSource]:
    """101 canonical URLs behind 257 records, exactly the observed shape."""
    return [
        _source(
            url=f"https://example.test/source-{record % 101 + 1:03d}",
            title=f"Source {record % 101 + 1:03d}",
            overall=0.60 + (record % 20) / 100,
        )
        for record in range(PATHOLOGICAL_SOURCE_RECORDS)
    ]


# --- fixtures ----------------------------------------------------------------


def _verified_finding(
    url: str,
    snippet: str,
    *,
    value: str,
    unit: str,
    period: str,
    kind: str,
    organisation: str,
    target: str,
    attribution: str = "own",
    release_date: str | None = None,
) -> Finding:
    """One finding the Evidence Verifier kept, with its verified figure."""
    read = make_read(snippet, url=url, title=f"{organisation} page")
    finding = make_finding(
        read,
        snippet,
        figures=[figure(value, unit, period, kind)],
        target_ids=[target],
        release_date=release_date,
    )
    result = FigureResult(
        figure=finding.figures[0],
        matched=True,
        evidence_words=snippet,
        context=FigureContext(
            period=period, attribution=attribution, organisation=organisation, kind=kind
        ),
    )
    return finding.model_copy(
        update={
            "verification": FindingVerification(
                status="verified", figure_results=[result]
            )
        }
    )


EIA_ACTUAL_2024 = _verified_finding(
    EIA_URL,
    "Generators added 10.4 gigawatts (GW) of new battery storage capacity in 2024.",
    value="10.4",
    unit="GW",
    period="2024",
    kind="actual",
    organisation=EIA,
    target="topic-01-target-01",
    release_date="2025-03-12",
)
STEO_FORECAST_2025 = _verified_finding(
    STEO_URL,
    "Battery storage capacity grows by 14 GW in 2025.",
    value="14",
    unit="GW",
    period="2025",
    kind="forecast",
    organisation=EIA,
    target="topic-02-target-01",
    attribution="relayed",
    release_date="2025-01-15",
)


def _topic(coverage_id: str, target_id: str, **fields: object) -> SubTopic:
    """One planned sub-topic carrying its one evidence target."""
    return SubTopic(
        coverage_id=coverage_id,
        title=target_id,
        rationale="r",
        search_queries=["q"],
        success_criteria=["c"],
        priority=1,
        evidence_targets=[make_target(target_id, **fields)],
    )


def _point(
    text: str,
    *,
    statement_id: str = "S001",
    source_urls: list[str] | None = None,
    finding_ids: list[str] | None = None,
    target_ids: list[str] | None = None,
) -> ReportPoint:
    """One rendered point, with the statement the record replays it from."""
    return ReportPoint(
        text=text,
        source_urls=source_urls if source_urls is not None else [SOURCE_URL],
        statement=ReportStatement(
            statement_id=statement_id,
            text=text,
            finding_ids=finding_ids if finding_ids is not None else [],
            target_ids=target_ids if target_ids is not None else [],
        ),
    )


def _written_composition(**overrides: object) -> ReportComposition:
    """The composition one written pass produces, as the record reads it.

    Every statement cites a finding the composition carries, every fact row
    names one of those findings, and the one required target no finding
    answers is listed under Not found -- the shape the Report Writer publishes.
    """
    eia_id = finding_fingerprint(EIA_ACTUAL_2024)
    steo_id = finding_fingerprint(STEO_FORECAST_2025)
    payload: dict[str, object] = {
        "question": BATTERY_QUESTION,
        "session_id": "session-1",
        "iteration": 0,
        "as_of": EXTRACTED_AT,
        "scope": "United States",
        "sub_topics": [
            _topic("topic-01", "topic-01-target-01"),
            _topic("topic-02", "topic-02-target-01", kind="forecast", period="2025"),
            _topic("topic-09", "topic-09-target-01", period="2026"),
        ],
        "sources": [
            _source(url=EIA_URL, title="Today in Energy"),
            _source(url=STEO_URL, title="Short-Term Energy Outlook"),
        ],
        "findings": [EIA_ACTUAL_2024, STEO_FORECAST_2025],
        "fact_rows": [
            FactRow(
                row_id="K001",
                organisation=EIA,
                attribution="own",
                measure="battery storage power capacity added",
                period="2024",
                value="10.4 GW",
                kind="actual",
                release="released 2025-03-12",
                finding_id=eia_id,
                target_ids=["topic-01-target-01"],
            ),
            FactRow(
                row_id="K002",
                organisation=EIA,
                attribution="relayed",
                relay_host="ent.news",
                measure="battery storage power capacity added",
                period="2025",
                value="14 GW",
                kind="forecast",
                release="January 2025 STEO",
                finding_id=steo_id,
                target_ids=["topic-02-target-01"],
            ),
        ],
        "not_found": [
            NotFoundTarget(
                target_id="topic-09-target-01",
                question="What does Wood Mackenzie project for 2026?",
                queries=["Wood Mackenzie 2026 storage forecast"],
                pages_read=["https://www.woodmac.com/press-releases/2025-record"],
                searched=True,
            )
        ],
        "finding_labels": {"F01": eia_id, "F02": steo_id},
        "statement_verdicts": {"S001": "consistent", "S002": "consistent"},
        "summary": [
            _point(
                "Generators added 10.4 GW of battery storage capacity in 2024.",
                statement_id="S001",
                source_urls=[EIA_URL],
                finding_ids=[eia_id],
                target_ids=["topic-01-target-01"],
            )
        ],
        "sections": [
            ReportSection(
                title="2025 outlook",
                points=[
                    _point(
                        "Battery storage capacity grows by 14 GW in 2025.",
                        statement_id="S002",
                        source_urls=[STEO_URL],
                        finding_ids=[steo_id],
                        target_ids=["topic-02-target-01"],
                    )
                ],
            )
        ],
    }
    payload.update(overrides)
    return ReportComposition.model_validate(payload)


def _section_body(markdown: str, heading: str) -> str:
    """The text between ``heading`` and the next H2 heading (or the end)."""
    start = markdown.index(heading)
    tail = markdown[start + len(heading) :]
    match = re.search(r"(?m)^## ", tail)
    return tail[: match.start()] if match else tail


# --- the quality record -------------------------------------------------------
#
# The third published artifact: one JSON document that makes the two Markdown
# documents auditable. It carries IDs rather than prose, hashes the bytes it
# describes without describing itself, and must serialize everything a replay
# needs to resolve a cited statement back to the finding it rests on.


def _record_state(composition: ReportComposition, **fields: object) -> ResearchState:
    """The state the terminal finalizer holds when it publishes the set."""
    state = ResearchState(
        session_id=composition.session_id,
        original_question=composition.question,
        sub_topics=list(composition.sub_topics),
        composition=composition,
        report=render_written_report(composition),
        report_evidence=render_finding_log(composition),
        quality_contract_version=QUALITY_CONTRACT_VERSION,
        **fields,  # type: ignore[arg-type]
    )
    return state.model_copy(
        update={"quality": compute_report_quality(state, composition)}
    )


def _artifact_texts(composition: ReportComposition) -> dict[str, str]:
    """The two Markdown artifacts, under the names publication writes them."""
    return {
        "reader_markdown": render_written_report(composition),
        "evidence_markdown": render_finding_log(composition),
    }


# --- identity, citation and plan helpers -------------------------------------


def test_canonicalization_collapses_repeated_records_in_first_seen_order() -> None:
    """257 source records are 101 canonical records, one row each."""
    canonical = canonical_sources(_pathological_sources())

    assert len(canonical) == PATHOLOGICAL_CANONICAL_SOURCES
    assert len({source.url for source in canonical}) == len(canonical)
    assert [source.url for source in canonical] == [
        f"https://example.test/source-{index:03d}"
        for index in range(1, PATHOLOGICAL_CANONICAL_SOURCES + 1)
    ]


def test_the_latest_recorded_timestamp_is_the_as_of_value() -> None:
    earlier = Finding(
        content="Earlier.",
        source_url=SOURCE_URL,
        source_title="QEC 2025",
        extracted_at="2026-07-01T09:00:00+00:00",
        confidence=0.5,
        related_sub_topic="Alpha",
    )
    later = earlier.model_copy(update={"extracted_at": EXTRACTED_AT})
    read = make_read("Break-even was reached.", url=SOURCE_URL, title="QEC 2025")

    assert report_as_of(findings=[earlier, later], reads=[]) == EXTRACTED_AT
    # A read newer than every finding is the newest evidence and wins; the
    # value comes from a read's retrieval time, never from a graph event.
    assert report_as_of(findings=[earlier], reads=[read]) == read.retrieved_at
    assert report_as_of(findings=[], reads=[]) == ""


def test_scope_is_stated_from_the_plan_alone() -> None:
    topic = _topic("topic-01", "topic-01-target-01")

    rendered = report_scope([topic])

    assert "topic-01" in rendered
    assert "topic-01-target-01" in rendered
    assert "no geography" in rendered.lower()
    assert report_scope([]) != ""


def test_citation_numbers_follow_first_use_in_the_written_report() -> None:
    """The summary is met first, so its page takes reference 1.

    The reader meets the summary, then the Key facts table, then the findings
    sections; one reference per page, numbered in that order.
    """
    composition = _written_composition(
        summary=[
            _point(
                "Costs fell.",
                statement_id="S001",
                source_urls=[OTHER_URL],
            )
        ],
        sections=[
            ReportSection(
                title="Both",
                points=[
                    _point(
                        "Break-even was reached.",
                        statement_id="S002",
                        source_urls=[SOURCE_URL],
                    )
                ],
            )
        ],
    )

    index = written_citations(composition)

    assert [citation.number for citation in index] == [1, 2, 3, 4]
    assert [citation.url.split("/")[2] for citation in index] == [
        "other.test",
        "eia.gov",
        "ent.news",
        "example.org",
    ]
    sources = _section_body(render_written_report(composition), "## Sources")
    assert sources.index(OTHER_URL) < sources.index(SOURCE_URL)


def test_markers_render_sorted_and_deduplicated() -> None:
    index = [
        Citation(number=1, url=SOURCE_URL, title="QEC 2025"),
        Citation(number=2, url=OTHER_URL, title="Other study"),
    ]

    assert (
        citation_markers([OTHER_URL, SOURCE_URL, OTHER_URL], index) == "[1][2]"
    )
    assert citation_markers(["https://invented.test/x"], index) == ""


def test_citations_render_one_numbered_line_each() -> None:
    index = [Citation(number=1, url=SOURCE_URL, title="QEC 2025")]

    assert render_citations(index) == f"1. QEC 2025 — {SOURCE_URL}"
    assert render_citations([]) == "(no sources were cited)"


def test_a_citation_object_rejects_a_zero_number() -> None:
    with pytest.raises(ValueError):
        Citation(number=0, url=SOURCE_URL, title="A")


# --- the finding log ----------------------------------------------------------


def test_the_finding_log_shows_the_full_drafted_text_of_a_refused_sentence() -> (
    None
):
    """A refusal is published whole: the drafted text, the labels it cited and
    the reason, never truncated to the terse reason alone.
    """
    long_text = " ".join(["A refused figure that was drafted."] * 10)
    assert len(long_text) > QUALITY_RECORD_TEXT_CHARS
    log = render_finding_log(
        _written_composition(
            rejected_points=[
                RejectedDraftPoint(
                    where="summary[1]",
                    text=long_text,
                    finding_labels=["F03"],
                    reason="an unsupported figure",
                )
            ]
        )
    )

    body = _section_body(log, "## Refused sentences")

    assert long_text in body
    assert "F03" in body
    assert "an unsupported figure" in body


# --- the quality record -------------------------------------------------------


def test_the_quality_record_publishes_each_statements_target_bindings() -> None:
    """A statement bound to no obligation is distinguishable, from the record
    alone, from one bound to an obligation nobody answered -- which is exactly
    the difference a coverage reading turns on.
    """
    composition = _written_composition(
        summary=[
            _point(
                "The pilot added 12 GW.",
                statement_id="S001",
                target_ids=["topic-01-target-01"],
            )
        ]
    )

    record = render_quality_record(_record_state(composition), composition, None)

    row = next(
        row for row in record["statements"] if row["statement_id"] == "S001"
    )
    assert row["target_ids"] == ["topic-01-target-01"]


def test_the_quality_record_publishes_refused_sentences_in_full() -> None:
    """The record's companion to the finding log's refusals: the same full
    text, the labels it cited and the reason, keyed by where it was drafted.
    """
    composition = _written_composition(
        rejected_points=[
            RejectedDraftPoint(
                where="summary[1]",
                text="A refused drafted sentence.",
                finding_labels=["F03"],
                reason="no evidence for this cell",
            )
        ]
    )

    record = render_quality_record(_record_state(composition), composition, None)

    assert record["refused_sentences"] == [
        {
            "where": "summary[1]",
            "text": "A refused drafted sentence.",
            "finding_labels": ["F03"],
            "reason": "no evidence for this cell",
        }
    ]


def test_the_quality_record_publishes_statement_text_uncut() -> None:
    """Nothing in the record is clipped a second time.

    A 289-character sentence was published cut mid-sentence at
    ``QUALITY_RECORD_TEXT_CHARS``, and the cut travelled: it reached the
    writer's packet and the reader's report. A recorded sentence is published
    as the pass recorded it.
    """
    text = (
        "PUDL covers electric power plants with 1 megawatt or greater "
        "combined nameplate capacity that are connected to the local or "
        "regional electric power grid, which is the respondent scope this "
        "pass recorded in full and which answers the question's third "
        "obligation about the grid-connection rule in the same sentence."
    )
    assert len(text) > QUALITY_RECORD_TEXT_CHARS
    composition = _written_composition(
        summary=[_point(text, statement_id="S001")]
    )

    record = render_quality_record(_record_state(composition), composition, None)

    row = next(
        row for row in record["statements"] if row["statement_id"] == "S001"
    )
    assert row["text"] == text


def test_the_quality_record_carries_the_session_status_it_was_given() -> None:
    """An operator reading only the record must see the run's own status, and a
    review nobody made is ``None`` rather than a clean bill of health.
    """
    composition = _written_composition()

    record = render_quality_record(
        _record_state(composition), composition, None, session_status="failed"
    )

    assert record["session_status"] == "failed"
    assert record["review"] is None


def test_a_record_without_a_session_status_says_so_rather_than_guessing() -> None:
    """No stamp is not a clean run: ``session_status`` stays empty."""
    composition = _written_composition()

    record = render_quality_record(_record_state(composition), composition, None)

    assert record["session_status"] == ""


def test_the_quality_record_registers_every_error_the_pass_recorded() -> None:
    """A replay must be able to find the failures the run continued past.

    The record publishes them by type, source and severity -- the fields a
    caller can address without parsing a message. ``details`` follow the one
    publication decision (``_published_details``) the finding log shares, so an
    unvetted type's details stay out of both.
    """
    composition = _written_composition(
        errors=[
            ResearchError(
                error_type="planner_plan_defects_unresolved",
                source="agent.planner",
                message="The plan stands with its recorded defects.",
                recoverable=True,
                details={
                    "stage": "confirming_review",
                    "plan": "review_repair",
                    "problems": ["review_repair: an unvetted model sentence"],
                },
            ),
            ResearchError(
                error_type="graph_planning_failed",
                source="graph.planning",
                message="Planning failed.",
                recoverable=False,
                details={},
            ),
        ]
    )
    state = _record_state(composition, errors=list(composition.errors))

    record = render_quality_record(state, composition, None)

    assert record["errors"] == [
        {
            "error_type": "planner_plan_defects_unresolved",
            "source": "agent.planner",
            "severity": "recoverable",
            "message": "The plan stands with its recorded defects.",
            "details": "—",
        },
        {
            "error_type": "graph_planning_failed",
            "source": "graph.planning",
            "severity": "fatal",
            "message": "Planning failed.",
            "details": "—",
        },
    ]


def test_the_quality_record_resolves_every_id_it_publishes() -> None:
    """Every id the record publishes resolves inside the record itself.

    A replay reads the record alone: the statement rows it publishes, the
    finding registry those rows cite, and the fact rows keyed by the same
    registry. Every link is asserted on the serialized rows rather than on the
    composition that produced them -- a record whose statement rows carried no
    finding ids is exactly the regression this test exists to catch, and
    reading the composition would not see it.
    """
    composition = _written_composition()

    record = render_quality_record(_record_state(composition), composition, None)

    finding_ids = {row["id"] for row in record["findings"]}
    statements = {row["statement_id"]: row for row in record["statements"]}

    assert finding_ids == set(composition.finding_labels.values())
    assert set(statements) == {
        statement.statement_id for statement in composition.statements
    }
    cited = {
        finding_id
        for row in record["statements"]
        for finding_id in row["finding_ids"]
    }
    # At least one statement row carries a link: an emptied list on every row
    # would otherwise satisfy every resolution check below.
    assert cited
    assert cited <= finding_ids
    for row in record["fact_rows"]:
        assert row["finding_id"] in finding_ids, row["row_id"]
    assert {row["target_id"] for row in record["not_found"]} == {
        target.target_id for target in composition.not_found
    }


def test_the_quality_record_hashes_the_published_bytes_and_never_itself() -> None:
    """Artefact hashes are of the final bytes, and the JSON has no self-hash.

    A document cannot carry the digest of the bytes that contain that digest,
    so the quality JSON is deliberately outside its own ``artifacts`` map. The
    two Markdown digests are recomputed here from the exact strings published.
    """
    composition = _written_composition()
    state = _record_state(composition)
    texts = _artifact_texts(composition)

    record = render_quality_record(state, composition, None, artifacts=texts)
    encoded = json.dumps(record, sort_keys=True, ensure_ascii=False)

    assert set(record["artifacts"]) == {
        "reader_markdown",
        "evidence_markdown",
    }
    for name, text in texts.items():
        assert record["artifacts"][name] == hashlib.sha256(
            text.encode("utf-8")
        ).hexdigest()
    own_digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    assert own_digest not in encoded
    assert "quality_json" not in record["artifacts"]


def test_the_semantic_fingerprint_moves_on_judged_content_not_on_the_badge() -> (
    None
):
    """The record's fingerprint covers the material a review judges, and only that.

    ``quality_status`` is presentation: the terminal finalizer rewrites it on
    the way out, and a judgement must survive that. The plan is not judged
    content either -- the reviewer never sees it -- while the statements, the
    fact rows, the Not found list and the finding ids the report cites are
    exactly what a review reads, so each of those moves the fingerprint.
    """
    composition = _written_composition()

    def fingerprint(candidate: ReportComposition) -> object:
        return render_quality_record(
            _record_state(candidate), candidate, None
        )["configuration"]["composition_fingerprint"]

    base = fingerprint(composition)
    assert base
    assert (
        fingerprint(composition.model_copy(update={"quality_status": "accepted"}))
        == base
    )
    assert (
        fingerprint(
            composition.model_copy(
                update={
                    "sub_topics": [
                        *composition.sub_topics,
                        _topic("topic-10", "topic-10-target-01"),
                    ]
                }
            )
        )
        == base
    )

    for field, value in (
        ("summary", [_point("A materially different summary sentence.")]),
        ("fact_rows", []),
        ("not_found", []),
        ("findings", [EIA_ACTUAL_2024]),
    ):
        changed = composition.model_copy(update={field: value})
        assert fingerprint(changed) != base, field


def test_the_quality_record_is_bounded_json_without_page_payloads() -> None:
    """It is JSON, and a whole extracted page never enters it."""
    page = "the complete extracted page text " * 200
    snippet = "Generators added 10.4 GW of new battery storage capacity in 2024."
    read = make_read(page, url=EIA_URL, title="Today in Energy")
    finding = make_finding(
        read,
        snippet,
        figures=[figure("10.4", "GW", "2024", "actual")],
        target_ids=["topic-01-target-01"],
    )
    finding_id = finding_fingerprint(finding)
    composition = _written_composition(
        findings=[finding],
        finding_labels={"F01": finding_id},
        fact_rows=[],
        sections=[],
        summary=[
            _point(
                snippet,
                statement_id="S001",
                source_urls=[EIA_URL],
                finding_ids=[finding_id],
                target_ids=["topic-01-target-01"],
            )
        ],
    )
    state = _record_state(
        composition, read_records={read.read_id: read}
    )

    record = render_quality_record(state, composition, None)
    encoded = json.dumps(record, sort_keys=True)

    assert record["findings"] and record["statements"]
    assert page not in encoded
    assert json.loads(encoded) == record


# --- the quality record a written pass publishes (§6.2; Task 4.5) -------------
#
# The record is read from the composition the Report Writer produces -- built
# here the way the writer's own tests build it -- never from a stand-in, so
# these tests see what the terminal publication writes.


def _record_tracker() -> Tracker:
    """The no-op tracker the writer needs to build a task and fingerprint calls."""
    return Tracker(
        LangSmithRuntimeConfig(
            tracing_enabled=False, project="agent-tests", api_key=None
        )
    )


_WRITTEN_DRAFT = ReportWriterDraft(
    executive_summary=[
        WriterPointDraft(
            text=(
                "Generators added 10.4 GW of battery storage capacity in the "
                "United States in 2024."
            ),
            finding_labels=["F01"],
        )
    ],
    sections=[
        WriterSectionDraft(
            title="2025 outlook",
            points=[
                WriterPointDraft(
                    text="Battery storage capacity grows by 14 GW in 2025.",
                    finding_labels=["F02"],
                )
            ],
        )
    ],
)


def _statement_check_reply(messages: list, schema: type) -> StatementCheckDraft:
    """Answer the Statement Check's batch, refusing the last sentence.

    The labels are read out of the request the checker itself built, so the
    kept sentence and the refused one are both exercised whichever batch the
    real ``check_statements`` hands the provider; the refused sentence is a
    section point, so its section has nothing left to publish.
    """
    del schema
    labels = re.findall(r"## (S\d+)", messages[1].content)
    refused = labels[-1]
    return StatementCheckDraft(
        statements=[
            StatementVerdictDraft(
                label=label,
                verdict="inconsistent" if label == refused else "consistent",
                reason=(
                    "The sentence states a figure the cited finding does not state."
                    if label == refused
                    else "The sentence states only what its cited finding states."
                ),
            )
            for label in labels
        ]
    )


def written_state() -> ResearchState:
    """A state whose composition is the one the Report Writer produces.

    The scripted provider answers the writer's draft request and then the
    Statement Check's batch, as the writer's own tests drive them, so the
    composition holds one kept sentence, one refused sentence, the key facts
    and one required target no finding answers. The state then carries the two
    published artifacts and the snapshot of the pass that published them.
    """
    targets = [
        make_target(organisation=EIA),
        make_target(
            "topic-02-target-01",
            kind="forecast",
            period="2025",
            organisation=EIA,
        ),
        make_target(
            "topic-09-target-01", period="2026", organisation="Wood Mackenzie"
        ),
    ]
    topics = [
        SubTopic(
            coverage_id=target.coverage_id,
            title=target.target_id,
            rationale="r",
            search_queries=["q"],
            success_criteria=["c"],
            priority=number,
            evidence_targets=[target],
        )
        for number, target in enumerate(targets, start=1)
    ]
    state = ResearchState(
        session_id="session-1",
        original_question=BATTERY_QUESTION,
        sub_topics=topics,
        verified_findings=[EIA_ACTUAL_2024, STEO_FORECAST_2025],
        # What ``graph.state`` stamps on a new run, so the record publishes the
        # contract this build writes rather than the legacy default.
        quality_contract_version=QUALITY_CONTRACT_VERSION,
    )
    completer = ScriptedCompleter(outputs=[_WRITTEN_DRAFT, _statement_check_reply])
    tracker = _record_tracker()
    writer = ReportWriterAgent(
        provider=completer,
        tracker=tracker,
        scratchpad=ScratchpadMemory(
            session_id=state.session_id,
            agent_name=REPORT_WRITER_NAME,
            max_entries=20,
        ),
        # The writer declares these two; composing a report never publishes
        # through them, so the root only has to exist for the tool to be built.
        tools=report_writer_tools(
            tracker, output_root=Path(tempfile.mkdtemp(prefix="ev-t4-5-"))
        ),
        config=AgentRuntimeConfig(max_iterations=2, tool_budget=0),
    )

    async def compose() -> ReportComposition:
        task = writer.build_task(state)
        draft, _ = await writer.draft(task)
        return await compose_written_report(
            task, draft, provider=completer, fingerprint=writer.fingerprint_call
        )

    composition = asyncio.run(compose())
    state = state.model_copy(
        update={
            "composition": composition,
            "report": render_written_report(composition),
            "report_evidence": render_finding_log(composition),
        }
    )
    return state.model_copy(
        update={"quality": compute_report_quality(state, composition)}
    )


def test_the_quality_record_carries_the_verified_findings_and_refusals() -> None:
    state = written_state()
    record = json.loads(
        render_quality_json(
            state, state.composition, None, quality_status="partial"
        )
    )

    assert {
        "quality",
        "review",
        "findings",
        "fact_rows",
        "not_found",
        "statements",
        "refused_sentences",
    } <= set(record)
    assert record["refused_sentences"][0]["text"]
    assert record["refused_sentences"][0]["finding_labels"]
    assert all("verification" in row for row in record["findings"])
    assert not [key for key in record if key.startswith("claim")]


def test_the_quality_record_publishes_the_contract_version_the_state_carries() -> (
    None
):
    """The record publishes the state's own contract version, never a relabelling.

    A new run stamps ``utils.types.QUALITY_CONTRACT_VERSION`` onto its state
    (``graph.state``) and a snapshot written before the versioned contract
    keeps ``LEGACY_QUALITY_CONTRACT_VERSION``. The record must carry whichever
    of the two the pass actually holds -- in both stamping sites -- because a
    consumer that finds a step-4 record has to be told it is one, and a
    consumer handed a legacy session's record has to be told that instead of
    being given the current build's number.
    """
    state = written_state()
    legacy = state.model_copy(
        update={"quality_contract_version": LEGACY_QUALITY_CONTRACT_VERSION}
    )

    current = json.loads(render_quality_json(state, state.composition, None))
    historical = json.loads(render_quality_json(legacy, legacy.composition, None))

    assert current["quality_contract_version"] == QUALITY_CONTRACT_VERSION
    assert current["configuration"]["quality_contract_version"] == (
        QUALITY_CONTRACT_VERSION
    )
    assert historical["quality_contract_version"] == LEGACY_QUALITY_CONTRACT_VERSION
    assert historical["configuration"]["quality_contract_version"] == (
        LEGACY_QUALITY_CONTRACT_VERSION
    )


def test_the_quality_record_publishes_the_review_the_reviewer_recorded() -> None:
    """The judgement block is the reviewer's own, never a re-derivation.

    ``mean_score`` is the review's own property (the mean over the seven
    dimensions, and ``None`` without a full set), each disposition is the
    reviewer's reading of one statement, and each defect keeps the materiality
    the acceptance helper reads -- so the record and the gate that judged the
    same review cannot disagree about what was decided.
    """
    state = written_state()
    review = ReportReview(
        status="scored",
        dimensions=dict.fromkeys(REVIEW_DIMENSIONS, 0.9),
        defects=[
            ReviewDefect(
                defect_id="review-01",
                kind="presentation",
                severity="major",
                statement_ids=["S001"],
                problem="The summary repeats the table's own release wording.",
            )
        ],
        reviewed_statement_ids=["S001"],
        per_statement_dispositions={"S001": "supported"},
        missing_required_target_ids=["topic-09-target-01"],
        input_fingerprint="packet-1",
    )
    record = json.loads(render_quality_json(state, state.composition, review))

    published = record["review"]
    assert published["mean_score"] == pytest.approx(0.9)
    assert {key: value for key, value in published.items() if key != "mean_score"} == {
        "status": "scored",
        "dimensions": dict.fromkeys(REVIEW_DIMENSIONS, 0.9),
        "defects": [
            {
                "defect_id": "review-01",
                "kind": "presentation",
                "severity": "major",
                "material": True,
                "target_ids": [],
                "statement_ids": ["S001"],
                "problem": "The summary repeats the table's own release wording.",
            }
        ],
        "dispositions": {"S001": "supported"},
        "missing_required_target_ids": ["topic-09-target-01"],
    }
