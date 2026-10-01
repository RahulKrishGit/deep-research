"""Tests for the three published artifacts: the written report, the finding log
and the quality JSON.

The written report is what a decision-maker reads: the answer-first skeleton
of spec §3 -- title, evidence line, bottom line, the question-shaped table,
part sections, what could not be confirmed, and sources. The finding log is
the same pass read closely: the About-this-report audit block, every verified
figure, every finding with its snippet and verification, every dropped figure
and every refused sentence in full. The quality record is the replay surface
over both: the verified findings, the gate snapshot as the type records it,
the reviewer's own judgement, and the hashes of the two Markdown documents
published beside it.

Nothing here performs I/O -- the two Markdown artifacts and the record are pure
functions of the composition handed to them (and, for the record, the state and
the review) -- so all three are asserted directly. Fixtures build
``ReportComposition`` directly rather than through the Report Writer agent:
these renderers and the quality record are pure functions of the composition,
and the writer's own behaviour belongs to ``test_report_writer.py``.
"""

from __future__ import annotations

import hashlib
import json
import re

import pytest

from deep_research.agents.identity import finding_fingerprint
from deep_research.agents.quality import compute_report_quality
from deep_research.agents.report import (
    Citation,
    ReportComposition,
    ReportPoint,
    ReportSection,
    canonical_sources,
    citation_markers,
    render_finding_log,
    render_quality_json,
    render_quality_record,
    render_written_report,
    report_as_of,
    report_scope,
    written_citations,
)
from deep_research.agents.researcher import sub_topic_skipped_error
from deep_research.agents.sources import normalize_source_url
from deep_research.observability import (
    RunTelemetryCollector,
)
from deep_research.request_budget import RequestBudget
from deep_research.utils.types import (
    LEGACY_QUALITY_CONTRACT_VERSION,
    QUALITY_CONTRACT_VERSION,
    REVIEW_DIMENSIONS,
    FactRow,
    FigureContext,
    FigureResult,
    Finding,
    FindingVerification,
    ItemMark,
    NotFoundTarget,
    PageCredit,
    RejectedDraftPoint,
    ReportPart,
    ReportReview,
    ReportStatement,
    ReportTable,
    ResearchError,
    ResearchState,
    RunTelemetry,
    ReviewDefect,
    ScoredSource,
    SubTopic,
    TableCell,
    TableEntry,
    UnreachablePage,
)
from tests.evidence_fakes import figure, make_finding, make_read, make_target

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


def test_citation_numbers_follow_bottom_line_then_sections() -> None:
    """Notes-progress-report spec §7.5: ``written_citations`` order is bottom
    line, then the sections, then the table; with no table here, the bottom
    line's page takes reference 1.
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

    assert [citation.number for citation in index] == [1, 2]
    assert [citation.url.split("/")[2] for citation in index] == [
        "other.test",
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
    assert len(long_text) > 240
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

    A long sentence was published cut mid-sentence at an internal display
    bound, and the cut travelled: it reached the writer's packet and the
    reader's report. A recorded sentence is published as the pass recorded it.
    """
    text = (
        "PUDL covers electric power plants with 1 megawatt or greater "
        "combined nameplate capacity that are connected to the local or "
        "regional electric power grid, which is the respondent scope this "
        "pass recorded in full and which answers the question's third "
        "obligation about the grid-connection rule in the same sentence."
    )
    assert len(text) > 240
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


# --- the errors the record publishes (§6.2) -----------------------------------
#
# Two decisions live in ``agents/report.py`` and reach the reader through the
# quality record: ``_published_details`` decides which error details may be
# published at all, and ``error_reading`` decides which sentence a record gets
# when one error type has several causes. Task 4.10's sweep deleted the tests
# that held both decisions, because the ledger section they were written
# against is gone; the artifact below publishes both, so these restore them.


def test_the_quality_record_publishes_the_bounded_tool_failure_diagnosis() -> None:
    """A classified tool failure keeps its diagnosis; an unvetted one keeps none.

    The bounded scraper diagnosis is the only reason a ``web_scraper`` failure
    can be counted by class rather than merely counted, so the public record
    has to carry it -- otherwise classifying the failure buys the reader
    nothing. Every other type's details stay out: this artifact is public, and
    those values are not produced by a projection that revalidates them.
    """
    composition = _written_composition(
        errors=[
            ResearchError(
                error_type="agent_tool_failed",
                source="agent.researcher",
                message="web_scraper failed; the agent continued.",
                recoverable=True,
                details={
                    "tool": "web_scraper",
                    "iteration": 3,
                    "tool_error_type": "HTTPStatusError",
                    "attempts": 2,
                    "retries": 1,
                    "status_code": 503,
                    "content_type": "text/html",
                },
            ),
            ResearchError(
                error_type="planner_plan_defects_unresolved",
                source="agent.planner",
                message="The plan stands with its recorded defects.",
                recoverable=True,
                details={"unvetted": "https://internal.example/secret"},
            ),
        ]
    )
    state = _record_state(composition, errors=list(composition.errors))

    record = render_quality_record(state, composition, None)

    rows = {row["error_type"]: row for row in record["errors"]}
    assert "tool_error_type=HTTPStatusError" in rows["agent_tool_failed"]["details"]
    assert "status_code=503" in rows["agent_tool_failed"]["details"]
    assert "content_type=text/html" in rows["agent_tool_failed"]["details"]
    assert "attempts=2" in rows["agent_tool_failed"]["details"]
    assert rows["agent_tool_failed"]["message"] == (
        "web_scraper failed; the agent continued."
    )
    assert rows["planner_plan_defects_unresolved"]["details"] == "—"


def test_the_quality_record_publishes_the_policy_refusal_reason() -> None:
    """A refusal's own sentence is the one thing it can publish.

    A run's rejected calls are otherwise indistinguishable from one another:
    the record said *that* a call was refused, never *why*, so a session whose
    searches were blocked by the acquisition policy read exactly like one whose
    guessed URLs were rejected. The reason is the policy's own code-generated
    sentence, bounded by the loop's summary limit before it is stored, which is
    the same evidence every other published key carries.
    """
    composition = _written_composition(
        errors=[
            ResearchError(
                error_type="agent_tool_policy_rejected",
                source="agent.researcher",
                message=(
                    "The acquisition policy rejected a requested tool action."
                ),
                recoverable=True,
                details={
                    "tool": "web_search",
                    "iteration": 5,
                    "policy_reason": (
                        "acquisition policy requires read before search"
                    ),
                },
            ),
        ]
    )
    state = _record_state(composition, errors=list(composition.errors))

    record = render_quality_record(state, composition, None)

    (row,) = record["errors"]
    assert row["error_type"] == "agent_tool_policy_rejected"
    assert "tool=web_search" in row["details"]
    assert "iteration=5" in row["details"]
    assert (
        "policy_reason=acquisition policy requires read before search"
        in row["details"]
    )


def test_the_quality_record_names_the_skipped_sub_topic_and_its_reason() -> None:
    """A skipped sub-topic must publish *which* one and *why*.

    It is the difference between a coverage gap and a deferral: a sub-topic
    the pass's own cap pushed out, a sub-topic a provider failure stopped the
    pass before, and a sub-topic an earlier pass already answered are three
    different states of the run. The locally-stamped ``coverage_id`` and the
    enumerated ``reason`` are what let a replay tell them apart, so both are
    published beside the sentence.
    """
    composition = _written_composition(
        errors=[
            sub_topic_skipped_error(
                _topic("topic-04", "Interconnection queue reform"), reason="cap"
            )
        ]
    )
    state = _record_state(composition, errors=list(composition.errors))

    record = render_quality_record(state, composition, None)

    details = record["errors"][0]["details"]
    assert "coverage_id=topic-04" in details
    assert "reason=cap" in details
    assert "priority=1" in details
    assert "sub_topic=Interconnection queue reform" in details


def test_the_quality_record_reads_each_skip_reason_distinctly() -> None:
    """One producer message, two readings: the reason decides the sentence.

    ``sub_topic_skipped_error`` writes the same message for every reason, and
    that message says the sub-topic was never researched -- which is true of a
    provider failure that stopped the pass and false of a sub-topic the pass's
    own cap deferred. Publishing the producer's sentence for both reports a
    capped run as a lost one, so the reading follows the enumerated reason.
    """
    composition = _written_composition(
        errors=[
            sub_topic_skipped_error(
                _topic("topic-04", "Interconnection queue reform"), reason="cap"
            ),
            sub_topic_skipped_error(
                _topic("topic-06", "Retirement schedules"),
                reason="provider_failure_stopped_processing",
            ),
        ]
    )
    state = _record_state(composition, errors=list(composition.errors))

    record = render_quality_record(state, composition, None)

    assert [row["message"] for row in record["errors"]] == [
        "This planned sub-topic was deferred: the pass reached its sub-topic "
        "limit before its turn came up.",
        "A planned sub-topic was never researched; a provider failure stopped "
        "the pass before it could run.",
    ]
    # The reading is beside the record's own typed details, not instead of
    # them: which pass stopped, and which sub-topic it cost, are both still
    # addressable without parsing the sentence.
    stopped_details = record["errors"][1]["details"]
    assert "coverage_id=topic-06" in stopped_details
    assert "reason=provider_failure_stopped_processing" in stopped_details


# --- the quality record a written pass publishes (§6.2; Task 4.5) -------------
#
# Built directly as ``ReportComposition``/``ResearchState``: this record is a
# pure function of the composition and the state, so a hand-built pass with
# one kept sentence, one refused sentence, the fact rows and one required
# target no finding answers exercises exactly what the terminal publication
# writes, without depending on the Report Writer agent's own machinery
# (``test_report_writer.py``'s concern).


def written_state() -> ResearchState:
    """A hand-built state whose composition matches the shape a written pass
    produces: one kept sentence, one refused sentence, the fact rows and one
    required target no finding answers.
    """
    composition = _written_composition(
        rejected_points=[
            RejectedDraftPoint(
                where="section[2025 outlook].points[1]",
                text="Battery storage capacity grows by 14 GW in 2025, the agency's best case.",
                finding_labels=["F02"],
                reason="a judgement with no named subject",
            )
        ],
        rejected=["a judgement with no named subject"],
    )
    return _record_state(composition)



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
                "resolution": None,
                "coverage_ids": [],
            }
        ],
        "dispositions": {"S001": "supported"},
        "missing_required_target_ids": ["topic-09-target-01"],
    }


def test_the_quality_record_surfaces_each_defects_resolution_and_coverage_ids() -> None:
    """T5 addendum item 4: a scoped re-review's own resolved/unresolved
    reading of a previous defect, and its carried ``coverage_ids``, are
    surfaced in the quality JSON's review record -- not only kept on the
    in-memory ``ReviewDefect`` for the acceptance gate to read. A fresh
    defect no scoped review has judged yet dumps ``resolution: null`` and
    whatever ``coverage_ids`` it carries.
    """
    state = written_state()
    review = ReportReview(
        status="scored",
        dimensions=dict.fromkeys(REVIEW_DIMENSIONS, 0.9),
        defects=[
            ReviewDefect(
                defect_id="review-01",
                kind="coverage",
                severity="major",
                target_ids=["topic-09-target-01"],
                problem="The question's second part names no answer.",
                coverage_ids=["topic-09"],
                resolution="resolved",
            ),
            ReviewDefect(
                defect_id="review-02",
                kind="contradiction",
                severity="major",
                statement_ids=["S001"],
                problem="The report states a rule its own findings qualify.",
                coverage_ids=["topic-02"],
                resolution="unresolved",
            ),
            ReviewDefect(
                defect_id="review-03",
                kind="presentation",
                severity="minor",
                statement_ids=["S002"],
                problem="A heading repeats the bottom line verbatim.",
            ),
        ],
        reviewed_statement_ids=["S001", "S002"],
        per_statement_dispositions={"S001": "supported", "S002": "supported"},
        input_fingerprint="packet-1",
    )

    record = json.loads(render_quality_json(state, state.composition, review))

    defects_by_id = {row["defect_id"]: row for row in record["review"]["defects"]}
    assert defects_by_id["review-01"]["resolution"] == "resolved"
    assert defects_by_id["review-01"]["coverage_ids"] == ["topic-09"]
    assert defects_by_id["review-02"]["resolution"] == "unresolved"
    assert defects_by_id["review-02"]["coverage_ids"] == ["topic-02"]
    assert defects_by_id["review-03"]["resolution"] is None
    assert defects_by_id["review-03"]["coverage_ids"] == []


def test_an_older_review_record_with_no_resolution_or_coverage_ids_still_loads() -> None:
    """A ``ReviewDefect`` built the way an older run's stored state would
    supply it -- with neither field named -- validates and dumps the same
    ``None``/``[]`` defaults a fresh defect gets, so a pre-addendum snapshot
    is read exactly as it was written."""
    legacy_defect = ReviewDefect.model_validate(
        {
            "defect_id": "review-01",
            "kind": "coverage",
            "severity": "major",
            "target_ids": ["topic-09-target-01"],
            "statement_ids": [],
            "problem": "The question's second part names no answer.",
        }
    )

    assert legacy_defect.resolution is None
    assert legacy_defect.coverage_ids == []

    state = written_state()
    review = ReportReview(
        status="scored",
        dimensions=dict.fromkeys(REVIEW_DIMENSIONS, 0.9),
        defects=[legacy_defect],
        reviewed_statement_ids=["S001"],
        per_statement_dispositions={"S001": "supported"},
        input_fingerprint="packet-1",
    )

    record = json.loads(render_quality_json(state, state.composition, review))

    (row,) = record["review"]["defects"]
    assert row["resolution"] is None
    assert row["coverage_ids"] == []


# --- the run's telemetry block (§7.3; Task 4.14b) -----------------------------


def run_telemetry_snapshot() -> RunTelemetry:
    """One run's telemetry, built by the collector from a real budget and a real call.

    Driven through the same two seams the providers use -- the budget's
    observer and ``record_call`` -- so a fixture cannot describe a shape the
    collector never emits.
    """
    collector = RunTelemetryCollector()
    budget = RequestBudget()
    budget.set_observer(collector.observe_budget)
    collector.note_call_starting("report_writer")
    budget.reserve("deepseek")
    collector.record_call(
        agent="report_writer",
        operation="structured_output",
        seconds=2.0,
        output_tokens=10,
        configured_cap=100,
        truncated=False,
    )
    return collector.snapshot()


def test_the_quality_record_carries_the_telemetry_block() -> None:
    """The run's §7.3 figures are published beside the pass's own judgements.

    The record is the replay surface for the whole run, so a reader with only
    this file can say what the peak was, how many calls the run had in flight,
    and which config key bounded the fullest reply — read from the state the
    terminal finalizer stamped, never re-derived here.
    """
    telemetry = run_telemetry_snapshot()
    state = written_state().model_copy(update={"run_telemetry": telemetry})

    record = render_quality_record(state, state.composition, None)

    assert record["telemetry"] == telemetry.model_dump(mode="json")


def test_a_run_without_a_collector_publishes_no_telemetry_figures() -> None:
    """``None``, never a row of zeroes.

    A harness that builds its providers directly records into a private
    collector nobody reads, so it took no measurement: zeros would publish a
    measured idle run in its place, and an absent figure must stay absent.
    """
    state = written_state()

    record = render_quality_record(state, state.composition, None)

    assert record["telemetry"] is None


def test_the_finding_log_shows_the_passage_the_statement_check_read() -> None:
    """Review F5: the checker reads the snippet's passage and its neighbours, so
    a verdict can rest on a sentence the snippet does not carry. The ledger
    prints the same bounded passage beside the snippet, and prints none for a
    composition whose writer had no reads in hand."""
    eia_id = finding_fingerprint(EIA_ACTUAL_2024)
    passage = ("Generators added 10.4 gigawatts (GW) of new battery storage capacity in 2024, "
               "and the agency expects 14 GW in 2025, its report states.")
    log = render_finding_log(_written_composition(statement_passages={eia_id: passage}))

    assert f'- Snippet: "{EIA_ACTUAL_2024.snippet}"' in log
    assert f'- Passage: "{passage}"' in log
    assert "its report states." in log

    plain = render_finding_log(_written_composition())
    assert "- Passage:" not in plain


def test_a_quoted_findings_evidence_log_entry_says_so() -> None:
    """D21: a no-figure finding the Evidence Verifier only quoted (its
    snippet is on the page, but neither check judged it for relevance or
    attribution) must never read as "verified" in the evidence log."""
    read = make_read("The grant covers travel.", url="https://example.test/grant",
                     title="Example grant page")
    quoted = make_finding(read, "The grant covers travel.").model_copy(
        update={"verification": FindingVerification(status="quoted")}
    )
    log = render_finding_log(_written_composition(
        findings=[EIA_ACTUAL_2024, STEO_FORECAST_2025, quoted],
    ))

    assert ("- Verification: quoted (snippet found on the page; not checked "
            "for context)") in log



# --- §9: the quality JSON's new fields (spec §14 T3) ---------------------------


def test_the_quality_record_publishes_answer_kind_table_parts_items_and_marks() -> None:
    """§9: ``answer_kind``, ``table``, ``sources``, ``parts``, each statement's
    ``part`` and ``items``, ``unreachable`` and ``dropped_marks``."""
    eia_id = finding_fingerprint(EIA_ACTUAL_2024)
    item = ItemMark(name="EIA", verdict="best positioned", picked=True, source_url=EIA_URL)
    statement = ReportStatement(
        statement_id="S001", text="EIA is best positioned to report this.",
        finding_ids=[eia_id], items=[item],
    )
    point = ReportPoint(text=statement.text, source_urls=[EIA_URL], statement=statement)
    table = ReportTable(
        shape="options", columns=["Option", "Recommended by"],
        rows=[[
            TableCell(text="EIA", statement_ids=["S001"], finding_ids=[eia_id]),
            TableCell(entries=[TableEntry(source_url=EIA_URL, date="2026-09-17")]),
        ]],
        caption="c",
    )
    composition = _written_composition(
        summary=[point],
        sections=[],
        table=table,
        answer_kind="comparison",
        parts=[ReportPart(coverage_id="topic-01", sub_topic_title="Sound", finding_ids=["F01"],
                          context_finding_ids=[], status="written")],
        unreachable=[UnreachablePage(url="https://denied.example.test/x", title="Denied", reason="access_denied")],
        dropped_marks=["S004: 'Model A' is not in the sentence"],
        page_credits={normalize_source_url(EIA_URL): PageCredit(publisher="QEC", date="2026-09-17", date_kind="published")},
    )

    record = render_quality_record(_record_state(composition), composition, None)

    assert record["answer_kind"] == "comparison"
    assert record["table"]["shape"] == "options"
    assert record["table"]["columns"] == ["Option", "Recommended by"]
    assert record["table"]["caption"] == "c"
    assert record["table"]["rows"][0][0]["text"] == "EIA"
    assert record["table"]["rows"][0][0]["finding_ids"] == [eia_id]
    assert record["table"]["rows"][0][1]["entries"][0]["source_url"] == EIA_URL
    assert record["parts"] == [{
        "coverage_id": "topic-01", "sub_topic_title": "Sound",
        "finding_ids": ["F01"], "context_finding_ids": [], "status": "written",
    }]
    assert record["unreachable"] == [
        {"url": "https://denied.example.test/x", "title": "Denied", "reason": "access_denied"}
    ]
    assert record["dropped_marks"] == ["S004: 'Model A' is not in the sentence"]
    statement_row = next(row for row in record["statements"] if row["statement_id"] == "S001")
    assert statement_row["part"] == "bottom_line"
    assert statement_row["items"] == [
        {"name": "EIA", "verdict": "best positioned", "picked": True,
         "source_url": EIA_URL, "finding_id": None}
    ]
    source_row = next(row for row in record["sources"] if row["url"] == normalize_source_url(EIA_URL))
    assert source_row["publisher"] == "QEC"
    assert source_row["date"] == "2026-09-17"


def test_a_section_statements_part_is_its_own_coverage_id() -> None:
    section_statement = ReportStatement(statement_id="S002", text="A section point.",
                                        finding_ids=[finding_fingerprint(STEO_FORECAST_2025)])
    section_point = ReportPoint(text=section_statement.text, source_urls=[STEO_URL], statement=section_statement)
    composition = _written_composition(
        sections=[ReportSection(title="Outlook", coverage_id="topic-02", points=[section_point])],
    )

    record = render_quality_record(_record_state(composition), composition, None)

    row = next(row for row in record["statements"] if row["statement_id"] == "S002")
    assert row["part"] == "topic-02"


def test_the_quality_record_stores_the_review_problem_whole() -> None:
    """D19: ``review.defects[*].problem`` is stored whole, never clamped."""
    long_problem = " ".join(["This report material defect explanation runs on at length."] * 10)
    assert len(long_problem) > 240
    composition = _written_composition()
    review = ReportReview(
        status="scored", dimensions=dict.fromkeys(REVIEW_DIMENSIONS, 0.9),
        defects=[ReviewDefect(defect_id="review-01", kind="presentation", severity="minor",
                              statement_ids=["S001"], problem=long_problem)],
        reviewed_statement_ids=["S001", "S002"],
        per_statement_dispositions={"S001": "supported", "S002": "supported"},
        input_fingerprint="packet-1",
    )

    record = render_quality_record(_record_state(composition), composition, review)

    assert record["review"]["defects"][0]["problem"] == long_problem


def test_the_quality_record_publishes_error_messages_whole() -> None:
    """Alongside D19: an error's message and source print in full too --
    ``report.py`` no longer clamps any published text (no strong limits)."""
    long_message = " ".join(["An unusually long recorded error message."] * 10)
    assert len(long_message) > 240
    composition = _written_composition(
        errors=[ResearchError(error_type="graph_planning_failed", source="graph.planning",
                              message=long_message, recoverable=False)]
    )
    state = _record_state(composition, errors=list(composition.errors))

    record = render_quality_record(state, composition, None)

    assert record["errors"][0]["message"] == long_message

