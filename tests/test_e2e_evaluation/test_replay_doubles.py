"""Task 4.9: every replay double answers the request its real agent builds.

Each test feeds a double the request a *production* builder produced --
``context_check_messages``, ``statement_check_messages``, ``section_messages``,
``bottom_line_messages``, ``review_messages`` -- and asserts the reply. A
double that keys its answer to a global order, or that answers a packet the
agents do not build, fails here rather than in the matrix (Task 4.11).

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
from deep_research.agents.identity import finding_fingerprint
from deep_research.agents.report_writer import (
    PartJob,
    ReportWriterTask,
    bottom_line_messages,
    compose_written_report,
    section_messages,
)
from deep_research.agents.report import render_written_report
from deep_research.e2e_evaluation.replay import (
    CaseExpectation,
    ReplayCompleter,
    ReplayContractError,
    ReplayScenario,
    ReplaySource,
    ReplayTopic,
)
from deep_research.utils.types import (
    BottomLineDraft,
    FigureContext,
    FigureResult,
    Finding,
    FindingVerification,
    ItemMarkDraft,
    ReadRecord,
    ReportPoint,
    ReportSection,
    ReportStatement,
    ResearchState,
    SectionDraft,
    SubTopic,
    WriterPointDraft,
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


def _statement_item(source: ReplaySource, text: str) -> StatementCheckItem:
    """One labelled sentence citing one page's finding, as the writer's pass asks."""
    return StatementCheckItem(
        label="S001", text=text, findings=[verified(source)], labels=["F01"]
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


def _part_job(task: ReportWriterTask) -> PartJob:
    """The task's single part, matching what ``compose_written_report`` would
    build for its one sub-topic: every fixture here plans exactly one, so its
    own findings are the whole registry."""
    [topic] = task.sub_topics
    return PartJob(
        coverage_id=topic.coverage_id, sub_topic_title=topic.title, order=0,
        targets=task.targets, findings=task.findings, context_findings=[],
        previous=None, defects=[], redraft=True,
    )


def request_text(task: ReportWriterTask) -> str:
    return "\n".join(message.content for message in section_messages(task, _part_job(task)))


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


def test_the_context_double_reads_the_subject_a_figure_line_records() -> None:
    """D11: the request's own `` | recorded subject …`` part decides the reply.

    The real line ends ``… | recorded kind actual | recorded subject Kettle K1``
    only when the figure carries one (Task 5.7b), so a double that ignores the
    part would confirm a subject-less figure and the report would lose the one
    thing that keeps two equal values apart.
    """
    source = page("rated", value="4.5", unit="out of 5", period="2026")
    completer = ReplayCompleter(scenario(source))
    read, finding = read_and_finding(source)
    recorded = finding.model_copy(
        update={
            "figures": [
                finding.figures[0].model_copy(update={"subject": "Kettle K1"})
            ]
        }
    )
    request = context_request([(read, recorded)])
    assert "| recorded subject Kettle K1" in request

    [draft] = completer._reply_ContextCheckDraft(request).figures

    assert draft.subject == "Kettle K1"


def test_a_context_override_names_the_subject_and_the_period_it_resolves() -> None:
    """The two D11 overrides: a proposal code keeps or drops on the page's words."""
    source = page(
        "resolved",
        value="4",
        unit="GW",
        period="2024",
        context={"period": "2026", "subject": "Kettle K1"},
    )
    completer = ReplayCompleter(scenario(source))
    read, finding = read_and_finding(source)

    [draft] = completer._reply_ContextCheckDraft(
        context_request([(read, finding)])
    ).figures

    assert draft.period == "2026"
    assert draft.subject == "Kettle K1"


def test_the_writer_double_keeps_a_multi_word_unit_whole() -> None:
    source = page("units", value="3.4", unit="million units")
    completer = ReplayCompleter(scenario(source))
    task = writer_task([("F01", verified(source))])

    draft = completer._reply_SectionDraft(request_text(task))

    assert draft.points[0].text.startswith("Acme Institute reports 3.4 million units")


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

    draft = completer._reply_SectionDraft(request_text(task))

    assert [point.finding_labels for point in draft.points] == [["F01"]]
    assert source.excerpt.split()[-3:] == draft.points[0].text.split()[-3:]


def test_the_writer_double_names_a_registry_line_subject_first() -> None:
    """D11: the drafted sentence names its own row's subject, and marks it.

    Two products rated the same value are two rows, and the writer's own
    restatement guard counts a row only for the subject the sentence names
    (``report_writer.py``), so a draft that omitted the subject would be
    refused as a restatement of the other row. The subject is also marked as
    an option (spec §11.3), so a question-shaped table has a real cell to
    build from a replay run.
    """
    source = page("kettle", value="4.5", unit="out of 5", period="2026")
    completer = ReplayCompleter(scenario(source))
    finding = verified(source)
    [result] = finding.verification.figure_results
    finding = finding.model_copy(
        update={
            "verification": finding.verification.model_copy(
                update={
                    "figure_results": [
                        result.model_copy(
                            update={
                                "context": result.context.model_copy(
                                    update={"subject": "Kettle K1"}
                                )
                            }
                        )
                    ]
                }
            )
        }
    )
    request = request_text(writer_task([("F01", finding)]))
    assert "| subject Kettle K1 |" in request

    draft = completer._reply_SectionDraft(request)

    assert draft.points[0].text.startswith("Kettle K1")
    assert "4.5 out of 5" in draft.points[0].text
    [mark] = draft.points[0].items
    assert mark.name == "Kettle K1"
    assert mark.verdict == "4.5 out of 5"


def test_a_page_refuses_a_subject_list_that_does_not_match_its_figures() -> None:
    """A fixture typo must fail loudly, not silently drop a subject."""
    with pytest.raises(ValueError) as raised:
        page(
            "mismatch",
            value="4",
            figures=(("4", "GW", "2026", "actual"),),
            figure_subjects=("Kettle K1", "Kettle K2"),
        )

    assert "Kettle K2" in str(raised.value)


def test_a_page_refuses_a_publication_date_quote_its_text_does_not_carry() -> None:
    """The quote has to be the page's own words, like every other fixture claim."""
    with pytest.raises(ValueError) as raised:
        page(
            "undated",
            value="4",
            publication_date=("2026-02-20", "Published 2026-02-20"),
        )

    assert "2026-02-20" in str(raised.value)

    dated = page(
        "dated",
        value="4",
        excerpt="It added 4 GW in 2026.",
        text=(
            "Kettle note. Published 2026-02-20. It added 4 GW in 2026. "
            "The dated analysis covers the United States."
        ),
        publication_date=("2026-02-20", "Published 2026-02-20"),
    )

    assert dated.publication_date == ("2026-02-20", "Published 2026-02-20")


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
        task, provider=completer, fingerprint=None,
    )

    # The checker really was asked, and with the writer's own labels: a reader
    # label is not a registry label, which is the distinction this pins. The
    # section's own batch is the *first* Statement Check packet recorded: the
    # bottom line's later batch re-checks the same kept sentences and would
    # overwrite ``completer.packets`` with an equally-labelled packet, but the
    # first is what this test means to pin.
    request = next(
        text for key, text in completer.packet_sequence
        if key == "evidence_verifier:StatementCheckDraft"
    )
    assert "cited findings:" in request
    assert "Acme Institute's own figure" in request
    assert not re.search(r"(?m)^  F\d+: ", request)

    # The sections print every kept sentence; the bottom line answers with the
    # first only (notes-progress-report spec §7.7).
    kept_texts = [point.text for section in composition.sections for point in section.points]
    assert "Acme Institute reports 10.4 GW for 2024." in kept_texts
    assert "Corrected reports 9.8 GW for 2024." in kept_texts
    assert not any("18.9" in text for text in kept_texts)
    refused_points = [
        point for point in composition.rejected_points if "18.9" in point.text
    ]
    assert len(refused_points) == 1
    assert "all segments" in refused_points[0].reason


def test_the_statement_double_refuses_a_cited_line_with_no_body() -> None:
    """R2: a finding with no kept figure is shown with its ``snippet:`` and then
    the body it is ``attributed to:``, or -- when the extraction admitted no body
    -- the site it was ``read at:`` (the re-review's C1; a host is where a
    statement was read).

    The double judges a sentence against the findings the packet shows for it,
    so a packet that reads ``(no kept figures)`` with neither sub-line is a
    shape no production builder emits: refusing it keeps the double from
    answering a request the agents cannot build.
    """
    source = page("plain", value="40", figures=())
    completer = ReplayCompleter(scenario(source))
    request = statement_request([_statement_item(source, "Plain states a figure.")])
    assert "(no kept figures)" in request
    assert "    attributed to: " in request or "    read at: " in request

    stripped = re.sub(r"(?m)^    (?:snippet|attributed to|read at): .*\n", "", request)

    with pytest.raises(ReplayContractError):
        completer._reply_StatementCheckDraft(stripped)


def test_the_statement_double_answers_a_cited_line_that_states_its_body() -> None:
    """The shipped format itself passes the check: the same request, unedited."""
    source = page("plain", value="40", figures=())
    completer = ReplayCompleter(scenario(source))

    [verdict] = completer._reply_StatementCheckDraft(
        statement_request([_statement_item(source, "Plain states a figure.")])
    ).statements

    assert verdict.verdict == "consistent"


def test_the_statement_double_refuses_an_attribution_line_with_a_figure() -> None:
    """R2: the body line belongs to the figureless case alone (Task 5.7a).

    A finding whose figure the Context Check kept states its attribution on the
    figure's own line, so the shipped builder prints no second, extraction-time
    body line for it: that line could credit a different body than the figure's
    verdict did. A packet that printed one anyway is refused.
    """
    source = page("kept", value="10.4")
    completer = ReplayCompleter(scenario(source))
    request = statement_request([_statement_item(source, "Kept reports 10.4 GW.")])
    assert "    attributed to: " not in request

    wrong = re.sub(
        r"(?m)^(    snippet: .*)$",
        r"\1\n    attributed to: Acme Institute",
        request,
        count=1,
    )

    with pytest.raises(ReplayContractError):
        completer._reply_StatementCheckDraft(wrong)


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

    draft = completer._reply_SectionDraft(request_text(task))

    assert [point.finding_labels for point in draft.points] == [
        ["F01"], ["F02"],
    ]
    assert "reports 10.4 GW for 2024" in draft.points[0].text
    assert "projects 14 GW for 2025" in draft.points[1].text
    composition = await compose_written_report(
        task, provider=completer, fingerprint=None,
    )
    assert composition.rejected_points == []
    assert draft.short_title == "Battery storage"  # the title's first two words (§7.7)
    # The bottom line answers with the first checked section statement
    # (notes-progress-report spec §7.7), in the section's own words.
    assert composition.summary[0].text == draft.points[0].text


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

    draft = completer._reply_SectionDraft(request_text(task))

    assert prose in draft.points[0].text
    [item] = [
        StatementCheckItem(
            label="S001", text=draft.points[0].text,
            findings=[verified(source)], labels=["F01"],
        )
    ]
    [verdict] = completer._reply_StatementCheckDraft(statement_request([item])).statements
    assert verdict.verdict == "inconsistent"
    assert verdict.reason


def test_the_writer_double_answers_once_and_drafts_one_line_per_topic() -> None:
    """notes-progress-report spec §7.7: the first statement of the first
    ``## {coverage_id} · {title}`` block is the one answer sentence, and the
    first statement of each block is that topic's line, with its labels."""
    sources = tuple(
        page(f"finding{n}", value=str(n), unit="GW", period="2024") for n in range(1, 5)
    )
    completer = ReplayCompleter(scenario(*sources))
    task = writer_task(
        [(f"F{n:02d}", verified(source)) for n, source in enumerate(sources, start=1)]
    )
    registry = dict(task.registry)

    def section(coverage_id: str, title: str, labels: list[str]) -> ReportSection:
        points = [
            ReportPoint(
                text=f"{label} statement.",
                statement=ReportStatement(
                    statement_id=f"S-{label}", text=f"{label} statement.",
                    finding_ids=[finding_fingerprint(registry[label])],
                ),
            )
            for label in labels
        ]
        return ReportSection(title=title, coverage_id=coverage_id, points=points)

    request = "\n".join(
        message.content
        for message in bottom_line_messages(
            task,
            [section("topic-01", "First part", ["F01", "F02"]),
             section("topic-02", "Second part", ["F03", "F04"])],
        )
    )

    draft = completer._reply_BottomLineDraft(request)

    assert [(point.text, point.finding_labels) for point in draft.sentences] == [
        ("F01 statement.", ["F01"]),
    ]
    assert [(line.topic, line.text, line.finding_labels) for line in draft.topics] == [
        ("topic-01", "F01 statement.", ["F01"]),
        ("topic-02", "F03 statement.", ["F03"]),
    ]


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
        task, provider=completer, fingerprint=None,
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

    # One finding, checked three times -- its own section point, the bottom
    # line's answer that restates it, and its topic's line (notes-progress-report
    # spec §7.7): the parallel writer statement-checks all three, so the packet
    # manifests all three ids rather than the single-call writer's one.
    assert len(packet.expected_statement_ids) == 3
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
    assert {
        (draft.statement_id, draft.disposition)
        for draft in reply.statement_dispositions
    } == {(sid, "supported") for sid in packet.expected_statement_ids}
    assert reply.rationale


@pytest.mark.asyncio
async def test_a_scenario_can_script_a_statement_unsupported_and_a_score() -> None:
    source = page("rejected", value="10.4")
    completer = ReplayCompleter(
        scenario(source, rejected_statement_ids=("S001",), review_score=0.4)
    )
    task = writer_task([("F01", verified(source))])
    composition = await compose_written_report(
        task, provider=completer, fingerprint=None,
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
    dispositions = {
        draft.statement_id: draft.disposition for draft in reply.statement_dispositions
    }
    assert dispositions["S001"] == "unsupported"
    assert set(dispositions.values()) == {"unsupported", "supported"}


@pytest.mark.asyncio
async def test_a_replayed_bottom_line_keeps_its_answer_and_its_topic_line() -> None:
    """notes-progress-report spec §7.7, end to end through the real writer: the
    double's answer and its topic line are both checked and kept, the topic line
    labelled with the section's short title."""
    source = page("answered", value="10.4")
    completer = ReplayCompleter(scenario(source))
    task = writer_task([("F01", verified(source))])

    composition = await compose_written_report(task, provider=completer, fingerprint=None)

    assert composition.rejected_points == []
    layout = composition.bottom_line
    assert layout is not None and not layout.assembled
    assert layout.answer_ids == ["S001"]
    assert [(line.coverage_id, line.label, line.statement_id) for line in layout.topic_lines] == [
        ("topic-01", "Battery storage", "S002"),
    ]
    assert [point.statement_id for point in composition.summary] == ["S001", "S002"]


def test_the_statement_double_answers_the_topic_line_keys() -> None:
    """``BT``/``RT`` are the bottom line's topic-line flight keys (spec §7.1)."""
    source = page("topic", value="10.4")
    completer = ReplayCompleter(scenario(source))
    items = [
        StatementCheckItem(label=label, text="Acme Institute reports 10.4 GW for 2024.",
                           findings=[verified(source)], labels=["Acme Institute's own figure"])
        for label in ("B01", "BT01", "R01", "RT01")
    ]

    reply = completer._reply_StatementCheckDraft(statement_request(items))

    assert [draft.label for draft in reply.statements] == ["B01", "BT01", "R01", "RT01"]
