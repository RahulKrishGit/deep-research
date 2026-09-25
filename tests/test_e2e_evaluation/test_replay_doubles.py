"""Task 4.9: every replay double answers the request its real agent builds.

Each test feeds a double the request a *production* builder produced --
``context_check_messages``, ``statement_check_messages``, ``writer_messages``,
``review_messages`` -- and asserts the reply. A double that keys its answer to
a global order, or that answers a packet the agents do not build, fails here
rather than in the matrix (Task 4.11).

A label is a batch's own: the Evidence Verifier numbers each Context Check
batch ``F01…`` from one (``agents/evidence_verifier.py``), so two batches both
carry an ``F01``. That is why the double reads the labels a request carries
instead of counting requests.
"""

from __future__ import annotations

from collections.abc import Sequence
import re

import pytest
from deep_research.agents.evidence_verifier import (
    ContextItem,
    FigureMatch,
    StatementCheckItem,
    check_statements,
    context_check_messages,
    context_passage,
    statement_check_messages,
)
from deep_research.agents.report_reviewer import (
    build_report_review_input,
    review_messages,
)
from deep_research.agents.report_writer import (
    ReportWriterDraft,
    ReportWriterTask,
    WriterPointDraft,
    compose_written_report,
    writer_messages,
)
from deep_research.agents.report import render_written_report
from deep_research.e2e_evaluation.replay import (
    CaseExpectation,
    ReplayCompleter,
    ReplayScenario,
    ReplaySource,
    ReplayTopic,
)
from deep_research.utils.types import (
    FigureContext,
    FigureResult,
    Finding,
    FindingVerification,
    ReadRecord,
    ResearchState,
    SubTopic,
)
from tests.evidence_fakes import figure, make_finding, make_read, make_target

QUESTION = "How much battery storage capacity was added in the United States in 2024?"
TARGET_ID = "topic-01-target-01"


# --- fixtures ---------------------------------------------------------------


def page(
    slug: str,
    *,
    value: str,
    unit: str = "GW",
    period: str | None = "2024",
    kind: str | None = "actual",
    text: str | None = None,
    excerpt: str | None = None,
    figures: tuple[tuple[str, str, str | None, str | None], ...] | None = None,
    **fields: object,
) -> ReplaySource:
    """One declared page whose excerpt states one figure."""
    title = f"{slug.title()} storage report"
    excerpt = excerpt or (
        f"{slug} added {value} {unit} of battery storage in {period or 'its report'}."
    )
    return ReplaySource(
        url=f"https://{slug}.example/report",
        title=title,
        text=text or f"{excerpt} The {slug} analysis covers the United States.",
        excerpt=excerpt,
        claim=excerpt,
        figures=(
            ((value, unit, period, kind),) if figures is None else figures
        ),
        **fields,  # type: ignore[arg-type]
    )


def scenario(*sources: ReplaySource, **fields: object) -> ReplayScenario:
    topic = ReplayTopic(
        title="Battery storage additions",
        question=QUESTION,
        measure="battery storage power capacity added",
        unit_dimension="power",
        query="battery storage capacity additions 2024",
        sources=tuple(sources),
    )
    return ReplayScenario(
        case_id="replay-doubles",
        question=QUESTION,
        topics=(topic,),
        expectation=CaseExpectation(terminal_quality="accepted", exit_code=0),
        **fields,  # type: ignore[arg-type]
    )


def read_and_finding(source: ReplaySource) -> tuple[ReadRecord, Finding]:
    read = make_read(source.text, url=source.url, title=source.title)
    finding = make_finding(
        read,
        source.excerpt,
        figures=[figure(*spec) for spec in source.figures],
        target_ids=[TARGET_ID],
    )
    return read, finding


def context_request(pairs: Sequence[tuple[ReadRecord, Finding]]) -> str:
    """One batch, labelled the way ``EvidenceVerifierAgent.verify`` labels it."""
    items = [
        ContextItem(
            label=f"F{number:02d}",
            finding=finding,
            read=read,
            passage=context_passage(read, finding.locator, finding.snippet),
            match=FigureMatch(read_found=True, snippet_on_page=True),
        )
        for number, (read, finding) in enumerate(pairs, start=1)
    ]
    return "\n".join(message.content for message in context_check_messages(items))


def statement_request(items: Sequence[StatementCheckItem]) -> str:
    return "\n".join(
        message.content
        for message in statement_check_messages(items, question=QUESTION)
    )


def verified(source: ReplaySource, *, organisation: str | None = None) -> Finding:
    """The finding one page's figures become once the verifier has kept them."""
    _, finding = read_and_finding(source)
    if not finding.figures:
        # A finding whose page stated no figure: Figure Match keeps it and no
        # Context Check runs for it (evidence_verifier's own branch).
        return finding.model_copy(
            update={
                "verification": FindingVerification(status="verified")
            }
        )
    spec = source.figures[0]
    result = FigureResult(
        figure=finding.figures[0],
        matched=True,
        evidence_words=source.excerpt,
        context=FigureContext(
            period=spec[2],
            scope=None,
            attribution="own",
            organisation=organisation or source.issuer,
            kind=spec[3] or "actual",  # type: ignore[arg-type]
        ),
    )
    return finding.model_copy(
        update={
            "verification": FindingVerification(
                status="verified", figure_results=[result]
            )
        }
    )


def writer_task(registry: Sequence[tuple[str, Finding]]) -> ReportWriterTask:
    findings = [finding for _, finding in registry]
    target = make_target(TARGET_ID)
    return ReportWriterTask(
        session_id="replay-doubles",
        instruction=QUESTION,
        question=QUESTION,
        iteration=0,
        max_extra_passes=1,
        as_of="2026-09-16",
        scope="United States",
        generated_on="2026-09-16",
        sub_topics=[
            SubTopic(
                coverage_id=target.coverage_id,
                title="Battery storage additions",
                rationale="It is the question asked.",
                search_queries=["battery storage capacity additions 2024"],
                success_criteria=["a capacity addition is quoted"],
                priority=1,
                evidence_targets=[target],
            )
        ],
        targets=[target],
        findings=findings,
        sources=[],
        registry=list(registry),
        facts=[],
        not_found=[],
        answered={},
    )


def request_text(task: ReportWriterTask) -> str:
    return "\n".join(message.content for message in writer_messages(task))


# --- the Context Check double -----------------------------------------------


def test_the_context_double_confirms_one_figure_per_figure_the_request_lists() -> None:
    beyond = page("beyond", value="12.5", unit="GW")
    completer = ReplayCompleter(scenario(beyond))
    read, finding = read_and_finding(beyond)
    two = finding.model_copy(
        update={"figures": [*finding.figures, figure("4.1", "GW", "2024", "actual")]}
    )

    reply = completer._reply_ContextCheckDraft(context_request([(read, two)]))

    assert [(draft.finding, draft.figure) for draft in reply.figures] == [
        ("F01", 1),
        ("F01", 2),
    ]
    for draft in reply.figures:
        assert draft.verdict == "confirm"
        assert draft.period == "2024"
        assert draft.kind == "actual"
        assert draft.attribution == "own"
        assert draft.reason
        # The words it quotes are the page's own, so code keeps the figure.
        assert draft.evidence_words.strip() == beyond.excerpt


def test_the_context_double_keys_its_reply_to_the_labels_one_batch_carries() -> None:
    """Two batches both label their first finding ``F01``.

    The double must answer the batch it was handed: a reply built from the
    scenario's page order would answer batch B's ``F01`` with batch A's page.
    """
    first = [page(f"alpha{n}", value=f"{n}.0") for n in range(1, 6)]
    second = [page(f"beta{n}", value=f"{n}.5") for n in range(1, 6)]
    completer = ReplayCompleter(scenario(*first, *second))
    batch_a = [read_and_finding(source) for source in first]
    batch_b = [read_and_finding(source) for source in second]

    reply_b = completer._reply_ContextCheckDraft(context_request(batch_b))
    reply_a = completer._reply_ContextCheckDraft(context_request(batch_a))

    for reply, batch in ((reply_b, second), (reply_a, first)):
        assert [draft.finding for draft in reply.figures] == [
            f"F{number:02d}" for number in range(1, 6)
        ]
        assert [draft.evidence_words for draft in reply.figures] == [
            source.excerpt for source in batch
        ]


def test_a_context_override_states_the_scope_attribution_kind_and_organisation() -> None:
    """PD-25/PD-18's shape: code keeps the correction only because the words are the page's."""
    words = "The 18.9 gigawatts figure covers all segments of the market."
    harness = page(
        "woodmac",
        value="18.9",
        unit="gigawatts",
        period="2025",
        text=(
            "The woodmac study states 18.9 gigawatts; the woodmac survey, a "
            "relay of Wood Mackenzie, adds that the projection covers all "
            "segments of the market. " + words
        ),
        excerpt="The woodmac study states 18.9 gigawatts",
        context={
            "scope": "all segments",
            "attribution": "relayed",
            "organisation": "Wood Mackenzie",
            "kind": "forecast",
            "evidence_words": words,
            "verdict": "confirm",
        },
    )
    completer = ReplayCompleter(scenario(harness))
    read, finding = read_and_finding(harness)

    [draft] = completer._reply_ContextCheckDraft(
        context_request([(read, finding)])
    ).figures

    assert draft.scope == "all segments"
    assert draft.attribution == "relayed"
    assert draft.organisation == "Wood Mackenzie"
    assert draft.kind == "forecast"
    assert draft.evidence_words == words


def test_a_context_override_can_reject_a_figure() -> None:
    page_one = page("rejectme", value="9.9", context={"verdict": "reject"})
    completer = ReplayCompleter(scenario(page_one))
    read, finding = read_and_finding(page_one)

    [draft] = completer._reply_ContextCheckDraft(
        context_request([(read, finding)])
    ).figures

    assert draft.verdict == "reject"
    assert draft.reason


def test_a_figure_whose_unit_is_more_than_one_word_is_read_by_the_context_double() -> None:
    """The real line is ``figure 1: 12 million dollars | recorded period …``."""
    source = page("dollars", value="12", unit="million dollars")
    completer = ReplayCompleter(scenario(source))
    read, finding = read_and_finding(source)

    [draft] = completer._reply_ContextCheckDraft(
        context_request([(read, finding)])
    ).figures

    assert (draft.finding, draft.figure) == ("F01", 1)
    assert draft.kind == "actual"
    assert draft.period == "2024"


def test_the_writer_double_keeps_a_multi_word_unit_whole() -> None:
    source = page("units", value="3.4", unit="million units")
    completer = ReplayCompleter(scenario(source))
    task = writer_task([("F01", verified(source))])

    draft = completer._reply_ReportWriterDraft(request_text(task))

    assert draft.executive_summary[0].text.startswith("Acme Institute reports 3.4 million units")


def test_the_writer_double_drafts_a_point_for_a_finding_with_no_figure() -> None:
    """A figureless finding is a registry row production really prints.

    ``EvidenceVerifierAgent`` marks a finding whose page stated no figure
    ``verified`` without a Context Check, and ``finding_registry`` still lists
    it: the writer is asked to write about it. A packet that lists findings and
    no figure line is therefore a valid request, and the double drafts the
    finding's own words rather than refusing the whole pass.
    """
    source = page("plain", value="40", figures=())
    completer = ReplayCompleter(scenario(source))
    task = writer_task([("F01", verified(source))])

    draft = completer._reply_ReportWriterDraft(request_text(task))

    assert [point.finding_labels for point in draft.executive_summary] == [["F01"]]
    assert source.excerpt.split()[-3:] == draft.executive_summary[0].text.split()[-3:]


def test_a_context_override_the_verifier_does_not_read_is_refused() -> None:
    """A fixture typo must fail loudly, not silently script nothing."""
    with pytest.raises(ValueError) as raised:
        page("typo", value="1.0", context={"scopes": "all segments"})

    assert "scopes" in str(raised.value)


# --- the Statement Check double ---------------------------------------------


def test_the_statement_double_answers_one_verdict_per_label_the_request_lists() -> None:
    first, second = page("one", value="1.0"), page("two", value="2.0")
    completer = ReplayCompleter(scenario(first, second))
    items = [
        StatementCheckItem(
            label="S001", text="One reports 1.0 GW for 2024.",
            findings=[verified(first)], labels=["F01"],
        ),
        StatementCheckItem(
            label="S002", text="Two reports 2.0 GW for 2024.",
            findings=[verified(second)], labels=["F01"],
        ),
    ]

    reply = completer._reply_StatementCheckDraft(statement_request(items))

    assert [draft.label for draft in reply.statements] == ["S001", "S002"]
    for draft in reply.statements:
        assert draft.verdict == "consistent"
        assert draft.corrected_text == ""
        assert draft.reason


@pytest.mark.asyncio
async def test_a_statement_override_corrects_or_refuses_through_the_real_writer() -> None:
    """The override must reach the verdict the real composer applies.

    R1: the items are built by ``compose_written_report`` itself, so the labels
    each item carries are the writer's own reader labels -- never the ``F01``
    registry labels a hand-built item would invent, which the real request does
    not contain. A page scripted ``inconsistent`` has to leave a refused point
    behind, and a page scripted ``corrected`` has to replace the sentence.
    """
    kept = page("kept", value="10.4")
    corrected = page(
        "corrected", value="9.8",
        statement={"verdict": "corrected", "text": "Corrected reports 9.8 GW for 2024."},
    )
    refused = page(
        "refused", value="18.9",
        statement={
            "verdict": "inconsistent",
            "reason": "the page states the figure across all segments, not grid-scale",
        },
    )
    completer = ReplayCompleter(scenario(kept, corrected, refused))
    task = writer_task(
        [
            ("F01", verified(kept)),
            ("F02", verified(corrected)),
            ("F03", verified(refused)),
        ]
    )

    composition = await compose_written_report(
        task,
        completer._reply_ReportWriterDraft(request_text(task)),
        provider=completer,
        fingerprint=None,
    )

    # The checker really was asked, and with the writer's own labels: a reader
    # label is not a registry label, which is the distinction this pins.
    request = completer.packets["evidence_verifier:StatementCheckDraft"]
    assert "cited findings:" in request
    assert "Acme Institute's own figure" in request
    assert not re.search(r"(?m)^  F\d+: ", request)

    kept_texts = [point.text for point in composition.summary]
    assert "Acme Institute reports 10.4 GW for 2024." in kept_texts
    assert "Corrected reports 9.8 GW for 2024." in kept_texts
    assert not any("18.9" in text for text in kept_texts)
    refused_points = [
        point for point in composition.rejected_points if "18.9" in point.text
    ]
    assert len(refused_points) == 1
    assert "all segments" in refused_points[0].reason


def test_a_scenario_can_script_the_statement_check_failing() -> None:
    """The keep-on-batch-failure path: every sentence keeps its drafted text."""
    source = page("checked", value="10.4")
    completer = ReplayCompleter(scenario(source, statement_failure=True))
    item = StatementCheckItem(
        label="S001", text="Checked reports 10.4 GW for 2024.",
        findings=[verified(source)], labels=["F01"],
    )

    import asyncio

    verdicts, errors = asyncio.run(
        check_statements(completer, [item], question=QUESTION)
    )

    assert verdicts == {"S001": None}
    assert [error.error_type for error in errors] == [
        "evidence_verifier_statement_check_failed"
    ]


# --- the Report Writer double -----------------------------------------------


@pytest.mark.asyncio
async def test_the_writer_double_drafts_one_kept_point_per_registry_line() -> None:
    actual, forecast = (
        page("actual", value="10.4", period="2024", kind="actual"),
        page("forecast", value="14", period="2025", kind="forecast"),
    )
    completer = ReplayCompleter(scenario(actual, forecast))
    task = writer_task([("F01", verified(actual)), ("F02", verified(forecast))])

    draft = completer._reply_ReportWriterDraft(request_text(task))

    assert [point.finding_labels for point in draft.executive_summary] == [
        ["F01"], ["F02"],
    ]
    assert "reports 10.4 GW for 2024" in draft.executive_summary[0].text
    assert "projects 14 GW for 2025" in draft.executive_summary[1].text
    composition = await compose_written_report(
        task, draft, provider=completer, fingerprint=None
    )
    assert composition.rejected_points == []
    assert [point.text for point in composition.summary] == [
        point.text for point in draft.executive_summary
    ]


def test_the_writer_double_drafts_prose_no_page_states_and_the_checker_refuses_it() -> None:
    """The case's fault, end to end: the drafted words, and the refusal.

    ``unsupported-mechanism``'s premise: the writer dresses a verified figure in
    a recommendation no page makes. Under D8 nothing in code reads the drafted
    prose -- the Statement Check refuses it -- so the double has to carry the
    scripted words into the draft and answer the sentence that quotes them.
    """
    source = page("invented", value="10.4")
    prose = "the agency should subsidise Acme widget deployment"
    completer = ReplayCompleter(scenario(source, invented_prose=prose))
    task = writer_task([("F01", verified(source))])

    draft = completer._reply_ReportWriterDraft(request_text(task))

    assert prose in draft.executive_summary[0].text
    [item] = [
        StatementCheckItem(
            label="S001", text=draft.executive_summary[0].text,
            findings=[verified(source)], labels=["F01"],
        )
    ]
    [verdict] = completer._reply_StatementCheckDraft(statement_request([item])).statements
    assert verdict.verdict == "inconsistent"
    assert verdict.reason


def test_every_manifest_entry_declares_the_result_its_builder_expects() -> None:
    """R4 (review-4.9): the entry's published result and the scenario's own
    expectation are one fact, and the runner reprints the entry's.

    ``runner.py`` copies ``expected_product_result`` into the recorded result,
    so an entry whose string disagrees with its builder's ``CaseExpectation``
    publishes a result the row was never measured against.
    """
    from deep_research.e2e_evaluation.replay_matrix import REPLAY_CASE_MANIFEST

    drifted: dict[str, tuple[str, str]] = {}
    for entry in REPLAY_CASE_MANIFEST:
        if entry.build is None:
            continue
        expectation = entry.build().expectation
        declared = f"{expectation.terminal_quality} / {expectation.exit_code}"
        if entry.expected_product_result != declared:
            drifted[entry.case_id] = (entry.expected_product_result, declared)

    assert drifted == {}


# --- the Report Reviewer double ---------------------------------------------


@pytest.mark.asyncio
async def test_the_reviewer_double_scores_every_dimension_and_disposes_every_statement() -> None:
    source = page("reviewed", value="10.4")
    completer = ReplayCompleter(scenario(source))
    task = writer_task([("F01", verified(source))])
    composition = await compose_written_report(
        task,
        completer._reply_ReportWriterDraft(request_text(task)),
        provider=completer,
        fingerprint=None,
    )
    state = ResearchState(
        session_id="replay-doubles",
        original_question=QUESTION,
        sub_topics=list(task.sub_topics),
        verified_findings=list(task.findings),
        composition=composition,
        report=render_written_report(composition),
    )
    packet = build_report_review_input(state)
    request = "\n".join(message.content for message in review_messages(packet))

    reply = completer._reply_ReportReviewDraft(request)

    assert packet.expected_statement_ids == ["S001"]
    assert set(reply.dimensions.as_dimensions()) == {
        "completeness",
        "prioritization",
        "evidence_quality",
        "attribution",
        "uncertainty",
        "readability",
        "actionability",
    }
    assert set(reply.dimensions.as_dimensions().values()) == {0.9}
    assert [
        (draft.statement_id, draft.disposition)
        for draft in reply.statement_dispositions
    ] == [("S001", "supported")]
    assert reply.rationale


@pytest.mark.asyncio
async def test_a_scenario_can_script_a_statement_unsupported_and_a_score() -> None:
    source = page("rejected", value="10.4")
    completer = ReplayCompleter(
        scenario(source, rejected_statement_ids=("S001",), review_score=0.4)
    )
    task = writer_task([("F01", verified(source))])
    composition = await compose_written_report(
        task,
        completer._reply_ReportWriterDraft(request_text(task)),
        provider=completer,
        fingerprint=None,
    )
    state = ResearchState(
        session_id="replay-doubles",
        original_question=QUESTION,
        sub_topics=list(task.sub_topics),
        verified_findings=list(task.findings),
        composition=composition,
        report=render_written_report(composition),
    )
    packet = build_report_review_input(state)
    request = "\n".join(message.content for message in review_messages(packet))

    reply = completer._reply_ReportReviewDraft(request)

    assert set(reply.dimensions.as_dimensions().values()) == {0.4}
    assert [
        draft.disposition for draft in reply.statement_dispositions
    ] == ["unsupported"]
