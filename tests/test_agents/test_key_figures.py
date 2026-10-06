"""Key figures.

The verified figures leave the bottom line for their own section after the topics:
each row labelled ``item · measure`` (never a quoted snippet), or -- for a row with no
named item -- by the source that reported it; the values one passage states about one
item merged, one row per label, at most ten rows.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from deep_research.agents import report_table
from deep_research.agents.identity import finding_fingerprint
from deep_research.agents.report import render_written_report
from deep_research.agents.report_table import (
    MAX_KEY_FIGURE_ROWS,
    build_table,
    key_figures_table,
    merge_key_figures,
)
from deep_research.utils.types import (
    EvidenceTarget,
    FactRow,
    FindingVerification,
    ReportComposition,
    ReportPoint,
    ReportSection,
    ReportStatement,
    SubTopic,
)
from tests.evidence_fakes import make_finding, make_read
from tests.test_api.replay_support import EXTRA_PASS_CASE, replay_outcome

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "latte-key-figures.json"


def _latte() -> ReportComposition:
    """The latte run's 140 fact rows and the findings they name, with each recorded
    finding rebuilt under a fingerprint of its own (the record keeps no finding
    body), and its nine planned targets in plan order."""
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    sub_topics = [
        SubTopic(
            coverage_id=topic["coverage_id"], title=topic["title"], rationale="not recorded",
            search_queries=["not recorded"], success_criteria=["not recorded"], priority=number,
            evidence_targets=[
                EvidenceTarget(target_id=target["target_id"], coverage_id=topic["coverage_id"],
                               question="not recorded", required=True, measure=target["measure"],
                               unit_dimension=target["unit_dimension"])
                for target in topic["targets"]
            ],
        )
        for number, topic in enumerate(data["sub_topics"], start=1)
    ]
    findings, renamed = [], {}
    for recorded in data["findings"]:
        read = make_read(f"Recorded finding {recorded['label']}.", url=recorded["source_url"],
                         title=recorded["label"])
        finding = make_finding(read, f"Recorded finding {recorded['label']}.",
                               content=f"Recorded finding {recorded['id']}.")
        finding = finding.model_copy(
            update={"verification": FindingVerification.model_validate(recorded["verification"])})
        renamed[recorded["id"]] = finding_fingerprint(finding)
        findings.append(finding)
    rows = [
        FactRow.model_validate({
            **row,
            "finding_id": renamed[row["finding_id"]],
            "duplicate_finding_ids": [renamed[i] for i in row["duplicate_finding_ids"]],
            "earlier": [{**e, "finding_id": renamed[e["finding_id"]]} for e in row["earlier"]],
        })
        for row in data["fact_rows"]
    ]
    return ReportComposition(question=data["question"], session_id="latte",
                             as_of="2026-09-30T20:16:05+00:00", sub_topics=sub_topics,
                             findings=findings, fact_rows=rows)


def test_key_figures_labels_and_merge_latte(monkeypatch: pytest.MonkeyPatch) -> None:
    composition = _latte()
    assert len(composition.fact_rows) == 140

    groups = merge_key_figures(composition.fact_rows, composition)

    bijan = [g for g in groups if g.label == "Bijan Bakery \u00b7 aggregate customer rating"]
    assert [(g.values, [row.row_id for row in g.rows]) for g in bijan] == [
        ("4.2 of 5 bubbles \u00b7 87 reviews", ["K003", "K004"]),
    ]
    assert all(len({row.finding_id for row in g.rows}) == 1 for g in groups)
    assert not any(g.label[:1] in {'"', "\u201c", "\u2026"} for g in groups)

    # With every row treated as eligible (eligibility reads finding bindings the
    # record does not keep), the printed section.
    monkeypatch.setattr(report_table, "_row_eligible", lambda *args, **kwargs: True)
    table = build_table(composition)
    assert table is not None
    assert table.columns == ["What", "Figure", "Source"]
    assert len(table.rows) <= MAX_KEY_FIGURE_ROWS
    labels = [row[0].text for row in table.rows]
    assert len(labels) == len(set(labels))
    bijan_finding = next(f for f in composition.findings if finding_fingerprint(f) == bijan[0].rows[0].finding_id)
    point = ReportPoint(
        text="Tripadvisor lists Bijan Bakery among San Jose's coffee shops.",
        source_urls=[bijan_finding.source_url],
        statement=ReportStatement(statement_id="S001", text="Tripadvisor lists Bijan Bakery among San Jose's coffee shops.",
                                  finding_ids=[finding_fingerprint(bijan_finding)]),
    )
    markdown = render_written_report(composition.model_copy(update={
        "table": table,
        "sections": [ReportSection(title="Published picks", coverage_id="topic-01", points=[point])],
    }))
    headings = [line for line in markdown.splitlines() if line.startswith("## ")]
    assert headings.index("## Published picks") < headings.index("## Key figures")
    figures = markdown.split("## Key figures\n\n", 1)[1].split("\n\n## ", 1)[0]
    assert figures.splitlines()[0] == "| What | Figure | Source |"
    assert "| Bijan Bakery \u00b7 aggregate customer rating | 4.2 of 5 bubbles \u00b7 87 reviews | tripadvisor.com [1] |" in figures


def _target(target_id: str, coverage_id: str, measure: str, unit_dimension: str | None = None) -> EvidenceTarget:
    return EvidenceTarget(target_id=target_id, coverage_id=coverage_id, question="q", required=True,
                          measure=measure, unit_dimension=unit_dimension)


def _topic(coverage_id: str, *targets: EvidenceTarget) -> SubTopic:
    return SubTopic(coverage_id=coverage_id, title=coverage_id, rationale="r", search_queries=["q"],
                    success_criteria=["c"], priority=1, evidence_targets=list(targets))


def _fact(row_id: str, *, finding_id: str = "f1", subject: str | None = "Example Cafe", value: str = "4.2 of 5",
          target_ids: tuple[str, ...] = (), measure: str = "row measure", period: str | None = None,
          kind: str = "actual") -> FactRow:
    return FactRow(row_id=row_id, organisation="Example Org", attribution="own", subject=subject, measure=measure,
                   period=period, value=value, kind=kind, finding_id=finding_id, target_ids=list(target_ids))


def _labels(composition: ReportComposition, *rows: FactRow) -> list[str]:
    return [g.label for g in merge_key_figures(list(rows), composition)]


def test_key_figures_measure_rule() -> None:
    plan = ReportComposition(question="q", session_id="s", sub_topics=[
        _topic("topic-01", _target("topic-01-target-01", "topic-01", "guide mentions")),
        _topic("topic-02", _target("topic-02-target-01", "topic-02", "review words"),
               _target("topic-02-target-02", "topic-02", "aggregate rating", unit_dimension="score"),
               _target("topic-02-target-03", "topic-02", "review count")),
        _topic("topic-03", _target("topic-03-target-01", "topic-03", "opening hours")),
    ])
    # The sub-topic owning most of the row's targets, and its target with a unit_dimension.
    majority = _fact("K001", target_ids=("topic-01-target-01", "topic-02-target-01", "topic-02-target-02"))
    # A tie goes to the earlier sub-topic in plan order.
    tie = _fact("K002", finding_id="f2", target_ids=("topic-03-target-01", "topic-01-target-01"))
    # No target with a unit_dimension: the sub-topic's first such target in plan order.
    plain = _fact("K003", finding_id="f3", target_ids=("topic-02-target-03", "topic-02-target-01"))
    # No planned target: the row's own measure.
    unplanned = _fact("K004", finding_id="f4", target_ids=("other-target",))
    assert _labels(plan, majority, tie, plain, unplanned) == [
        "Example Cafe \u00b7 aggregate rating", "Example Cafe \u00b7 guide mentions",
        "Example Cafe \u00b7 review words", "Example Cafe \u00b7 row measure",
    ]


def test_a_note_topics_row_is_labelled_by_the_notes_short_subject_not_its_question() -> None:
    """A research note's target measure is its whole question
    (``reader_notes.note_sub_topic``), so a row it answered printed "Item · {whole question}".
    The label takes the note topic's title without "Your note: ", trimmed; the target the
    agents read keeps its measure."""
    question = "Which San Jose cafés known for lattes also serve well-reviewed pastries?"
    note_target = _target("note-n1-target-01", "note-n1", question)
    note_topic = _topic("note-n1", note_target).model_copy(
        update={"title": f"Your note: {question.removesuffix('?').lower()}"})
    short_topic = _topic("note-n2", _target("note-n2-target-01", "note-n2", "pastries at the cafés?")).model_copy(
        update={"title": "Your note: pastries at the  cafés."})
    plan = ReportComposition(question="q", session_id="s", sub_topics=[
        _topic("topic-01", _target("topic-01-target-01", "topic-01", "aggregate rating")),
        note_topic, short_topic,
    ])

    assert _labels(
        plan,
        _fact("K001", subject="Bijan Bakery", target_ids=("note-n1-target-01",)),
        _fact("K002", subject="Bijan Bakery", target_ids=("note-n2-target-01",)),
        _fact("K003", subject="Bijan Bakery", target_ids=("topic-01-target-01",)),
    ) == [
        "Bijan Bakery \u00b7 which san jose caf\u00e9s known for lattes",
        "Bijan Bakery \u00b7 pastries at the caf\u00e9s",
        "Bijan Bakery \u00b7 aggregate rating",
    ]
    assert note_target.measure == question  # the target the agents read is unchanged


def test_a_note_topic_with_several_targets_keeps_one_row_per_target() -> None:
    """The per-topic short subject would label two rows about one
    item that answer different targets of one note alike, and the one-row-per-label rule
    would drop the second. A note topic with several targets labels each row by its
    own target's question, cut at 40 characters on a word boundary; one target keeps the
    topic's short subject."""
    def note_target(number: int, question: str) -> EvidenceTarget:
        return EvidenceTarget(target_id=f"note-n1-target-{number:02d}", coverage_id="note-n1",
                              question=question, required=True, measure=question)

    almond = "Which San Jose caf\u00e9s sell almond croissants?"
    pain = "Which San Jose caf\u00e9s sell pain au chocolat?"
    plan = [_topic("note-n1", note_target(1, almond), note_target(2, pain)).model_copy(
        update={"title": "Your note: pastries at the caf\u00e9s"})]
    findings = []
    for name in ("a", "b"):
        read = make_read(f"Page {name}.", url=f"https://{name}.example.test/p", title=name)
        findings.append(make_finding(read, f"Page {name}.", target_ids=["note-n1-target-01", "note-n1-target-02"])
                        .model_copy(update={"verification": FindingVerification(status="verified")}))
    ids = [finding_fingerprint(finding) for finding in findings]
    rows = [
        _fact("K001", finding_id=ids[0], subject="Bijan Bakery", value="3 almond croissants",
              target_ids=("note-n1-target-01",)),
        _fact("K002", finding_id=ids[1], subject="Bijan Bakery", value="2 pain au chocolat",
              target_ids=("note-n1-target-02",)),
    ]
    composition = ReportComposition(question="q", session_id="s", sub_topics=plan, findings=findings,
                                    fact_rows=rows)

    table = key_figures_table(composition)

    assert [(row[0].text, row[1].text, row[0].row_ids) for row in table.rows] == [
        ("Bijan Bakery \u00b7 Which San Jose caf\u00e9s sell almond", "3 almond croissants", ["K001"]),
        ("Bijan Bakery \u00b7 Which San Jose caf\u00e9s sell pain au", "2 pain au chocolat", ["K002"]),
    ]
    # A topic with one target still labels by its short subject.
    one = [_topic("note-n1", note_target(1, almond)).model_copy(update={"title": "Your note: pastries at the caf\u00e9s"})]
    assert _labels(composition.model_copy(update={"sub_topics": one}), rows[0].model_copy(
        update={"target_ids": ["note-n1-target-01"]})) == ["Bijan Bakery \u00b7 pastries at the caf\u00e9s"]
    # Questions that the cut would make read alike stay whole, so their rows stay apart.
    lattes_a = "Which San Jose caf\u00e9s known for lattes also serve almond croissants?"
    lattes_b = "Which San Jose caf\u00e9s known for lattes also serve pain au chocolat?"
    alike = composition.model_copy(update={"sub_topics": [_topic("note-n1", note_target(1, lattes_a),
                                                                 note_target(2, lattes_b))]})
    assert _labels(alike, *rows) == [
        "Bijan Bakery \u00b7 Which San Jose caf\u00e9s known for lattes also serve almond croissants",
        "Bijan Bakery \u00b7 Which San Jose caf\u00e9s known for lattes also serve pain au chocolat",
    ]


def test_key_figure_labels_name_the_item_never_a_snippet() -> None:
    plan = ReportComposition(question="q", session_id="s")
    # With no named item -- no subject, or one that starts with a pronoun -- the
    # label names the source that reported the row.
    assert _labels(plan, _fact("K001", subject=None, measure="aggregate rating")) == [
        "Example Org \u00b7 Aggregate rating",
    ]
    assert _labels(plan, _fact("K001", subject="its new location", measure="opening hours")) == [
        "Example Org \u00b7 Opening hours",
    ]
    assert _labels(plan, _fact("K001", subject=None, measure="stated figure")) == []
    assert _labels(plan, _fact("K001", subject="Example Cafe", measure="stated figure")) == [
        "Example Cafe \u00b7 stated figure",
    ]
    assert _labels(plan, _fact("K001", subject="iJava Cafe", measure="rating")) == ["iJava Cafe \u00b7 rating"]
    assert _labels(plan, _fact("K001", subject="bijan bakery", measure="rating")) == ["Bijan bakery \u00b7 rating"]
    assert _labels(plan, _fact("K001", measure="rating", period="2025")) == ["Example Cafe \u00b7 rating, 2025"]
    assert _labels(plan, _fact("K001", measure="rating in 2025", period="2025")) == ["Example Cafe \u00b7 rating in 2025"]


def test_key_figures_merge_one_passage_and_never_two() -> None:
    plan = ReportComposition(question="q", session_id="s")
    groups = merge_key_figures([
        _fact("K001", value="4.7 of 5 bubbles"),
        _fact("K002", value="20 reviews"),
        _fact("K003", value="4.6 of 5 bubbles"),
        _fact("K004", value="4.7 of 5 bubbles"),
        _fact("K005", finding_id="f2", value="30 reviews"),
        _fact("K006", value="4.7 of 5 bubbles", kind="forecast"),
    ], plan)
    assert [(g.values, [r.row_id for r in g.rows]) for g in groups] == [
        ("4.7 of 5 bubbles \u00b7 20 reviews", ["K001", "K002", "K004"]),
        ("4.6 of 5 bubbles", ["K003"]),
        ("30 reviews", ["K005"]),
        ("4.7 of 5 bubbles", ["K006"]),
    ]


def _eligible_composition(rows: list[FactRow], **fields) -> ReportComposition:
    findings, renamed = [], {}
    for row in rows:
        if row.finding_id in renamed:
            continue
        read = make_read(f"Page {row.finding_id}.", url=f"https://{row.finding_id}.example.test/p", title=row.finding_id)
        finding = make_finding(read, f"Page {row.finding_id}.", target_ids=["topic-01-target-01"])
        findings.append(finding.model_copy(update={"verification": FindingVerification(status="verified")}))
        renamed[row.finding_id] = finding_fingerprint(findings[-1])
    plan = [_topic("topic-01", _target("topic-01-target-01", "topic-01", "rating"))]
    return ReportComposition(question="q", session_id="s", sub_topics=plan, findings=findings,
                             fact_rows=[r.model_copy(update={"finding_id": renamed[r.finding_id],
                                                             "target_ids": ["topic-01-target-01"]}) for r in rows],
                             **fields)


def test_key_figures_print_one_row_per_label() -> None:
    """Two passages' rows under one label keep only the first; the rest stay in the evidence log."""
    composition = _eligible_composition([
        _fact("K001", finding_id="a", subject="Starbucks", value="3.5 of 5"),
        _fact("K002", finding_id="b", subject="Starbucks", value="4.4 of 5"),
        _fact("K003", finding_id="c", subject="Philz Coffee", value="4.5 of 5"),
    ])
    table = key_figures_table(composition)
    assert [(row[0].text, row[1].text, row[0].row_ids) for row in table.rows] == [
        ("Starbucks \u00b7 rating", "3.5 of 5", ["K001"]),
        ("Philz Coffee \u00b7 rating", "4.5 of 5", ["K003"]),
    ]
    assert table.caption == (
        "Showing 2 of 3 verified figures; all are in the evidence log. "
        "No figure in this table is a forecast."
    )


def test_a_row_with_no_named_item_is_labelled_by_its_source() -> None:
    """A row with no named item is labelled by the source that reported it, so
    figures from different findings keep separate rows. One passage's values about it
    still merge; under one label only the first row prints; a relayed figure is
    labelled by the organisation it is credited to."""
    def unnamed(row_id: str, finding_id: str, organisation: str, value: str, **fields) -> FactRow:
        return _fact(row_id, finding_id=finding_id, subject=None, value=value, period="2024").model_copy(
            update={"organisation": organisation, **fields})

    composition = _eligible_composition([
        unnamed("K001", "a", "Tripadvisor", "4.7 of 5"),
        unnamed("K002", "a", "Tripadvisor", "20 reviews"),
        unnamed("K003", "b", "Yelp", "4.4 of 5"),
        unnamed("K004", "c", "Tripadvisor", "4.1 of 5"),
        unnamed("K005", "d", "EIA", "38 GW", attribution="relayed", relay_host="energi.media"),
    ])
    table = key_figures_table(composition)
    assert [(row[0].text, row[1].text, row[2].text, row[0].row_ids) for row in table.rows] == [
        ("Tripadvisor \u00b7 Rating, 2024", "4.7 of 5 \u00b7 20 reviews", "Tripadvisor", ["K001", "K002"]),
        ("Yelp \u00b7 Rating, 2024", "4.4 of 5", "Yelp", ["K003"]),
        ("EIA \u00b7 Rating, 2024", "38 GW", "EIA, reported by energi.media", ["K005"]),
    ]
    assert table.caption == (
        "Showing 4 of 5 verified figures; all are in the evidence log. "
        "No figure in this table is a forecast."
    )


def test_key_figures_cap_at_ten_rows_and_count_fact_rows() -> None:
    rows = []
    for n in range(12):
        rows.append(_fact(f"K{2 * n + 1:03d}", finding_id=f"p{n}", subject=f"Cafe {n:02d}", value=f"4.{n} of 5"))
        rows.append(_fact(f"K{2 * n + 2:03d}", finding_id=f"p{n}", subject=f"Cafe {n:02d}", value=f"{n + 10} reviews"))
    table = key_figures_table(_eligible_composition(rows))
    assert len(table.rows) == MAX_KEY_FIGURE_ROWS == 10
    assert table.rows[0][1].text == "4.0 of 5 \u00b7 10 reviews"
    assert table.caption == (
        "Showing 20 of 24 verified figures; all are in the evidence log. "
        "No figure in this table is a forecast."
    )


def test_a_key_figures_source_prints_its_text_then_its_markers() -> None:
    composition = _eligible_composition([
        _fact("K001", finding_id="a", subject="Starbucks", value="3.5 of 5"),
        _fact("K002", finding_id="b", subject="Philz Coffee", value="4.5 of 5"),
    ])
    markdown = render_written_report(composition.model_copy(update={"table": key_figures_table(composition)}))
    assert "| Starbucks \u00b7 rating | 3.5 of 5 | Example Org [1] |" in markdown
    assert re.search(r"\| Philz Coffee \u00b7 rating \| 4\.5 of 5 \| Example Org \[2\] \|", markdown)


def test_the_replay_default_case_labels_its_figures_by_their_sources(tmp_path: Path) -> None:
    """The replay server's default case: its five figures name no item and come
    from five findings, so each keeps its own row, labelled by its source -- where a
    measure-only label printed two rows of five."""
    table = replay_outcome(EXTRA_PASS_CASE, tmp_path).composition.table
    assert table is not None and table.columns == ["What", "Figure", "Source"]
    assert [(row[0].text, row[1].text) for row in table.rows] == [
        ("Acme Institute 17 \u00b7 Rate, 2024", "40 percent"),
        ("Acme Institute 2 \u00b7 Value, 2024", "12 million dollars"),
        ("Independent Bureau 2 \u00b7 Value, 2024", "12 million dollars"),
        ("Acme Institute 3 \u00b7 Value, 2024", "3.4 million units"),
        ("Independent Bureau 3 \u00b7 Value, 2024", "3.4 million units"),
    ]
    assert table.caption == "No figure in this table is a forecast."
