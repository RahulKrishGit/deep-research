"""The tool-free half of the prompt/transport congruence invariant.

Every separate structured call — plan, extraction, scoring, judge, the
Context Check, the Statement Check, the Writer's draft request and the
Reviewer's review — sends no tools. Its developer message must therefore name
none of the registered tools, and it must carry exactly one output-shape
sentence plus at most one or two bounded examples. These tests build the real
messages through the real builders; none of them copies a prompt string.

The second half of the file is the cross-operation conformance matrix: one row
per tool-free structured operation, asserted against the request that operation
actually renders, and one row per provider transport a structured request can
travel on. Both halves read their subject out of the real builder's output, so
the matrix cannot drift away from the prompt it pins. The table is closed and
hand-maintained: a new tool-free request has to be added to it by hand.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict, Field

from deep_research.agents.evidence import build_read_dossiers, build_read_record
from deep_research.agents.evidence_verifier import (
    ContextCheckDraft,
    ContextItem,
    FigureMatch,
    StatementCheckDraft,
    StatementCheckItem,
    context_check_messages,
    context_passage,
    statement_check_messages,
)
from deep_research.agents.identity import finding_fingerprint
from deep_research.agents.planner import (
    _PLAN_REPLY_EXAMPLES,
    ResearchPlanDraft,
    plan_messages,
)
from deep_research.agents.prompts import (
    STRUCTURED_EXAMPLE_NOTICE,
    STRUCTURED_REPLY_FORMAT,
    STRUCTURED_REQUEST_END,
    AgentTask,
)
from deep_research.agents.report_reviewer import (
    ReportReviewDraft,
    ReportReviewInput,
    ReviewDeterministic,
    ReviewFindingView,
    ReviewStatementView,
    review_messages,
)
from deep_research.agents.report_writer import (
    PartJob,
    ReportWriterTask,
    bottom_line_messages,
    section_messages,
)
from deep_research.agents.researcher import (
    _FINDING_REPLY_EXAMPLES,
    SubTopicFindingsDraft,
    SubTopicTask,
    extraction_messages,
)
from deep_research.agents.source_evaluator import (
    _SOURCE_SCORE_REPLY_EXAMPLES,
    SourceEvaluationTask,
    SourceScoresDraft,
    scoring_messages,
)
from deep_research.agents.sources import SourceGroup
from deep_research.agents.steps import ReActRun
from deep_research.evaluation.judging import (
    COMMON_DIMENSION_WEIGHTS,
    JudgeInput,
    render_judge_messages,
)
from deep_research.evaluation.models import JudgeVerdict
from deep_research.observability import LangSmithRuntimeConfig, Tracker
from deep_research.providers import (
    ChatMessage,
    DeepSeekChatProvider,
    DeepSeekJudgeProvider,
    OpenAIChatProvider,
    StructuredOutputError,
)
from deep_research.tools import (
    DocumentReaderTool,
    QueryMemoryTool,
    SaveToMemoryTool,
    WebScraperTool,
    WebSearchTool,
    WriteDocumentTool,
)
from deep_research.utils.config import LLMConfig
from deep_research.utils.types import (
    BottomLineDraft,
    FigureContext,
    FigureResult,
    Finding,
    FindingFigure,
    FindingVerification,
    ReadRecord,
    ReportPoint,
    ReportSection,
    ReportStatement,
    SectionDraft,
    SubTopic,
)
from tests.evidence_fakes import make_target

# Read from the real tool classes' own ``name`` attributes, so a renamed
# tool is caught. This is still an explicit list of the six production tools:
# a newly registered seventh tool would have to be added here too.
REGISTERED_TOOL_NAMES = frozenset(
    tool_class.name
    for tool_class in (
        WebSearchTool,
        WebScraperTool,
        DocumentReaderTool,
        QueryMemoryTool,
        SaveToMemoryTool,
        WriteDocumentTool,
    )
)

EXTRACTED_AT = "2026-08-01T12:00:00+00:00"


def _sub_topic(title: str = "Angle") -> SubTopic:
    return SubTopic(
        coverage_id="topic-01",
        title=title,
        rationale=f"Rationale for {title}.",
        search_queries=[f"query for {title}"],
        success_criteria=[f"criterion for {title}"],
        priority=1,
    )


def _finding(url: str = "https://real.test/one") -> Finding:
    return Finding(
        content="A measured result.",
        source_url=url,
        source_title="Measured result",
        extracted_at=EXTRACTED_AT,
        confidence=0.8,
        related_sub_topic="Angle",
    )


def _finished_run(agent_name: str = "planner") -> ReActRun:
    return ReActRun(agent_name=agent_name, stop_reason="finished")


def _planner_messages() -> list:
    return plan_messages(
        AgentTask(instruction="How much capacity can quantum computing reach?"),
        _finished_run("planner"),
    )


def _extraction_messages() -> list:
    return extraction_messages(
        SubTopicTask(instruction="Gather evidence.", sub_topic=_sub_topic()),
        _finished_run("researcher"),
        evidence_chars=200,
    )


def _scoring_messages() -> list:
    return scoring_messages(
        SourceEvaluationTask(
            instruction="Score every source.",
            groups=[
                SourceGroup(
                    url="https://real.test/one",
                    domain="real.test",
                    title="Measured result",
                    findings=[_finding()],
                    sub_topics=["Angle"],
                )
            ],
        ),
        excerpt_chars=200,
    )


_JUDGE_RUBRIC_DIMENSION = "decomposition_quality"


def _judge_input() -> JudgeInput:
    """The smallest judge view that still renders the real judge prompt.

    The evaluation fixtures live one directory over, in
    ``tests/test_evaluation/conftest.py``; the matrix is self-contained, so the
    input is built here from the frozen weight table and one real dimension.
    """
    return JudgeInput(
        prompt_id="individual-agent-judge",
        rubric_version=1,
        agent="planner",
        tier="controlled",
        case_id="matrix-case",
        case_version=1,
        purpose="Plan one research question.",
        rubric={
            "common_dimensions": dict(COMMON_DIMENSION_WEIGHTS),
            "agent_dimensions": [
                {
                    "dimension_id": _JUDGE_RUBRIC_DIMENSION,
                    "description": "The plan decomposes the question.",
                    "anchors": {"1.0": "decomposed", "0.0": "not decomposed"},
                }
            ],
        },
        inputs={"question": "How much capacity can quantum computing reach?"},
        reference_expectations={"minimum_sub_topics": 3},
        agent_output={"sub_topics": []},
        state_update={},
        evidence={},
        trajectory=[],
        gate_results=[],
    )


def _judge_messages() -> list:
    return render_judge_messages(_judge_input())


# --- the pipeline's four tool-free requests -----------------------------------
#
# The Context Check, the Statement Check, the Report Writer's draft request and
# the Report Reviewer's review: the four structured calls the evidence-verifier
# pipeline sends with no tools. Every one is built by its real builder from the
# smallest input that renders it.

EVIDENCE_QUESTION = "How much capacity was added in the United States in 2024?"
ORGANISATION = "Example Statistical Agency"
FINDING_TEXT = "Example Statistical Agency measured 10.4 GW of capacity in 2024."
PAGE_TEXT = (
    FINDING_TEXT
    + " The measurement covers utility-scale installations in the United States."
)
READER_LABEL = "Example Statistical Agency's own figure; actual"
PAGE_URL = "https://real.test/one"


def _verified_pair() -> tuple[ReadRecord, Finding]:
    """One read, and the finding whose single figure the verifier kept."""
    read = build_read_record(
        session_id="matrix-session",
        reader="web_scraper",
        requested_url=PAGE_URL,
        resolved_url=PAGE_URL,
        title="Measured capacity",
        retrieved_at=EXTRACTED_AT,
        text=PAGE_TEXT,
        passages={"page-1-chunk-0": PAGE_TEXT},
    )
    wanted = FindingFigure(value="10.4", unit="GW", period="2024", kind="actual")
    finding = Finding(
        content=FINDING_TEXT,
        source_url=read.resolved_url,
        source_title=read.title,
        extracted_at=EXTRACTED_AT,
        confidence=0.8,
        related_sub_topic="Angle",
        snippet=FINDING_TEXT,
        read_id=read.read_id,
        locator="page-1-chunk-0",
        figures=[wanted],
        verification=FindingVerification(
            status="verified",
            figure_results=[
                FigureResult(
                    figure=wanted,
                    matched=True,
                    evidence_words=FINDING_TEXT,
                    context=FigureContext(
                        period="2024",
                        attribution="own",
                        organisation=ORGANISATION,
                        kind="actual",
                    ),
                )
            ],
        ),
    )
    return read, finding


def _context_check_messages() -> list:
    """One Context Check batch, labelled the way the verifier labels it."""
    read, finding = _verified_pair()
    return context_check_messages(
        [
            ContextItem(
                label="F01",
                finding=finding,
                read=read,
                passage=context_passage(read, finding.locator, finding.snippet),
                match=FigureMatch(read_found=True, snippet_on_page=True),
                issuer=ORGANISATION,
            )
        ]
    )


def _statement_check_messages() -> list:
    """One Statement Check batch: the sentence and the figure it cites."""
    _, finding = _verified_pair()
    return statement_check_messages(
        [
            StatementCheckItem(
                label="S001",
                text=FINDING_TEXT,
                findings=[finding],
                labels=[READER_LABEL],
            )
        ],
        question=EVIDENCE_QUESTION,
    )


def _section_messages() -> list:
    """The Report Writer's section request for one target and one finding."""
    _, finding = _verified_pair()
    target = make_target()
    sub_topic = _sub_topic()
    task = ReportWriterTask(
        session_id="matrix-session",
        instruction=EVIDENCE_QUESTION,
        question=EVIDENCE_QUESTION,
        iteration=0,
        max_extra_passes=1,
        as_of="2026-08-01",
        scope="United States",
        generated_on="2026-08-01",
        sub_topics=[sub_topic],
        targets=[target],
        findings=[finding],
        sources=[],
        registry=[("F01", finding)],
        not_found=[],
        answered={target.target_id: ["F01"]},
    )
    job = PartJob(coverage_id=sub_topic.coverage_id, sub_topic_title=sub_topic.title, order=0,
                 targets=[target], findings=[finding], context_findings=[], previous=None,
                 defects=[], redraft=True)
    return section_messages(task, job)


def _bottom_line_messages() -> list:
    """The Report Writer's bottom-line request for one checked section statement."""
    _, finding = _verified_pair()
    target = make_target()
    task = ReportWriterTask(
        session_id="matrix-session",
        instruction=EVIDENCE_QUESTION,
        question=EVIDENCE_QUESTION,
        iteration=0,
        max_extra_passes=1,
        as_of="2026-08-01",
        scope="United States",
        generated_on="2026-08-01",
        sub_topics=[_sub_topic()],
        targets=[target],
        findings=[finding],
        sources=[],
        registry=[("F01", finding)],
        not_found=[],
        answered={target.target_id: ["F01"]},
    )
    statement = ReportStatement(statement_id="S001", text=FINDING_TEXT,
                                finding_ids=[finding_fingerprint(finding)], target_ids=[target.target_id])
    section = ReportSection(title="Angle", coverage_id="topic-01",
                            points=[ReportPoint(text=FINDING_TEXT, statement=statement)])
    return bottom_line_messages(task, [section])


def _review_packet() -> ReportReviewInput:
    """The reviewer's packet: one statement, its finding, and the report text."""
    _, finding = _verified_pair()
    return ReportReviewInput(
        question=EVIDENCE_QUESTION,
        reader_content=(
            f"# {EVIDENCE_QUESTION}\n\n"
            "*As of 2026-08-01. Scope: United States. 1 sources cited; "
            "1 findings checked against their pages (0 with corrected "
            "context, 0 with unchecked context), 0 dropped; 0 required "
            "targets not found.*\n\n"
            "## Executive summary\n\n"
            f"- {FINDING_TEXT} [1]\n\n"
            "## Key facts\n\n"
            "| Organisation | Measure | Period | Value | Kind | Scope | "
            "Release or edition | Source |\n"
            "|---|---|---|---|---|---|---|---|\n"
            f"| {ORGANISATION} | capacity | 2024 | 10.4 GW | actual | "
            "not stated | not stated | [1] |\n\n"
            "## Sources\n\n"
            f"1. Measured capacity — {PAGE_URL}\n"
        ),
        statements=[
            ReviewStatementView(
                statement_id="S001",
                text=FINDING_TEXT,
                label=READER_LABEL,
                finding_refs=["F01"],
                finding_labels=[READER_LABEL],
                target_ids=[make_target().target_id],
            )
        ],
        findings=[
            ReviewFindingView(
                label="F01",
                finding_id=finding_fingerprint(finding),
                source_title="Measured capacity",
                host="real.test",
                snippet=FINDING_TEXT,
                figure_labels=[READER_LABEL],
            )
        ],
        fact_rows=[f"- 10.4 GW (capacity) | period 2024 | kind actual | {READER_LABEL}"],
        not_found=[],
        deterministic=ReviewDeterministic(hard_checks=[], unjudged_sentences=[]),
        composition_fingerprint="composition-1",
        fingerprint="packet-1",
    )


def _review_messages() -> list:
    """The Report Reviewer's review request for one statement and its finding."""
    return review_messages(_review_packet())


# The first half's three requests: every one is built from a shared example
# table and keeps its own sections at one heading level. The evidence-verifier
# pipeline's four requests (built above) are covered by the matrix below, whose
# rows declare their own heading levels — their batch, item and registry blocks
# are "## " headings, so this half's level-1 rule is not theirs to hold.
TOOL_FREE_STRUCTURED_MESSAGE_CASES = (
    pytest.param(_planner_messages, id="planner-plan"),
    pytest.param(_extraction_messages, id="researcher-extraction"),
    pytest.param(_scoring_messages, id="source-evaluator-scoring"),
)


@pytest.mark.parametrize("build_messages", TOOL_FREE_STRUCTURED_MESSAGE_CASES)
def test_tool_free_structured_system_prompts_advertise_no_tool(
    build_messages,
) -> None:
    developer = build_messages()[0].content
    advertised = {
        name
        for name in REGISTERED_TOOL_NAMES
        if re.search(
            rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])",
            developer,
        )
    }
    assert advertised == set()


@pytest.mark.parametrize("build_messages", TOOL_FREE_STRUCTURED_MESSAGE_CASES)
def test_every_tool_free_request_has_one_heading_level_and_one_reply_format(
    build_messages,
) -> None:
    messages = build_messages()
    body = messages[1].content

    # Every request-owned heading is exactly one '# '.
    headings = [
        line
        for line in body.splitlines()
        if line.startswith("#") and not line.startswith("# ")
    ]
    assert headings == []
    assert body.startswith("# ")

    # Exactly one output-shape sentence owns the literal phrase, and it is the
    # shared one.
    assert body.count("JSON object") == 1
    assert STRUCTURED_REPLY_FORMAT in body
    assert STRUCTURED_EXAMPLE_NOTICE in body


@pytest.mark.parametrize("build_messages", TOOL_FREE_STRUCTURED_MESSAGE_CASES)
def test_every_tool_free_request_carries_at_most_two_bounded_examples(
    build_messages,
) -> None:
    body = build_messages()[1].content
    rendered = body.split("Example JSON output:\n")[1:]
    assert 1 <= len(rendered) <= 2
    # Each example is one compact JSON-object line, never a fence.
    for payload in rendered:
        line = payload.splitlines()[0]
        assert line.startswith("{") and line.endswith("}")
        assert "```" not in payload
    assert '```' not in body


def _judge_examples() -> tuple[tuple[str, str], ...]:
    """The Judge's rendered pair, read back out of the real request.

    The Judge's two examples are generated from the rubric in hand rather than
    kept in a table, so they are lifted from the prompt the model would receive.
    """
    lines = _judge_messages()[1].content.splitlines()
    labels = ("Weak run:", "Strong run:")
    return tuple(
        (line.strip(), lines[index + 1])
        for index, line in enumerate(lines)
        if line.strip() in labels
    )


# --- the example tables themselves -------------------------------------------


def _example_tables() -> tuple:
    """Every example table, paired with the exact model it must validate as."""
    return (
        (_PLAN_REPLY_EXAMPLES, ResearchPlanDraft),
        (_FINDING_REPLY_EXAMPLES, SubTopicFindingsDraft),
        (_SOURCE_SCORE_REPLY_EXAMPLES, SourceScoresDraft),
        (_judge_examples(), JudgeVerdict),
    )


@pytest.mark.parametrize(
    ("examples", "schema"),
    _example_tables(),
    ids=lambda value: getattr(value, "__name__", None) or "examples",
)
def test_every_example_is_schema_valid_bounded_and_reserved(examples, schema) -> None:
    assert 1 <= len(examples) <= 2
    for label, payload in examples:
        schema.model_validate_json(payload)
        assert label.strip() and "\n" not in label
        # The three shared tables introduce an isolated example input and the
        # Judge's generated pair labels its cases weak and strong.
        lowered = label.lower()
        assert (
            "example input" in lowered
            or lowered in {"weak run:", "strong run:"}
        ), label
        assert "```" not in payload
        assert "<" not in payload and ">" not in payload


@pytest.mark.parametrize(
    ("examples", "schema"),
    _example_tables(),
    ids=lambda value: getattr(value, "__name__", None) or "examples",
)
def test_every_synthetic_url_uses_the_reserved_test_domain(examples, schema) -> None:
    del schema
    for _, payload in examples:
        for url in re.findall(r"https?://[^\"\s]+", payload):
            assert ".example.test" in url, url


# --- the cross-operation conformance matrix -----------------------------------
#
# One row per tool-free structured operation the task's inventory names. Every
# row renders its request through the real builder and reads its examples back
# out of that rendered text, so a prompt cannot pass this matrix while shipping
# a different prompt than the one asserted here.

# The inventory verbatim: operation, owning agent, and the number of examples
# the operation is allowed to carry. Two examples are allowed only where the
# second shows what a rule cannot: the planner's two plan shapes, the source
# evaluator's weak and strong ends of one scale, and the judge's pair.
PLANNED_OPERATION_INVENTORY = {
    "plan finalization": ("planner", 2),
    # The extraction's second example is the text-finding shape: the first
    # teaches a figure finding whose fields are all stated by its passage, and
    # a rule, a reproduced instrument or a relayed statement — what a
    # qualitative target usually rests on — carries no figure at all
    # (EXTRA-2, review RES-6 §1).
    "finding extraction": ("researcher", 2),
    "source scoring": ("source_evaluator", 2),
    "evaluation verdict": ("judge", 2),
    # The evidence-verifier pipeline's four tool-free requests (Task 4.10d).
    # The report review renders one example like the rest of the pipeline: D10
    # gave it the shared reply format, so its row holds every convention here.
    "context check": ("evidence_verifier", 1),
    # Run-2 improvement 8 gave the statement check its second example: the
    # first shows a wrong forecast/actual distinction, the second a conditional
    # rule stated without its condition or exception, judged against the
    # passage beside the snippet. Two failure classes, one example each.
    "statement check": ("evidence_verifier", 2),
    # The parallel writer's two calls (spec §6.3, §6.6): the section call
    # keeps the writer's two-example shapes (a figure line and its snippet,
    # then a statement-only finding whose line names only the host it was
    # read on -- the two shapes whose crediting the audits found wrong, run 2
    # S005/S009, smoke 1), and the bottom-line call gets one neutral example
    # (WRI-6): a fresh call with its own reply schema, not another shape the
    # first needs to teach.
    "report section drafting": ("report_writer", 2),
    "report bottom line drafting": ("report_writer", 1),
    "report review": ("report_reviewer", 1),
}


def _labelled_examples(body: str) -> tuple[tuple[str, str], ...]:
    """The shared tables: a label line, then ``Example JSON output:``."""
    lines = body.splitlines()
    return tuple(
        (lines[index - 1], lines[index + 1])
        for index, line in enumerate(lines)
        if line.strip() == "Example JSON output:"
    )


def _anchored_examples(*labels: str) -> Callable[[str], tuple[tuple[str, str], ...]]:
    """The Judge's pair, which labels its cases instead of ``Example input``."""
    anchors = set(labels)

    def extract(body: str) -> tuple[tuple[str, str], ...]:
        lines = body.splitlines()
        return tuple(
            (line.strip(), lines[index + 1])
            for index, line in enumerate(lines)
            if line.strip() in anchors
        )

    return extract


@dataclass(frozen=True)
class StructuredOperation:
    """One tool-free structured operation and the request it renders."""

    operation: str
    agent: str
    build_messages: Callable[[], list]
    schema: type[BaseModel]
    examples: Callable[[str], tuple[tuple[str, str], ...]]
    # D10 (PD-29): the request-owned sections before "# Reply format", in order.
    # None only for the evaluation judge, which keeps its reply contract last:
    # it is not a pipeline request.
    static_headings: tuple[str, ...] | None = None
    reply_heading: str = "# Reply format"
    heading_levels: tuple[int, ...] = (1,)

    def body(self) -> str:
        """The user message: this request's own text plus any evidence."""
        return self.build_messages()[1].content


OPERATIONS = (
    StructuredOperation(
        operation="plan finalization",
        agent="planner",
        build_messages=_planner_messages,
        schema=ResearchPlanDraft,
        examples=_labelled_examples,
        static_headings=("# Plan requirements",),
    ),
    # PD-29's one recorded exemption (final-review fix round, slice 3): the
    # plan review is the single tool-free pipeline request that is not
    # rendered static-first. Rebuilding it that way is not a request-shaped
    # edit: this matrix's pipeline assertions require the envelope to carry
    # exactly one "# Reply format" and one "JSON object", which is what
    # ``render_structured_reply_format`` prints from one or two examples —
    # and the plan review's reply is a four-field verdict the system prompt
    # already describes, with no example to show. Adding one to satisfy a
    # matrix would be inventing a prompt this request never needed, so the
    # exemption is recorded here instead, and the request keeps its own
    # envelope. A future change that does give it a reply format adds the row
    # with ``static_headings=("# Review requirements",)``.
    StructuredOperation(
        operation="finding extraction",
        agent="researcher",
        build_messages=_extraction_messages,
        schema=SubTopicFindingsDraft,
        examples=_labelled_examples,
        static_headings=("# Response contract",),
    ),
    StructuredOperation(
        operation="source scoring",
        agent="source_evaluator",
        build_messages=_scoring_messages,
        schema=SourceScoresDraft,
        examples=_labelled_examples,
        static_headings=("# Scoring contract",),
    ),
    StructuredOperation(
        operation="evaluation verdict",
        agent="judge",
        build_messages=_judge_messages,
        schema=JudgeVerdict,
        examples=_anchored_examples("Weak run:", "Strong run:"),
        reply_heading="## Reply format",
        heading_levels=(1, 2),
    ),
    # The evidence-verifier pipeline's four tool-free requests. Each row's
    # heading levels are the levels the request itself renders: the batch and
    # item blocks of both checks and the writer's registry lines are "## "
    # headings, and the review quotes the reader report's own Markdown inside
    # its fence and heads each cited finding with "### ".
    StructuredOperation(
        operation="context check",
        agent="evidence_verifier",
        build_messages=_context_check_messages,
        schema=ContextCheckDraft,
        examples=_labelled_examples,
        static_headings=("# Response contract",),
        heading_levels=(1, 2),
    ),
    StructuredOperation(
        operation="statement check",
        agent="evidence_verifier",
        build_messages=_statement_check_messages,
        schema=StatementCheckDraft,
        examples=_labelled_examples,
        static_headings=("# Response contract",),
        heading_levels=(1, 2),
    ),
    StructuredOperation(
        operation="report section drafting",
        agent="report_writer",
        build_messages=_section_messages,
        schema=SectionDraft,
        examples=_labelled_examples,
        static_headings=("# Rules",),
        heading_levels=(1, 2),
    ),
    StructuredOperation(
        operation="report bottom line drafting",
        agent="report_writer",
        build_messages=_bottom_line_messages,
        schema=BottomLineDraft,
        examples=_labelled_examples,
        static_headings=("# Rules",),
        heading_levels=(1, 2),
    ),
    StructuredOperation(
        operation="report review",
        agent="report_reviewer",
        build_messages=_review_messages,
        schema=ReportReviewDraft,
        examples=_labelled_examples,
        static_headings=("# Response contract", "# What each dimension means"),
        heading_levels=(1, 3),
    ),
)

OPERATIONS_BY_NAME = {operation.operation: operation for operation in OPERATIONS}


def _operation_id(operation: StructuredOperation) -> str:
    return operation.operation.replace(" ", "-")


def _examples_for(operation_name: str) -> tuple[tuple[str, str], ...]:
    operation = OPERATIONS_BY_NAME[operation_name]
    return operation.examples(operation.body())


def _request_envelope(body: str) -> str:
    """This request's own text, without any embedded fenced provider content.

    The Report Reviewer quotes the reader report inside a Markdown fence whose
    body is the report's own Markdown — headings included. Nothing inside that
    fence is part of the request, so the request's shape is asserted on the
    envelope. The other seven operations embed no fenced provider content, so
    their envelope is their whole body.
    """
    lines = body.splitlines()
    fences = [
        index for index, line in enumerate(lines) if line.startswith("```")
    ]
    if not fences:
        return body
    opening, closing = fences[0], fences[-1]
    return "\n".join([*lines[:opening], *lines[closing + 1 :]])


def _advertised_tools(text: str) -> set[str]:
    """The registered tools ``text`` names, by whole word."""
    return {
        name
        for name in REGISTERED_TOOL_NAMES
        if re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", text)
    }


def test_the_matrix_covers_exactly_the_planned_operation_inventory() -> None:
    """Step 1: every operation, with the planned number of rendered examples."""
    rendered = {
        operation.operation: (
            operation.agent,
            len(operation.examples(operation.body())),
        )
        for operation in OPERATIONS
    }

    assert rendered == PLANNED_OPERATION_INVENTORY


@pytest.mark.parametrize("operation", OPERATIONS, ids=_operation_id)
def test_every_rendered_example_is_valid_json_schema_valid_and_reserved(
    operation: StructuredOperation,
) -> None:
    """Step 1: parse, validate, bound, unfence, and reserve every example."""
    examples = operation.examples(operation.body())

    assert 1 <= len(examples) <= 2
    for label, payload in examples:
        decoded = json.loads(payload)
        assert isinstance(decoded, dict)
        operation.schema.model_validate(decoded)
        assert label.strip() and "\n" not in label
        assert "```" not in payload
        assert "<" not in payload and ">" not in payload
        for url in re.findall(r"https?://[^\"\s]+", payload):
            assert ".example.test" in url, url


PIPELINE_OPERATIONS = tuple(
    operation for operation in OPERATIONS if operation.static_headings is not None
)


@pytest.mark.parametrize("operation", PIPELINE_OPERATIONS, ids=_operation_id)
def test_every_pipeline_request_puts_its_static_sections_first(
    operation: StructuredOperation,
) -> None:
    """D10 (E2.4): the contract and the reply format lead, the material follows."""
    envelope = _request_envelope(operation.body())
    lines = envelope.rstrip().splitlines()

    assert lines[-1] == STRUCTURED_REQUEST_END
    assert envelope.count("JSON object") == 1
    assert envelope.count("# Reply format") == 1
    assert "```" not in envelope
    assert "## Tools" not in envelope

    sections = [line for line in lines if line.startswith("# ")]
    reply = sections.index("# Reply format")
    assert tuple(sections[:reply]) == operation.static_headings
    assert reply < len(sections) - 1
    levels = {len(line) - len(line.lstrip("#")) for line in lines if line.startswith("#")}
    assert levels <= set(operation.heading_levels)

    start = envelope.index("# Reply format")
    reply_format = envelope[start : envelope.index("\n# ", start)]
    for field in operation.schema.model_fields:
        assert f'"{field}"' in reply_format, field


@pytest.mark.parametrize(
    "operation",
    [operation for operation in OPERATIONS if operation.static_headings is None],
    ids=_operation_id,
)
def test_the_judge_request_keeps_its_reply_contract_last(
    operation: StructuredOperation,
) -> None:
    """The evaluation judge is outside the pipeline, so D10's layout is not its own."""
    body = operation.body()
    envelope = _request_envelope(body)

    assert envelope.count("JSON object") == 1
    assert envelope.count("# Reply format") == 1
    assert envelope.rstrip().splitlines()[-1].strip().endswith("}")
    assert "```" not in envelope
    assert "## Tools" not in envelope

    # The last request-owned section is the reply contract.
    headings = [line for line in envelope.splitlines() if line.startswith("#")]
    assert headings[-1] == operation.reply_heading
    levels = {len(line) - len(line.lstrip("#")) for line in headings}
    assert levels <= set(operation.heading_levels)

    # Every field is stated in that one contract, and no competing prose
    # protocol repeats it.
    contract = envelope[envelope.index(operation.reply_heading) :]
    for field in operation.schema.model_fields:
        assert f'"{field}"' in contract, field


@pytest.mark.parametrize("operation", OPERATIONS, ids=_operation_id)
def test_no_structured_request_names_a_registered_tool(
    operation: StructuredOperation,
) -> None:
    """The request offers no tools, so neither message may name one."""
    messages = operation.build_messages()

    for message in messages:
        assert _advertised_tools(message.content) == set(), operation.operation


# The one operation in this matrix that embeds provider content, and so the one
# whose request carries a fence at all.
EMBEDDING_OPERATION = "report review"


def _unfenced_operations() -> tuple:
    """Every row whose request embeds nothing, so it renders no fence at all.

    The one embedding request is filtered out here rather than excluded by
    silence: its fence has its own positive assertion below.
    """
    return tuple(
        operation
        for operation in OPERATIONS
        if operation.operation != EMBEDDING_OPERATION
    )


@pytest.mark.parametrize(
    "operation", _unfenced_operations(), ids=_operation_id
)
def test_no_request_in_the_matrix_carries_a_markdown_fence(
    operation: StructuredOperation,
) -> None:
    """A request that embeds no provider content renders no fence.

    A fence in any of these would be this request's own reply shape, which the
    reply contract forbids. The request that does embed content is asserted
    positively in the next test.
    """
    fences = [
        line for line in operation.body().splitlines() if line.startswith("```")
    ]

    assert fences == []


def test_the_report_review_request_fences_the_reader_report_it_embeds() -> None:
    """The one embedding request quotes its content in one labelled fence.

    The pre-sweep matrix asserted this shape for the review request it carried:
    the provider text travels whole, between a delimiter no run inside it can
    close, so the report's own headings are read as the report's rather than as
    sections of this request. Restored here because the reviewer is again the
    matrix's one embedding request: exactly one balanced fence, opened with the
    ``report`` info string, and its content is the packet's own reader content,
    character for character.
    """
    packet = _review_packet()
    lines = _review_messages()[1].content.splitlines()
    fences = [index for index, line in enumerate(lines) if line.startswith("```")]

    assert [lines[index] for index in fences] == ["```report", "```"]
    assert "\n".join(lines[fences[0] + 1 : fences[-1]]) == packet.reader_content.rstrip()


# --- scoring clarity and opposite examples ------------------------------------


def test_the_source_scoring_pair_is_schema_valid_and_opposite() -> None:
    """Step 3: a weak and a strong source, in the same direction, per dimension."""
    labelled = {
        label.split()[0].lower(): payload
        for label, payload in _examples_for("source scoring")
    }

    assert set(labelled) == {"weak", "strong"}
    weak = SourceScoresDraft.model_validate_json(labelled["weak"])
    strong = SourceScoresDraft.model_validate_json(labelled["strong"])

    assert len(weak.sources) == len(strong.sources) == 1
    assert weak.sources[0].url != strong.sources[0].url
    for dimension in ("authority_score", "recency_score", "relevance_score"):
        low = getattr(weak.sources[0], dimension)
        high = getattr(strong.sources[0], dimension)
        assert 0.0 <= low < high <= 1.0, dimension
    assert weak.sources[0].rationale.strip()
    assert strong.sources[0].rationale.strip()


def _read_backed_scoring_messages() -> list:
    """The source-scoring request for a dossier built from an actual read."""
    read = build_read_record(
        session_id="session-1",
        reader="web_scraper",
        requested_url="https://lab.example/report",
        resolved_url="https://lab.example/report",
        title="Grid Storage Outlook",
        retrieved_at="2026-09-16T10:00:00+00:00",
        text=(
            "Grid Storage Outlook. Published by Example Lab on 2026-01-15. "
            "Example Lab measured that 1,200 MW was withheld during 2024."
        ),
        passages={
            "chunk-0": (
                "Grid Storage Outlook. Published by Example Lab on "
                "2026-01-15. Example Lab measured that 1,200 MW was withheld "
                "during 2024."
            )
        },
        target_ids=("target-1",),
    )
    dossier = build_read_dossiers(
        [read], cited_sub_topics={read.resolved_url: ["Grid storage"]}
    )[0]
    return scoring_messages(
        SourceEvaluationTask(
            instruction="Score every source behind the findings.",
            groups=[
                SourceGroup(
                    url=read.resolved_url,
                    domain="lab.example",
                    title=read.title,
                    sub_topics=["Grid storage"],
                )
            ],
            dossiers={read.resolved_url: dossier},
        ),
        excerpt_chars=200,
    )


def test_a_read_backed_scoring_request_shows_the_read_and_names_no_tool() -> None:
    """The read-backed request is still a tool-free structured request.

    It shows the model what the *document* says — the host that served it, the
    text itself, and the register — so a role, a transport relation, and a
    date are judged from the read rather than from a finding's paraphrase of
    it.
    """
    messages = _read_backed_scoring_messages()
    developer, body = messages[0].content, messages[1].content

    for content in (developer, body):
        assert _advertised_tools(content) == set()

    assert body.count("JSON object") == 1
    assert body.count("# Reply format") == 1
    assert "https://lab.example/report" in body
    assert "Serving host: lab.example" in body
    assert "Example Lab measured that 1,200 MW was withheld" in body
    assert "Grid storage" in body

    contract = body[body.index("# Reply format") :]
    for field in (
        "source_role",
        "transport_relation",
        "self_interest",
        "publication_date",
        "data_period",
        "forecast_horizon",
        "effective_date",
        "freshness_status",
        "methods_score",
        "issuer",
        "doi",
    ):
        assert f'"{field}"' in contract, field


def test_the_scoring_contract_asks_for_a_verbatim_quote_per_date() -> None:
    """A date is admitted only through its quote, so the request must ask.

    The local check is containment: the model's value survives only when the
    document's own words state it. The prompt therefore has to ask for the
    words and for a null when the document states nothing.
    """
    body = _read_backed_scoring_messages()[1].content
    contract = body[body.index("# Scoring contract") : body.index("# Reply format")]

    assert "verbatim" in contract
    assert '"quote"' in contract
    assert "never infer a date" in contract
    assert "Return null" in contract
    # The reply shape shows the same thing, so the example cannot teach the
    # model to send a bare date string.
    reply = body[body.index("# Reply format") :]
    assert '"publication_date":{"quote":' in reply
    assert '"publication_date":null' in reply


# --- the pinned provider repair instructions ----------------------------------
#
# Step 4: every structured transport must ask for the same JSON object the agent
# prompt asks for, carry the exact schema the reply is validated against, invite
# no Markdown fence, and repair exactly once. The real provider classes are
# driven through injected clients that record every request, so these assertions
# are made on the request the transport would actually send.

# The literal sentence both DeepSeek transports append to the prompt.
DEEPSEEK_JSON_DEMAND = (
    "Return only one JSON object that validates against this JSON Schema. "
    "Do not add Markdown or explanatory text."
)

MATRIX_ANSWER_MAX_LENGTH = 40


class MatrixAnswer(BaseModel):
    """A local strict schema: bounded string, bounded integer, no extras."""

    model_config = ConfigDict(extra="forbid")

    answer: str = Field(min_length=1, max_length=MATRIX_ANSWER_MAX_LENGTH)
    confidence: int = Field(ge=0, le=10)


_STRUCTURED_MESSAGES = [
    ChatMessage(
        role="user",
        content=(
            "# Reply format\n"
            f"{STRUCTURED_REPLY_FORMAT}\n"
            "Example JSON output:\n"
            '{"answer":"example","confidence":5}'
        ),
    )
]

# One reply per fault the task names: unparseable text, a missing field, an
# undeclared field on a strict schema, a wrong type, and an over-long string.
FAULT_REPLIES = (
    ("malformed-json", "{not json"),
    ("missing-field", json.dumps({"answer": "parsed"})),
    (
        "extra-field",
        json.dumps({"answer": "parsed", "confidence": 5, "note": "undeclared"}),
    ),
    ("wrong-type", json.dumps({"answer": 7, "confidence": "high"})),
    (
        "out-of-bounds-string",
        json.dumps(
            {"answer": "x" * (MATRIX_ANSWER_MAX_LENGTH + 1), "confidence": 5}
        ),
    ),
)


class _RecordingTransport:
    """A transport recorder: one queued outcome per request, in order."""

    def __init__(self, *outcomes: object) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> object:
        return self._next(kwargs)

    async def parse(self, **kwargs: Any) -> object:
        return self._next(kwargs)

    def _next(self, kwargs: dict[str, Any]) -> object:
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _deepseek_config() -> LLMConfig:
    return LLMConfig.model_validate(
        {
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "thinking_mode": "enabled",
            "reasoning_effort": "high",
        }
    )


def _openai_config() -> LLMConfig:
    return LLMConfig.model_validate(
        {
            "provider": "openai",
            "model": "gpt-4o",
            "thinking_mode": "disabled",
            "reasoning_effort": "none",
        }
    )


def _offline_tracker() -> Tracker:
    return Tracker(LangSmithRuntimeConfig(tracing_enabled=False))


def _chat_reply(text: str) -> SimpleNamespace:
    """One clean Chat Completions response carrying ``text``."""
    return SimpleNamespace(
        id="matrix-chat-response",
        model="deepseek-v4-flash",
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(
                    content=text, reasoning_content=None, tool_calls=None
                ),
            )
        ],
        usage=SimpleNamespace(prompt_tokens=4, completion_tokens=2, total_tokens=6),
    )


def _responses_reply(text: str, parsed: object = None) -> SimpleNamespace:
    """One clean Responses response; ``parsed`` is what the SDK would return."""
    return SimpleNamespace(
        id="matrix-responses-response",
        status="completed",
        incomplete_details=None,
        output_text=text,
        output_parsed=parsed,
        model="matrix-model",
        usage=SimpleNamespace(input_tokens=4, output_tokens=2, total_tokens=6),
    )


def _canonical_schema(schema: type[BaseModel]) -> str:
    """The schema exactly as the DeepSeek transports state it in the prompt."""
    return json.dumps(
        schema.model_json_schema(), sort_keys=True, separators=(",", ":")
    )


class _StructuredTransport:
    """One provider transport for a tool-free structured request."""

    name: str
    provider_type: type
    schema_in_prompt: bool

    def __init__(self, outcomes: Sequence[object]) -> None:
        self._tracker = _offline_tracker()
        self._records = _RecordingTransport(*outcomes)
        self.provider = self._build_provider()

    def _build_provider(self) -> Any:
        raise NotImplementedError

    @property
    def requests(self) -> list[dict[str, Any]]:
        """Every request the transport was sent, in order."""
        return self._records.calls

    def prompt(self, request: Mapping[str, Any]) -> str:
        """The whole prompt text of one request."""
        raise NotImplementedError

    def transport(self, request: Mapping[str, Any]) -> object:
        """The schema transport one request carries."""
        raise NotImplementedError

    def expected_transport(self, schema: type[BaseModel]) -> object:
        """The exact schema transport this provider must use."""
        raise NotImplementedError

    def first_demands(self, schema: type[BaseModel]) -> tuple[str, ...]:
        """Literal text the first request's prompt must contain."""
        raise NotImplementedError

    def repair_demands(self, schema: type[BaseModel]) -> tuple[str, ...]:
        """Literal text the repair request's prompt must contain."""
        raise NotImplementedError

    @staticmethod
    def invalid(text: str) -> object:
        """An outcome that fails validation for the reason ``text`` states."""
        raise NotImplementedError

    @staticmethod
    def valid(model: BaseModel) -> object:
        """An outcome carrying ``model`` as a valid reply."""
        raise NotImplementedError

    @asynccontextmanager
    async def session(self) -> Iterator[None]:
        async with self._tracker.session_span("matrix-session", "matrix"):
            yield

    async def complete(self, schema: type[BaseModel]) -> Any:
        return await self.provider.complete_structured(_STRUCTURED_MESSAGES, schema)


class DeepSeekChatTransport(_StructuredTransport):
    """Chat Completions JSON mode: ``json_object`` plus a schema message."""

    name = "deepseek-chat"
    provider_type = DeepSeekChatProvider
    schema_in_prompt = True

    def _build_provider(self) -> DeepSeekChatProvider:
        client = SimpleNamespace(chat=SimpleNamespace(completions=self._records))
        return DeepSeekChatProvider(
            _deepseek_config(), self._tracker, client=client
        )

    def prompt(self, request: Mapping[str, Any]) -> str:
        return "\n".join(
            str(message["content"]) for message in request["messages"]
        )

    def transport(self, request: Mapping[str, Any]) -> object:
        return request["response_format"]

    def expected_transport(self, schema: type[BaseModel]) -> object:
        return {"type": "json_object"}

    def first_demands(self, schema: type[BaseModel]) -> tuple[str, ...]:
        return (
            STRUCTURED_REPLY_FORMAT,
            DEEPSEEK_JSON_DEMAND,
            f"JSON Schema:\n{_canonical_schema(schema)}",
        )

    def repair_demands(self, schema: type[BaseModel]) -> tuple[str, ...]:
        return (
            STRUCTURED_REPLY_FORMAT,
            f"The previous JSON response failed {schema.__name__} validation.",
            DEEPSEEK_JSON_DEMAND,
            f"JSON Schema:\n{_canonical_schema(schema)}",
        )

    @staticmethod
    def invalid(text: str) -> object:
        return _chat_reply(text)

    @staticmethod
    def valid(model: BaseModel) -> object:
        return _chat_reply(model.model_dump_json())


class DeepSeekJudgeResponsesTransport(_StructuredTransport):
    """Responses ``json_schema``: the judge's own schema-enforced transport."""

    name = "deepseek-judge-responses"
    provider_type = DeepSeekJudgeProvider
    schema_in_prompt = True

    def _build_provider(self) -> DeepSeekJudgeProvider:
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=_RecordingTransport()),
            responses=self._records,
        )
        return DeepSeekJudgeProvider(
            _deepseek_config(), self._tracker, client=client
        )

    def prompt(self, request: Mapping[str, Any]) -> str:
        return "\n".join(str(message["content"]) for message in request["input"])

    def transport(self, request: Mapping[str, Any]) -> object:
        return request["text"]["format"]

    def expected_transport(self, schema: type[BaseModel]) -> object:
        return {
            "type": "json_schema",
            "name": schema.__name__,
            "schema": schema.model_json_schema(),
        }

    def first_demands(self, schema: type[BaseModel]) -> tuple[str, ...]:
        return (
            STRUCTURED_REPLY_FORMAT,
            DEEPSEEK_JSON_DEMAND,
            f"JSON Schema:\n{_canonical_schema(schema)}",
        )

    def repair_demands(self, schema: type[BaseModel]) -> tuple[str, ...]:
        return (
            STRUCTURED_REPLY_FORMAT,
            f"The previous JSON response failed {schema.__name__} validation.",
            DEEPSEEK_JSON_DEMAND,
            f"JSON Schema:\n{_canonical_schema(schema)}",
        )

    @staticmethod
    def invalid(text: str) -> object:
        return _responses_reply(text)

    @staticmethod
    def valid(model: BaseModel) -> object:
        return _responses_reply(model.model_dump_json())


class OpenAIResponsesTransport(_StructuredTransport):
    """OpenAI Responses: the SDK transmits ``text_format`` for every attempt."""

    name = "openai-responses"
    provider_type = OpenAIChatProvider
    schema_in_prompt = False

    def _build_provider(self) -> OpenAIChatProvider:
        client = SimpleNamespace(responses=self._records)
        return OpenAIChatProvider(_openai_config(), self._tracker, client=client)

    def prompt(self, request: Mapping[str, Any]) -> str:
        return "\n".join(str(message["content"]) for message in request["input"])

    def transport(self, request: Mapping[str, Any]) -> object:
        return request["text_format"]

    def expected_transport(self, schema: type[BaseModel]) -> object:
        return schema

    def first_demands(self, schema: type[BaseModel]) -> tuple[str, ...]:
        # This transport constrains decoding with the schema itself rather than
        # with a prose JSON keyword, so the only literal demand it carries is the
        # caller's own reply contract.
        return (STRUCTURED_REPLY_FORMAT,)

    def repair_demands(self, schema: type[BaseModel]) -> tuple[str, ...]:
        return (
            STRUCTURED_REPLY_FORMAT,
            f"The previous response failed {schema.__name__} validation.",
            "Return a corrected response that matches the supplied schema exactly.",
        )

    @staticmethod
    def invalid(text: str) -> object:
        return _responses_reply(text, parsed=None)

    @staticmethod
    def valid(model: BaseModel) -> object:
        return _responses_reply(model.model_dump_json(), parsed=model)


TRANSPORT_TYPES = (
    DeepSeekChatTransport,
    DeepSeekJudgeResponsesTransport,
    OpenAIResponsesTransport,
)


def _transport_id(transport_type: type) -> str:
    return transport_type.name


def test_the_repair_matrix_covers_the_three_planned_transports() -> None:
    """Step 4: the three transports the task names, and their real classes."""
    assert [transport.name for transport in TRANSPORT_TYPES] == [
        "deepseek-chat",
        "deepseek-judge-responses",
        "openai-responses",
    ]
    assert [transport.provider_type for transport in TRANSPORT_TYPES] == [
        DeepSeekChatProvider,
        DeepSeekJudgeProvider,
        OpenAIChatProvider,
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("transport_type", TRANSPORT_TYPES, ids=_transport_id)
async def test_every_structured_transport_pins_json_the_schema_and_no_fence(
    transport_type: type,
) -> None:
    """Both the first and the repair request carry the same JSON contract."""
    repaired = MatrixAnswer(answer="repaired", confidence=5)
    transport = transport_type(
        [transport_type.invalid("{not json"), transport_type.valid(repaired)]
    )

    async with transport.session():
        result = await transport.complete(MatrixAnswer)

    assert result == repaired
    assert len(transport.requests) == 2
    first, repair = transport.requests
    for request in (first, repair):
        prompt = transport.prompt(request)
        # No fence markup, and the caller's own no-fence rule survives.
        assert "```" not in prompt
        assert STRUCTURED_REPLY_FORMAT in prompt
        assert transport.transport(request) == transport.expected_transport(
            MatrixAnswer
        )
    for demand in transport.first_demands(MatrixAnswer):
        assert demand in transport.prompt(first), demand
    for demand in transport.repair_demands(MatrixAnswer):
        assert demand in transport.prompt(repair), demand


@pytest.mark.asyncio
@pytest.mark.parametrize("transport_type", TRANSPORT_TYPES, ids=_transport_id)
@pytest.mark.parametrize(
    ("fault", "payload"),
    FAULT_REPLIES,
    ids=[fault for fault, _ in FAULT_REPLIES],
)
async def test_every_structured_transport_repairs_each_fault_exactly_once(
    transport_type: type, fault: str, payload: str
) -> None:
    """One repair per fault, and the rejected reply is never echoed back."""
    repaired = MatrixAnswer(answer="repaired", confidence=5)
    transport = transport_type(
        [transport_type.invalid(payload), transport_type.valid(repaired)]
    )

    async with transport.session():
        result = await transport.complete(MatrixAnswer)

    assert result == repaired, fault
    assert len(transport.requests) == 2, fault
    repair_prompt = transport.prompt(transport.requests[1])
    assert f"failed {MatrixAnswer.__name__} validation" in repair_prompt, fault
    # Only the bounded category and field path travel with the repair.
    assert payload not in repair_prompt, fault


@pytest.mark.asyncio
@pytest.mark.parametrize("transport_type", TRANSPORT_TYPES, ids=_transport_id)
async def test_every_structured_transport_stops_after_one_failed_repair(
    transport_type: type,
) -> None:
    """A second invalid reply is a typed failure, never a third request."""
    transport = transport_type(
        [
            transport_type.invalid("{not json"),
            transport_type.invalid("{still not json"),
        ]
    )

    async with transport.session():
        with pytest.raises(StructuredOutputError) as caught:
            await transport.complete(MatrixAnswer)

    assert len(transport.requests) == 2
    assert "after one repair attempt" in str(caught.value)
    assert [item.attempt for item in caught.value.diagnostics] == [1, 2]
    assert {item.category for item in caught.value.diagnostics} == {"json_invalid"}
