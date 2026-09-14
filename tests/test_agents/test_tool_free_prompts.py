"""The tool-free half of the prompt/transport congruence invariant.

Every separate structured call — plan, extraction, scoring, claim extraction,
claim verification, report, review, judge — sends no tools. Its developer
message must therefore name none of the registered tools, and it must carry
exactly one output-shape sentence plus at most one or two bounded examples.
These tests build the real messages through the real builders; none of them
copies a prompt string.

The second half of the file is the cross-operation conformance matrix: one row
per tool-free structured operation, asserted against the request that operation
actually renders, and one row per provider transport a structured request can
travel on. Both halves read their subject out of the real builder's output, so
the matrix cannot drift away from the prompt it pins.
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

from deep_research.agents.critic import (
    _CRITIQUE_HIGH_EXAMPLE_JSON,
    _CRITIQUE_LOW_EXAMPLE_JSON,
    _HIGH_EXAMPLE_SCORE,
    _LOW_EXAMPLE_SCORE,
    CritiqueDraft,
    CritiqueTask,
    critique_messages,
)
from deep_research.agents.fact_checker import (
    _CLAIM_EXTRACTION_REPLY_EXAMPLES,
    _CLAIM_VERIFICATION_REPLY_EXAMPLES,
    ClaimsDraft,
    ClaimTask,
    ClaimVerdictDraft,
    claim_extraction_messages,
    claim_verification_messages,
)
from deep_research.agents.planner import (
    _PLAN_REPLY_EXAMPLES,
    ResearchPlanDraft,
    plan_messages,
)
from deep_research.agents.prompts import (
    STRUCTURED_EXAMPLE_NOTICE,
    STRUCTURED_REPLY_FORMAT,
    AgentTask,
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
from deep_research.agents.synthesizer import (
    _REPORT_REPLY_EXAMPLES,
    ReportDraft,
    SynthesisTask,
    report_messages,
)
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
    Claim,
    Finding,
    ResearchState,
    ScoredSource,
    SubTopic,
)

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


def _scored_source(url: str = "https://real.test/one") -> ScoredSource:
    return ScoredSource(
        url=url,
        title="Measured result",
        authority_score=0.8,
        recency_score=0.8,
        relevance_score=0.8,
        overall_score=0.75,
        rationale="Signals recorded.",
    )


def _claim(url: str = "https://real.test/one") -> Claim:
    return Claim(
        text="A measured result was reported.",
        source_urls=[url],
        verdict="verified",
        confidence=0.9,
        evidence=["An independent study reports the same result."],
        contradictions=[],
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


def _claim_extraction_messages() -> list:
    return claim_extraction_messages(
        ResearchState.model_validate(
            {
                "session_id": "session-1",
                "original_question": "How much capacity can quantum computing reach?",
                "raw_findings": [_finding()],
                "evaluated_sources": [_scored_source()],
            }
        ),
        max_findings=10,
    )


def _claim_verification_messages() -> list:
    return claim_verification_messages(
        ClaimTask(
            instruction="Verify the claim.",
            claim={
                "text": "A measured result was reported.",
                "source_urls": [_claim().source_urls[0]],
            },
        ),
        _finished_run("fact_checker"),
        evidence_chars=200,
        independent=["independent.test"],
    )


def _report_messages() -> list:
    return report_messages(
        SynthesisTask(
            instruction="Write the report.",
            session_id="session-1",
            claims=[_claim()],
            sources=[_scored_source()],
            findings=[_finding()],
            limitations=[],
        ),
        finding_digest=10,
        claim_digest=10,
    )


def _critique_messages() -> list:
    return critique_messages(
        CritiqueTask(
            instruction="Review the report.",
            report="# Research report: a measured result was reported.",
            iteration=1,
            max_iterations=3,
            claims=[_claim()],
            sources=[_scored_source()],
            sub_topics=["Angle"],
        ),
        _finished_run("critic"),
        report_chars=6000,
        claim_digest=40,
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


TOOL_FREE_STRUCTURED_MESSAGE_CASES = (
    pytest.param(_planner_messages, id="planner-plan"),
    pytest.param(_extraction_messages, id="researcher-extraction"),
    pytest.param(_scoring_messages, id="source-evaluator-scoring"),
    pytest.param(_claim_extraction_messages, id="fact-checker-claim-extraction"),
    pytest.param(_claim_verification_messages, id="fact-checker-claim-verification"),
    pytest.param(_report_messages, id="synthesizer-report"),
    pytest.param(_critique_messages, id="critic-review"),
)

# The Critic's review request is tool-free like the rest, but its reply format,
# envelope, and examples are its own already-verified specialization and are
# deliberately unchanged by this work, so the shared-format guards skip it.
SHARED_REPLY_FORMAT_CASES = TOOL_FREE_STRUCTURED_MESSAGE_CASES[:-1]


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


@pytest.mark.parametrize("build_messages", SHARED_REPLY_FORMAT_CASES)
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

    sections = [line for line in body.splitlines() if line.startswith("# ")]
    assert sections[-1] == "# Reply format"

    # Exactly one output-shape sentence owns the literal phrase, and it is the
    # shared one.
    assert body.count("JSON object") == 1
    assert STRUCTURED_REPLY_FORMAT in body
    assert STRUCTURED_EXAMPLE_NOTICE in body


@pytest.mark.parametrize("build_messages", SHARED_REPLY_FORMAT_CASES)
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


def _critique_examples() -> tuple[tuple[str, str], ...]:
    """The Critic's preserved pair, in the order it renders."""
    return (
        (f"Weak report, score {_LOW_EXAMPLE_SCORE}:", _CRITIQUE_LOW_EXAMPLE_JSON),
        (
            f"Strong report, score {_HIGH_EXAMPLE_SCORE}:",
            _CRITIQUE_HIGH_EXAMPLE_JSON,
        ),
    )


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
        (_CLAIM_EXTRACTION_REPLY_EXAMPLES, ClaimsDraft),
        (_CLAIM_VERIFICATION_REPLY_EXAMPLES, ClaimVerdictDraft),
        (_REPORT_REPLY_EXAMPLES, ReportDraft),
        (_critique_examples(), CritiqueDraft),
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
        # The six shared tables introduce an isolated example input, the
        # Critic's preserved pair labels its cases by band, and the Judge's
        # generated pair labels them weak and strong.
        lowered = label.lower()
        assert (
            "example input" in lowered
            or "report, score" in lowered
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
# second demonstrates the opposite semantic case.
PLANNED_OPERATION_INVENTORY = {
    "plan finalization": ("planner", 1),
    "finding extraction": ("researcher", 1),
    "source scoring": ("source_evaluator", 2),
    "claim extraction": ("fact_checker", 1),
    "claim verification": ("fact_checker", 2),
    "report construction": ("synthesizer", 1),
    "report review": ("critic", 2),
    "evaluation verdict": ("judge", 2),
}


def _labelled_examples(body: str) -> tuple[tuple[str, str], ...]:
    """The six shared tables: a label line, then ``Example JSON output:``."""
    lines = body.splitlines()
    return tuple(
        (lines[index - 1], lines[index + 1])
        for index, line in enumerate(lines)
        if line.strip() == "Example JSON output:"
    )


def _anchored_examples(*labels: str) -> Callable[[str], tuple[tuple[str, str], ...]]:
    """The Critic's and the Judge's pairs, which label their cases instead."""
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
    ),
    StructuredOperation(
        operation="finding extraction",
        agent="researcher",
        build_messages=_extraction_messages,
        schema=SubTopicFindingsDraft,
        examples=_labelled_examples,
    ),
    StructuredOperation(
        operation="source scoring",
        agent="source_evaluator",
        build_messages=_scoring_messages,
        schema=SourceScoresDraft,
        examples=_labelled_examples,
    ),
    StructuredOperation(
        operation="claim extraction",
        agent="fact_checker",
        build_messages=_claim_extraction_messages,
        schema=ClaimsDraft,
        examples=_labelled_examples,
    ),
    StructuredOperation(
        operation="claim verification",
        agent="fact_checker",
        build_messages=_claim_verification_messages,
        schema=ClaimVerdictDraft,
        examples=_labelled_examples,
    ),
    StructuredOperation(
        operation="report construction",
        agent="synthesizer",
        build_messages=_report_messages,
        schema=ReportDraft,
        examples=_labelled_examples,
    ),
    StructuredOperation(
        operation="report review",
        agent="critic",
        build_messages=_critique_messages,
        schema=CritiqueDraft,
        examples=_anchored_examples(
            f"Weak report, score {_LOW_EXAMPLE_SCORE}:",
            f"Strong report, score {_HIGH_EXAMPLE_SCORE}:",
        ),
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
)

OPERATIONS_BY_NAME = {operation.operation: operation for operation in OPERATIONS}


def _operation_id(operation: StructuredOperation) -> str:
    return operation.operation.replace(" ", "-")


def _examples_for(operation_name: str) -> tuple[tuple[str, str], ...]:
    operation = OPERATIONS_BY_NAME[operation_name]
    return operation.examples(operation.body())


def _request_envelope(body: str) -> str:
    """This request's own text, without any embedded fenced provider content.

    The Critic quotes the report under review inside a Markdown fence whose body
    is the report's own Markdown — headings included. Nothing inside that fence
    is part of the request, so the request's shape is asserted on the envelope,
    exactly as the Critic's own envelope tests already do. The other seven
    operations embed no provider content, so their envelope is their whole body.
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


@pytest.mark.parametrize("operation", OPERATIONS, ids=_operation_id)
def test_every_rendered_request_keeps_one_reply_contract(
    operation: StructuredOperation,
) -> None:
    """Step 2: one reply contract, last, unfenced, with no tool section."""
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


def test_the_only_markdown_fence_is_the_critic_report_delimiter() -> None:
    """The one fence in the matrix quotes the report, never the reply shape."""
    fenced = {
        operation.operation: [
            line for line in operation.body().splitlines() if line.startswith("```")
        ]
        for operation in OPERATIONS
        if "```" in operation.body()
    }

    assert fenced == {"report review": ["```report", "```"]}


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


def test_the_claim_verification_pair_is_schema_valid_and_opposite() -> None:
    """Step 3: the populated and the empty verdict shapes, both valid."""
    labelled = {
        label.split()[0].lower().rstrip(":"): payload
        for label, payload in _examples_for("claim verification")
    }

    assert set(labelled) == {"verified", "insufficient-evidence"}
    verified = ClaimVerdictDraft.model_validate_json(labelled["verified"])
    insufficient = ClaimVerdictDraft.model_validate_json(
        labelled["insufficient-evidence"]
    )

    assert verified.verdict == "verified"
    assert insufficient.verdict == "insufficient_evidence"
    assert verified.evidence and verified.contradictions == []
    assert insufficient.evidence == [] and insufficient.contradictions == []
    assert 0.0 <= insufficient.confidence < verified.confidence <= 1.0


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
