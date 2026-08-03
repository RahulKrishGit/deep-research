"""Tests for the Synthesizer's contracts, limitations, and composition."""

from __future__ import annotations

from pathlib import Path

import pytest

from deep_research.agents.errors import AgentConfigurationError
from deep_research.agents.prompts import AgentTask
from deep_research.agents.report import REPORT_SECTIONS, ReportSection
from deep_research.agents.steps import ReActRun
from deep_research.agents.synthesizer import (
    DEFAULT_MEMORY_CONFIDENCE,
    REPORT_SUMMARY_FALLBACK,
    ReportDraft,
    ReportSectionDraft,
    SynthesisTask,
    SynthesizedReport,
    SynthesizerAgent,
    build_report_sections,
    compose_report,
    high_confidence_claims,
    limitation_reasons,
    memory_payload,
    render_revision_guidance,
    report_filename,
    report_messages,
)
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import Tracker
from deep_research.providers import OpenAIProviderError
from deep_research.tools.base import BaseTool
from deep_research.utils.config import AgentRuntimeConfig
from deep_research.utils.types import (
    Claim,
    Critique,
    Finding,
    ResearchError,
    ResearchState,
    ScoredSource,
)
from tests.agent_fakes import ScriptedCompleter
from tests.research_fakes import FakeMemory, synthesizer_tools

SYNTH_EXTRACTED_AT = "2026-08-01T12:00:00+00:00"
SOURCE_URL = "https://example.org/a"


def _finding(url: str = SOURCE_URL, sub_topic: str = "Alpha") -> Finding:
    return Finding(
        content="Logical error rates fell below break-even.",
        source_url=url,
        source_title="QEC 2025",
        extracted_at=SYNTH_EXTRACTED_AT,
        confidence=0.8,
        related_sub_topic=sub_topic,
    )


def _source(
    *,
    url: str = SOURCE_URL,
    overall: float = 0.76,
    low_confidence: bool = False,
) -> ScoredSource:
    return ScoredSource(
        url=url,
        title="QEC 2025",
        authority_score=0.8,
        recency_score=0.7,
        relevance_score=0.9,
        corroboration_score=0.5,
        overall_score=overall,
        rationale="Peer-reviewed and corroborated.",
        low_confidence=low_confidence,
    )


def _claim(
    *,
    text: str = "Logical error rates fell below break-even in 2025.",
    verdict: str = "verified",
    confidence: float = 0.8,
    urls: list[str] | None = None,
) -> Claim:
    return Claim(
        text=text,
        source_urls=urls or [SOURCE_URL],
        verdict=verdict,
        confidence=confidence,
        evidence=[],
        contradictions=[],
    )


def _state(**overrides: object) -> ResearchState:
    payload: dict[str, object] = {
        "session_id": "session-1",
        "original_question": "How mature is quantum error correction?",
        "raw_findings": [_finding()],
        "evaluated_sources": [_source()],
        "verified_claims": [_claim()],
    }
    payload.update(overrides)
    return ResearchState.model_validate(payload)


def _task(**overrides: object) -> SynthesisTask:
    payload: dict[str, object] = {
        "instruction": "How mature is quantum error correction?",
        "session_id": "session-1",
        "iteration": 0,
        "claims": [_claim()],
        "sources": [_source()],
        "findings": [_finding()],
        "limitations": [],
    }
    payload.update(overrides)
    return SynthesisTask.model_validate(payload)


def test_a_clean_pass_records_no_limitations() -> None:
    assert limitation_reasons(_state()) == []


def test_every_weak_signal_becomes_an_enumerated_limitation() -> None:
    state = _state(
        errors=[
            ResearchError(
                error_type="researcher_sub_topic_without_findings",
                source="agent.researcher",
                message="A high-priority sub-topic produced no findings.",
            )
        ],
        iteration=3,
        max_iterations=3,
        evaluated_sources=[_source(overall=0.1, low_confidence=True)],
        verified_claims=[_claim(verdict="contradicted", confidence=0.4)],
    )

    assert limitation_reasons(state) == [
        "errors_recorded",
        "max_iterations_reached",
        "low_confidence_sources",
        "no_verified_claims",
        "contradicted_claims",
    ]


def test_an_unscored_pass_reports_that_source_quality_is_unknown() -> None:
    reasons = limitation_reasons(_state(evaluated_sources=[]))

    assert "no_sources_evaluated" in reasons
    assert "low_confidence_sources" not in reasons


@pytest.mark.parametrize(
    ("session_id", "iteration", "expected"),
    [
        ("session-1", 0, "report-session-1-0.md"),
        ("Session_42", 2, "report-session-42-2.md"),
        ("../../etc/passwd", 1, "report-etc-passwd-1.md"),
        ("   ", 0, "report-session-0.md"),
    ],
)
def test_report_filenames_are_slugged_and_traversal_free(
    session_id: str, iteration: int, expected: str
) -> None:
    assert report_filename(session_id=session_id, iteration=iteration) == expected


def test_report_filename_rejects_a_negative_iteration() -> None:
    with pytest.raises(ValueError, match="iteration"):
        report_filename(session_id="session-1", iteration=-1)


def test_sections_keep_only_source_urls_that_reached_the_evidence() -> None:
    sections, rejected = build_report_sections(
        ReportDraft(
            executive_summary="Break-even was reached.",
            sections=[
                ReportSectionDraft(
                    title="  Error correction  ",
                    body="Break-even was reached.\n\nScaling is open.",
                    source_urls=[
                        "https://WWW.example.org/a/",
                        "https://invented.test/x",
                        SOURCE_URL,
                    ],
                )
            ],
            uncertainty_notes="",
        ),
        known_urls=[SOURCE_URL],
        max_sections=4,
    )

    assert [section.title for section in sections] == ["Error correction"]
    assert sections[0].source_urls == [SOURCE_URL]
    assert "\n\n" in sections[0].body
    assert rejected == ["section 1: 1 source url(s) not in evidence"]


def test_a_blank_section_is_dropped_and_named() -> None:
    sections, rejected = build_report_sections(
        ReportDraft(
            executive_summary="",
            sections=[ReportSectionDraft(title="  ", body="Text.", source_urls=[])],
            uncertainty_notes="",
        ),
        known_urls=[SOURCE_URL],
        max_sections=4,
    )

    assert sections == []
    assert rejected == ["section 1: blank title or body"]


def test_sections_past_the_cap_are_dropped_and_named() -> None:
    draft = ReportDraft(
        executive_summary="",
        sections=[
            ReportSectionDraft(title=f"S{index}", body="Text.", source_urls=[])
            for index in range(3)
        ],
        uncertainty_notes="",
    )

    sections, rejected = build_report_sections(
        draft, known_urls=[SOURCE_URL], max_sections=2
    )

    assert [section.title for section in sections] == ["S0", "S1"]
    assert rejected == ["section 3: past the section cap"]


def test_build_report_sections_rejects_a_zero_cap() -> None:
    with pytest.raises(ValueError, match="max_sections"):
        build_report_sections(
            ReportDraft(executive_summary="", sections=[], uncertainty_notes=""),
            known_urls=[],
            max_sections=0,
        )


def test_only_confident_verified_claims_are_kept_for_memory() -> None:
    claims = [
        _claim(confidence=0.9),
        _claim(text="Weakly verified.", confidence=0.5),
        _claim(text="Unverified.", verdict="unverified", confidence=0.9),
    ]

    kept = high_confidence_claims(claims, threshold=DEFAULT_MEMORY_CONFIDENCE)

    assert [claim.confidence for claim in kept] == [0.9]
    assert kept[0].verdict == "verified"


def test_a_memory_payload_carries_the_claim_and_its_attribution() -> None:
    content, metadata = memory_payload(_claim(), session_id="session-1")

    assert content == "Logical error rates fell below break-even in 2025."
    assert metadata["entry_type"] == "finding"
    assert metadata["session_id"] == "session-1"
    assert metadata["agent_id"] == "synthesizer"
    assert metadata["source_url"] == SOURCE_URL
    assert metadata["confidence"] == pytest.approx(0.8)


def test_revision_guidance_repeats_the_critic_feedback() -> None:
    state = _state(
        critique=Critique(
            score=4,
            gaps=["No cost data."],
            unsupported_claims=["Costs fell tenfold."],
            recommended_queries=["qec cost 2025"],
            should_continue=True,
            rationale="Thin sourcing.",
        )
    )

    guidance = render_revision_guidance(state)

    assert "No cost data." in guidance
    assert "Costs fell tenfold." in guidance
    # Recommended queries are the Researcher's business, not the writer's.
    assert "qec cost 2025" not in guidance
    assert render_revision_guidance(_state()) == ""


def test_a_composed_report_carries_its_counts_and_every_section() -> None:
    report = compose_report(
        _task(),
        summary="Break-even was reached.",
        sections=[
            ReportSection(
                title="Error correction",
                body="Break-even was reached.",
                source_urls=[SOURCE_URL],
            )
        ],
        uncertainty_notes="Vendor numbers remain unaudited.",
        limitations=["errors_recorded"],
    )

    for heading in REPORT_SECTIONS:
        assert heading in report.markdown
    assert report.section_count == 1
    assert report.citation_count == 1
    assert report.source_count == 1
    assert report.path is None
    assert report.saved_findings == 0


def test_a_report_composed_without_a_model_still_cites_its_claims() -> None:
    report = compose_report(
        _task(),
        summary=REPORT_SUMMARY_FALLBACK,
        sections=[],
        uncertainty_notes="",
        limitations=["report_generation_failed"],
    )

    assert REPORT_SUMMARY_FALLBACK in report.markdown
    assert "(no findings were reported)" in report.markdown
    assert "[1] (confidence 0.80)" in report.markdown
    assert "The model provider failed while this report was written" in (
        report.markdown
    )


def test_report_messages_carry_every_input_the_writer_needs() -> None:
    messages = report_messages(
        _task(guidance="Close the cost gap.", limitations=["errors_recorded"]),
        finding_digest=10,
        claim_digest=10,
    )

    assert [message.role for message in messages] == ["developer", "user"]
    body = messages[1].content
    assert "## Research question" in body
    assert "## Context" in body
    assert "Close the cost gap." in body
    assert "## Verified and checked claims" in body
    assert "[verified 0.80]" in body
    assert "## Retrieved findings" in body
    assert "## Source quality" in body
    assert "## Known limitations" in body
    assert "## Response contract" in body


def test_report_messages_drop_the_context_section_without_guidance() -> None:
    body = report_messages(_task(), finding_digest=10, claim_digest=10)[1].content

    assert "## Context" not in body


def _synthesizer(
    tracker: Tracker,
    completer: ScriptedCompleter,
    tools: list[BaseTool],
    **overrides: object,
) -> SynthesizerAgent:
    return SynthesizerAgent(
        provider=completer,
        tracker=tracker,
        scratchpad=ScratchpadMemory(
            session_id="session-1",
            agent_name="synthesizer",
            max_entries=20,
        ),
        tools=tools,
        config=AgentRuntimeConfig(max_iterations=2, tool_budget=0),
        **overrides,
    )


def _draft(
    *,
    summary: str = "Break-even was reached in 2025.",
    urls: list[str] | None = None,
    notes: str = "Vendor numbers remain unaudited.",
) -> ReportDraft:
    return ReportDraft(
        executive_summary=summary,
        sections=[
            ReportSectionDraft(
                title="Error correction",
                body="Break-even was reached.",
                source_urls=urls if urls is not None else [SOURCE_URL],
            )
        ],
        uncertainty_notes=notes,
    )


def test_build_task_carries_the_evidence_limitations_and_revision_notes(
    tracker: Tracker, tmp_path: Path
) -> None:
    agent = _synthesizer(
        tracker,
        ScriptedCompleter(),
        synthesizer_tools(tracker, output_root=tmp_path),
    )
    state = _state(
        evaluated_sources=[_source(overall=0.1, low_confidence=True)],
        critique=Critique(
            score=4,
            gaps=["No cost data."],
            unsupported_claims=[],
            recommended_queries=[],
            should_continue=True,
            rationale="Thin sourcing.",
        ),
    )

    task = agent.build_task(state)

    assert task.instruction == state.original_question
    assert task.session_id == "session-1"
    assert task.iteration == 0
    assert [claim.text for claim in task.claims] == [
        "Logical error rates fell below break-even in 2025."
    ]
    assert task.limitations == ["low_confidence_sources"]
    assert "No cost data." in task.guidance


@pytest.mark.asyncio
async def test_a_run_writes_the_report_and_records_its_counts(
    tracker: Tracker, tmp_path: Path
) -> None:
    memory = FakeMemory()
    agent = _synthesizer(
        tracker,
        ScriptedCompleter(outputs=[_draft()]),
        synthesizer_tools(tracker, output_root=tmp_path, memory=memory),
    )

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(_state())

    assert outcome.result is not None
    assert outcome.result.path == "report-session-1-0.md"
    assert (tmp_path / "report-session-1-0.md").read_text(encoding="utf-8") == (
        outcome.result.markdown
    )
    assert outcome.state_update["report"] == outcome.result.markdown
    assert "## Executive summary" in outcome.result.markdown
    assert "Break-even was reached in 2025." in outcome.result.markdown
    assert "Vendor numbers remain unaudited." in outcome.result.markdown
    assert outcome.react.stop_reason == "finished"
    assert outcome.errors == []
    # One high-confidence verified claim was kept for future sessions.
    assert [content for content, _ in memory.saved] == [
        "Logical error rates fell below break-even in 2025."
    ]
    assert outcome.result.saved_findings == 1


@pytest.mark.asyncio
async def test_a_run_emits_the_counts_the_spec_requires(
    tracker: Tracker, tmp_path: Path
) -> None:
    agent = _synthesizer(
        tracker,
        ScriptedCompleter(outputs=[_draft()]),
        synthesizer_tools(tracker, output_root=tmp_path),
    )

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(_state())

    events = outcome.state_update["events"]
    assert [event.event_type for event in events] == [
        "synthesizer.synthesis.started",
        "synthesizer.synthesis.completed",
    ]
    completed = events[-1].metadata
    assert completed["section_count"] == 1
    assert completed["citation_count"] == 1
    assert completed["source_appendix_count"] == 1
    assert completed["output_path"] == "report-session-1-0.md"
    assert completed["saved_findings"] == 1
    assert completed["limitations"] == []


@pytest.mark.asyncio
async def test_an_invented_section_url_is_dropped_and_recorded(
    tracker: Tracker, tmp_path: Path
) -> None:
    agent = _synthesizer(
        tracker,
        ScriptedCompleter(outputs=[_draft(urls=["https://invented.test/x"])]),
        synthesizer_tools(tracker, output_root=tmp_path),
    )

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(_state())

    assert outcome.result is not None
    assert "https://invented.test/x" not in outcome.result.markdown
    assert "Sources: none cited" in outcome.result.markdown
    assert [error.error_type for error in outcome.errors] == [
        "synthesizer_invalid_section"
    ]
    assert outcome.errors[0].recoverable is True


@pytest.mark.asyncio
async def test_a_provider_failure_still_produces_a_cited_report(
    tracker: Tracker, tmp_path: Path
) -> None:
    agent = _synthesizer(
        tracker,
        ScriptedCompleter(outputs=[OpenAIProviderError("down")]),
        synthesizer_tools(tracker, output_root=tmp_path),
    )

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(_state())

    assert outcome.result is not None
    assert REPORT_SUMMARY_FALLBACK in outcome.result.markdown
    assert "[1] (confidence 0.80)" in outcome.result.markdown
    assert "The model provider failed while this report was written" in (
        outcome.result.markdown
    )
    assert outcome.react.stop_reason == "provider_error"
    errors = {error.error_type: error for error in outcome.errors}
    assert errors["synthesizer_report_provider_error"].recoverable is False
    assert errors["synthesizer_report_provider_error"].details == {
        "exception_type": "OpenAIProviderError"
    }
    assert (tmp_path / "report-session-1-0.md").is_file()


@pytest.mark.asyncio
async def test_no_evidence_skips_the_provider_and_says_so(
    tracker: Tracker, tmp_path: Path
) -> None:
    completer = ScriptedCompleter()
    agent = _synthesizer(
        tracker, completer, synthesizer_tools(tracker, output_root=tmp_path)
    )
    state = _state(raw_findings=[], evaluated_sources=[], verified_claims=[])

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(state)

    assert completer.calls == []
    assert outcome.result is not None
    assert "(no claim reached a verified verdict)" in outcome.result.markdown
    assert "no_evidence" in {
        error.error_type.removeprefix("synthesizer_") for error in outcome.errors
    }
    assert "No source behind these findings was scored" in outcome.result.markdown


@pytest.mark.asyncio
async def test_a_failed_write_keeps_the_report_in_state(
    tracker: Tracker, tmp_path: Path
) -> None:
    # A directory where the report file must go makes the real tool fail.
    (tmp_path / "report-session-1-0.md").mkdir()
    agent = _synthesizer(
        tracker,
        ScriptedCompleter(outputs=[_draft()]),
        synthesizer_tools(tracker, output_root=tmp_path),
    )

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(_state())

    assert outcome.result is not None
    assert outcome.result.path is None
    assert outcome.state_update["report"] == outcome.result.markdown
    assert [error.error_type for error in outcome.errors] == [
        "synthesizer_report_not_written"
    ]
    assert outcome.errors[0].details["reason"] == "tool_failed"


@pytest.mark.asyncio
async def test_a_failed_memory_write_never_blocks_the_report(
    tracker: Tracker, tmp_path: Path
) -> None:
    memory = FakeMemory(error=RuntimeError("memory down"))
    agent = _synthesizer(
        tracker,
        ScriptedCompleter(outputs=[_draft()]),
        synthesizer_tools(tracker, output_root=tmp_path, memory=memory),
    )

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(_state())

    assert outcome.result is not None
    assert outcome.result.path == "report-session-1-0.md"
    assert outcome.result.saved_findings == 0
    error = next(
        error
        for error in outcome.errors
        if error.error_type == "synthesizer_memory_save_failed"
    )
    assert error.recoverable is True
    assert error.details == {"failures": 1, "attempted": 1}


@pytest.mark.asyncio
async def test_only_capped_high_confidence_claims_reach_memory(
    tracker: Tracker, tmp_path: Path
) -> None:
    memory = FakeMemory()
    agent = _synthesizer(
        tracker,
        ScriptedCompleter(outputs=[_draft()]),
        synthesizer_tools(tracker, output_root=tmp_path, memory=memory),
        max_memory_findings=1,
    )
    state = _state(
        verified_claims=[
            _claim(text="First.", confidence=0.9),
            _claim(text="Second.", confidence=0.9),
            _claim(text="Weak.", confidence=0.2),
        ]
    )

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(state)

    assert [content for content, _ in memory.saved] == ["First."]
    assert outcome.result is not None
    assert outcome.result.saved_findings == 1


@pytest.mark.asyncio
async def test_finalize_requires_a_synthesis_task(
    tracker: Tracker, tmp_path: Path
) -> None:
    agent = _synthesizer(
        tracker,
        ScriptedCompleter(outputs=[_draft()]),
        synthesizer_tools(tracker, output_root=tmp_path),
    )

    with pytest.raises(AgentConfigurationError, match="SynthesisTask"):
        await agent.finalize(
            AgentTask(instruction="anything"),
            ReActRun(agent_name="synthesizer", stop_reason="finished"),
        )


def test_the_synthesizer_declares_its_two_writes(
    tracker: Tracker, tmp_path: Path
) -> None:
    agent = _synthesizer(
        tracker,
        ScriptedCompleter(),
        synthesizer_tools(tracker, output_root=tmp_path),
    )

    assert SynthesizerAgent.name == "synthesizer"
    assert SynthesizerAgent.allowed_tools == ("write_document", "save_to_memory")
    assert agent.output_schema is SynthesizedReport
