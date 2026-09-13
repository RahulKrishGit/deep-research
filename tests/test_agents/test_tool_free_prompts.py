"""The tool-free half of the prompt/transport congruence invariant.

Every separate structured call — plan, extraction, scoring, claim extraction,
claim verification, report, review — sends no tools. Its developer message must
therefore name none of the registered tools, and it must carry exactly one
output-shape sentence plus at most one or two bounded examples. These tests
build the real messages through the real builders; none of them copies a prompt
string.
"""

from __future__ import annotations

import re

import pytest

from deep_research.agents.critic import CritiqueTask, critique_messages
from deep_research.agents.fact_checker import (
    ClaimTask,
    claim_extraction_messages,
    claim_verification_messages,
)
from deep_research.agents.planner import plan_messages
from deep_research.agents.prompts import (
    STRUCTURED_EXAMPLE_NOTICE,
    STRUCTURED_REPLY_FORMAT,
    AgentTask,
)
from deep_research.agents.researcher import SubTopicTask, extraction_messages
from deep_research.agents.source_evaluator import (
    SourceEvaluationTask,
    scoring_messages,
)
from deep_research.agents.sources import SourceGroup
from deep_research.agents.steps import ReActRun
from deep_research.agents.synthesizer import SynthesisTask, report_messages
from deep_research.utils.types import (
    Claim,
    Finding,
    ResearchState,
    ScoredSource,
    SubTopic,
)

REGISTERED_TOOL_NAMES = {
    "web_search",
    "web_scraper",
    "document_reader",
    "query_memory",
    "save_to_memory",
    "write_document",
}

EXTRACTED_AT = "2026-08-01T12:00:00+00:00"


def _sub_topic(title: str = "Angle") -> SubTopic:
    return SubTopic(
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
        corroboration_score=0.5,
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
    assert body.count("```") == 0 or "# Reply format" in body


# --- the example tables themselves -------------------------------------------

def _example_tables() -> tuple:
    from deep_research.agents.critic import (
        _CRITIQUE_HIGH_EXAMPLE_JSON,
        _CRITIQUE_LOW_EXAMPLE_JSON,
        CritiqueDraft,
    )
    from deep_research.agents.fact_checker import (
        _CLAIM_EXTRACTION_REPLY_EXAMPLES,
        _CLAIM_VERIFICATION_REPLY_EXAMPLES,
        ClaimsDraft,
        ClaimVerdictDraft,
    )
    from deep_research.agents.planner import _PLAN_REPLY_EXAMPLES, ResearchPlanDraft
    from deep_research.agents.researcher import (
        _FINDING_REPLY_EXAMPLES,
        SubTopicFindingsDraft,
    )
    from deep_research.agents.source_evaluator import (
        _SOURCE_SCORE_REPLY_EXAMPLES,
        SourceScoresDraft,
    )
    from deep_research.agents.synthesizer import _REPORT_REPLY_EXAMPLES, ReportDraft

    return (
        (_PLAN_REPLY_EXAMPLES, ResearchPlanDraft),
        (_FINDING_REPLY_EXAMPLES, SubTopicFindingsDraft),
        (_SOURCE_SCORE_REPLY_EXAMPLES, SourceScoresDraft),
        (_CLAIM_EXTRACTION_REPLY_EXAMPLES, ClaimsDraft),
        (_CLAIM_VERIFICATION_REPLY_EXAMPLES, ClaimVerdictDraft),
        (_REPORT_REPLY_EXAMPLES, ReportDraft),
        (
            (
                ("Weak report, score 3:", _CRITIQUE_LOW_EXAMPLE_JSON),
                ("Strong report, score 9:", _CRITIQUE_HIGH_EXAMPLE_JSON),
                # labels checked below; the Critic pair keeps its own wording
            ),
            CritiqueDraft,
        ),
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
        # The six new tables introduce an isolated example input; the Critic's
        # preserved pair labels its two cases by band instead.
        lowered = label.lower()
        assert "example input" in lowered or "report, score" in lowered
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
