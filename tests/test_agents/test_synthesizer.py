"""Tests for the Synthesizer's contract, composition, and refusal rules.

The Synthesizer returns claim-linked points and composes two Markdown
artifacts. It writes neither of them: publication and long-term memory belong
to the terminal finalizer, so every test here also pins that a run touches no
tool, no file, and no memory entry.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from deep_research.agents.errors import AgentConfigurationError
from deep_research.agents.identity import claim_fingerprint
from deep_research.agents.prompts import (
    AgentTask,
    render_finding_digest,
    render_report_claim_packet,
)
from deep_research.agents.report import (
    QUALITY_STATUS_NOT_GATED,
    REPORT_SECTIONS,
    REPORT_SUMMARY_FALLBACK,
    ReportPoint,
    ReportSection,
)
from deep_research.agents.steps import ReActRun
from deep_research.agents.synthesizer import (
    DEFAULT_MEMORY_CONFIDENCE,
    SYNTHESIS_OPEN_QUESTIONS_CHARS,
    ConstraintDraft,
    ReportDraft,
    ReportPointDraft,
    ReportSectionDraft,
    SynthesisTask,
    SynthesizedReport,
    SynthesizerAgent,
    bounded_claim_packet,
    bounded_finding_digest,
    build_report_composition,
    claim_label,
    claim_registry,
    compose_report,
    evidence_report_filename,
    high_confidence_claims,
    limitation_reasons,
    memory_payload,
    ordered_claims_for_report,
    render_revision_guidance,
    report_filename,
    report_messages,
)
from deep_research.evaluation.cases import cases_for
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import TokenUsage, Tracker
from deep_research.providers import (
    ProviderOutputLimitError,
    ProviderResponseTelemetry,
)
from deep_research.tools.base import BaseTool
from deep_research.utils.config import AgentRuntimeConfig
from deep_research.utils.types import (
    Claim,
    Critique,
    Finding,
    ResearchError,
    ResearchState,
    ScoredSource,
    SubTopic,
)
from tests.agent_fakes import ScriptedCompleter
from tests.research_fakes import FakeMemory, synthesizer_tools

SYNTH_EXTRACTED_AT = "2026-08-01T12:00:00+00:00"
SOURCE_URL = "https://example.org/a"
OTHER_URL = "https://other.test/b"


def _output_limit_error() -> ProviderOutputLimitError:
    return ProviderOutputLimitError(
        ProviderResponseTelemetry(
            finish_reason_category="length",
            configured_max_tokens=4096,
            usage=TokenUsage(input_tokens=5, output_tokens=4096),
            request_attempt=1,
        )
    )


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
    coverage_ids: list[str] | None = None,
    finding_fingerprints: list[str] | None = None,
) -> Claim:
    return Claim(
        claim_id=claim_fingerprint(text),
        text=text,
        source_urls=urls or [SOURCE_URL],
        verdict=verdict,
        confidence=confidence,
        evidence=[],
        contradictions=[],
        verification_evidence=[],
        consumed_finding_fingerprints=finding_fingerprints or [],
        consumed_coverage_ids=coverage_ids or [],
    )


def _sub_topic(title: str = "Alpha") -> SubTopic:
    return SubTopic(
        coverage_id="topic-01",
        title=title,
        rationale="The first thing to establish.",
        search_queries=["alpha evidence"],
        success_criteria=["a measured alpha result"],
        priority=1,
    )


def _state(**overrides: object) -> ResearchState:
    payload: dict[str, object] = {
        "session_id": "session-1",
        "original_question": "How mature is quantum error correction?",
        "sub_topics": [_sub_topic()],
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
        "max_iterations": 3,
        "as_of": SYNTH_EXTRACTED_AT,
        "scope": "1 planned sub-topic.",
        "claims": [_claim()],
        "sources": [_source()],
        "findings": [_finding()],
        "limitations": [],
        "errors": [],
    }
    payload.update(overrides)
    return SynthesisTask.model_validate(payload)


def _point_draft(
    text: str = "Break-even was reached.",
    *,
    claim_ids: list[str] | None = None,
    source_urls: list[str] | None = None,
) -> ReportPointDraft:
    return ReportPointDraft(
        text=text,
        claim_ids=claim_ids if claim_ids is not None else ["C001"],
        source_urls=source_urls if source_urls is not None else [SOURCE_URL],
    )


def _draft(
    *,
    summary: str = "Break-even was reached in 2025.",
    urls: list[str] | None = None,
    notes: list[str] | None = None,
) -> ReportDraft:
    cited = urls if urls is not None else [SOURCE_URL]
    return ReportDraft(
        executive_summary=[_point_draft(summary, source_urls=cited)],
        ranked_constraints=[
            ConstraintDraft(
                constraint="Charge for driving inside the measured zone.",
                deployment_mechanism="area licence with camera enforcement",
                geography="not stated",
                claim_ids=["C001"],
                source_urls=cited,
            )
        ],
        sections=[
            ReportSectionDraft(
                title="Error correction",
                points=[
                    _point_draft(
                        "Break-even was reached.", source_urls=cited
                    )
                ],
            )
        ],
        uncertainty_notes=(
            ["Vendor numbers remain unaudited."] if notes is None else notes
        ),
    )


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


# --- limitations and filenames -------------------------------------------------


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


def test_the_evidence_filename_derives_from_the_reader_report() -> None:
    assert (
        evidence_report_filename(session_id="Session_42", iteration=2)
        == "report-session-42-2-evidence.md"
    )
    with pytest.raises(ValueError, match="iteration"):
        evidence_report_filename(session_id="session-1", iteration=-1)


# --- the checked-claim packet -------------------------------------------------


def test_claims_are_labelled_by_their_canonical_position() -> None:
    claims = [
        _claim(),
        _claim(text="Cost fell tenfold.", urls=[OTHER_URL]),
    ]

    registry = claim_registry(claims)

    assert [label for label, _ in registry] == ["C001", "C002"]
    assert [claim.claim_id for _, claim in registry] == [
        claims[0].claim_id,
        claims[1].claim_id,
    ]
    with pytest.raises(ValueError, match="positions"):
        claim_label(0)


def test_claims_are_ranked_by_coverage_then_verdict_then_impact() -> None:
    covered = _claim(text="Covered.", coverage_ids=["topic-01", "topic-02"])
    high_impact = _claim(
        text="High impact.",
        confidence=0.1,
        finding_fingerprints=["finding-1", "finding-2"],
    )
    low_impact = _claim(
        text="Low impact.",
        confidence=0.9,
        finding_fingerprints=["finding-3"],
    )
    contradicted = _claim(
        text="Contradicted.", verdict="contradicted", confidence=0.9
    )

    # Coverage first, then verdict, then recorded evidence impact: confidence
    # must not move a less load-bearing claim ahead of a more load-bearing one.
    assert [claim.text for claim in ordered_claims_for_report(
        [low_impact, high_impact, contradicted, covered]
    )] == ["Covered.", "High impact.", "Low impact.", "Contradicted."]


def test_a_claim_repeated_in_state_is_ranked_once() -> None:
    claim = _claim()
    ranked = ordered_claims_for_report([claim, claim, _claim(text="Other.")])

    assert [entry.text for entry in ranked] == [claim.text, "Other."]


def test_the_packet_keeps_the_ranked_head_and_counts_the_omission() -> None:
    claims = [
        _claim(text="First.", coverage_ids=["topic-01"]),
        _claim(text="Second."),
        _claim(text="Third."),
    ]

    packet, omitted = bounded_claim_packet(
        claim_registry(claims), limit=2, budget_chars=10_000
    )

    assert [claim.text for _, claim in packet] == ["First.", "Second."]
    assert omitted == 1


def test_the_packet_honours_a_character_budget() -> None:
    claims = [_claim(text="x" * 400), _claim(text="y" * 400)]

    packet, omitted = bounded_claim_packet(
        claim_registry(claims), limit=10, budget_chars=100
    )

    assert packet == []
    assert omitted == 2
    assert len(render_report_claim_packet(packet, omitted=omitted)) <= 100


def test_the_packet_rejects_a_budget_below_its_empty_fallback() -> None:
    with pytest.raises(ValueError, match="budget_chars"):
        bounded_claim_packet([], limit=1, budget_chars=1)


def test_the_packet_accepts_the_omission_fallback_boundary() -> None:
    packet, omitted = bounded_claim_packet(
        claim_registry([_claim()]), limit=1, budget_chars=89
    )

    assert packet == []
    assert omitted == 1
    assert len(render_report_claim_packet(packet, omitted=omitted)) == 89


def test_the_prompt_omitted_claim_is_not_a_validation_allow_list() -> None:
    task = _task(
        claims=[
            _claim(),
            _claim(text="Cost fell tenfold.", urls=[OTHER_URL]),
        ]
    )
    report_messages(task, finding_digest=10, claim_digest=1)

    composition, rejected = build_report_composition(
        task,
        ReportDraft(
            executive_summary=[
                _point_draft(
                    "Cost fell tenfold.",
                    claim_ids=["C002"],
                    source_urls=[OTHER_URL],
                )
            ],
            ranked_constraints=[],
            sections=[],
            uncertainty_notes=[],
        ),
        max_sections=4,
        limitations=[],
    )

    assert composition.summary == []
    assert rejected == ["executive summary point 1: no known checked claim"]
    # The full canonical snapshot still feeds the evidence ledger.
    assert len(composition.claims) == 2


def test_open_questions_use_a_deterministic_rendered_character_bound() -> None:
    task = _task(
        findings=[
            _finding(url=f"https://example.test/finding-{index:03d}")
            for index in range(100)
        ]
    )

    body = report_messages(task, finding_digest=100, claim_digest=10)[1].content
    open_questions = body.split(
        "# Retrieved findings (open questions only)\n", 1
    )[1].split("\n\n# Source quality", 1)[0]

    assert len(open_questions) <= SYNTHESIS_OPEN_QUESTIONS_CHARS
    assert open_questions.startswith("1. [Alpha] ")


def test_open_questions_reject_a_budget_below_the_empty_fallback() -> None:
    with pytest.raises(ValueError, match="budget_chars"):
        bounded_finding_digest([], limit=1, budget_chars=1)


def test_open_questions_accept_the_empty_fallback_boundary() -> None:
    digest = bounded_finding_digest([], limit=1, budget_chars=13)

    assert digest == "(no findings)"
    assert len(digest) == 13
    assert len(render_finding_digest([])) == 13


def test_the_packet_bounds_reject_a_zero() -> None:
    with pytest.raises(ValueError, match="limit"):
        bounded_claim_packet(claim_registry([_claim()]), limit=0, budget_chars=10)
    with pytest.raises(ValueError, match="budget_chars"):
        bounded_claim_packet(claim_registry([_claim()]), limit=1, budget_chars=0)


def test_only_confident_verified_claims_are_kept_for_memory() -> None:
    claims = [
        _claim(confidence=0.9),
        _claim(text="Weakly verified.", confidence=0.5),
        _claim(text="Unverified.", verdict="unverified", confidence=0.9),
    ]

    kept = high_confidence_claims(claims, threshold=DEFAULT_MEMORY_CONFIDENCE)

    assert [claim.confidence for claim in kept] == [0.9]
    assert kept[0].verdict == "verified"
    assert high_confidence_claims(claims, limit=0) == []


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


# --- claim-linked validation --------------------------------------------------


def test_a_settled_point_needs_a_known_checked_claim() -> None:
    composition, rejected = build_report_composition(
        _task(),
        ReportDraft(
            executive_summary=[_point_draft(claim_ids=["C999"])],
            ranked_constraints=[],
            sections=[],
            uncertainty_notes=[],
        ),
        max_sections=4,
        limitations=[],
    )

    assert composition.summary == []
    assert rejected == ["executive summary point 1: no known checked claim"]


def test_a_point_citing_a_url_its_claims_do_not_carry_is_refused() -> None:
    composition, rejected = build_report_composition(
        _task(),
        ReportDraft(
            executive_summary=[_point_draft(source_urls=["https://invented.test/x"])],
            ranked_constraints=[],
            sections=[],
            uncertainty_notes=[],
        ),
        max_sections=4,
        limitations=[],
    )

    assert composition.summary == []
    assert rejected == [
        "executive summary point 1: 1 source url(s) not on those claims"
    ]


def test_an_unknown_label_is_counted_and_never_silently_accepted() -> None:
    composition, rejected = build_report_composition(
        _task(),
        ReportDraft(
            executive_summary=[_point_draft(claim_ids=["c001", "C404"])],
            ranked_constraints=[],
            sections=[],
            uncertainty_notes=[],
        ),
        max_sections=4,
        limitations=[],
    )

    # The known claim still carries the point; the invented label is named.
    assert composition.summary[0].claim_ids == [_claim().claim_id]
    assert rejected == [
        "executive summary point 1: 1 claim id(s) outside the registry"
    ]


def test_a_point_without_a_source_url_is_refused() -> None:
    composition, rejected = build_report_composition(
        _task(),
        ReportDraft(
            executive_summary=[_point_draft(source_urls=[])],
            ranked_constraints=[],
            sections=[],
            uncertainty_notes=[],
        ),
        max_sections=4,
        limitations=[],
    )

    assert composition.summary == []
    assert rejected == [
        "executive summary point 1: no source url for a settled statement"
    ]


def test_a_blank_point_is_refused_without_quoting_the_model() -> None:
    composition, rejected = build_report_composition(
        _task(),
        ReportDraft(
            executive_summary=[_point_draft("   ")],
            ranked_constraints=[],
            sections=[],
            uncertainty_notes=[],
        ),
        max_sections=4,
        limitations=[],
    )

    assert composition.summary == []
    assert rejected == ["executive summary point 1: blank statement"]


def test_a_repeated_point_is_refused() -> None:
    point = _point_draft()
    composition, rejected = build_report_composition(
        _task(),
        ReportDraft(
            executive_summary=[point, point],
            ranked_constraints=[],
            sections=[],
            uncertainty_notes=[],
        ),
        max_sections=4,
        limitations=[],
    )

    assert len(composition.summary) == 1
    assert rejected == [
        "executive summary point 2: repeats an earlier point"
    ]


def test_the_registry_holds_what_the_point_cited_and_nothing_else() -> None:
    composition, rejected = build_report_composition(
        _task(),
        _draft(),
        max_sections=4,
        limitations=[],
    )

    assert rejected == []
    assert composition.summary[0].claim_ids == [_claim().claim_id]
    assert composition.summary[0].source_urls == [SOURCE_URL]
    assert composition.constraints[0].claim_ids == [_claim().claim_id]
    assert composition.constraints[0].deployment_mechanism == (
        "area licence with camera enforcement"
    )


def test_a_section_past_the_cap_is_refused_and_named() -> None:
    draft = ReportDraft(
        executive_summary=[],
        ranked_constraints=[],
        sections=[
            ReportSectionDraft(
                title=f"S{index}", points=[_point_draft(f"Point {index}.")]
            )
            for index in range(3)
        ],
        uncertainty_notes=[],
    )

    composition, rejected = build_report_composition(
        _task(), draft, max_sections=1, limitations=[]
    )

    assert [section.title for section in composition.sections] == ["S0"]
    assert rejected == ["2 section(s) past the section cap"]


def test_a_blank_section_title_is_refused_and_named() -> None:
    draft = ReportDraft(
        executive_summary=[],
        ranked_constraints=[],
        sections=[ReportSectionDraft(title="   ", points=[_point_draft()])],
        uncertainty_notes=[],
    )

    composition, rejected = build_report_composition(
        _task(), draft, max_sections=4, limitations=[]
    )

    assert composition.sections == []
    assert rejected == ["section 1: blank title"]


def test_a_section_whose_points_all_fail_is_refused() -> None:
    draft = ReportDraft(
        executive_summary=[],
        ranked_constraints=[],
        sections=[
            ReportSectionDraft(
                title="Unsupported", points=[_point_draft(claim_ids=["C999"])]
            )
        ],
        uncertainty_notes=[],
    )

    composition, rejected = build_report_composition(
        _task(), draft, max_sections=4, limitations=[]
    )

    assert composition.sections == []
    assert rejected == [
        "section 1 point 1: no known checked claim",
        "section 1: no printable point",
    ]


def test_a_constraint_is_validated_like_any_other_point() -> None:
    draft = ReportDraft(
        executive_summary=[],
        ranked_constraints=[
            ConstraintDraft(
                constraint="Unsupported constraint.",
                deployment_mechanism="not stated",
                geography="not stated",
                claim_ids=["C999"],
                source_urls=[SOURCE_URL],
            )
        ],
        sections=[],
        uncertainty_notes=[],
    )

    composition, rejected = build_report_composition(
        _task(), draft, max_sections=4, limitations=[]
    )

    assert composition.constraints == []
    assert rejected == ["constraint 1: no known checked claim"]


def test_a_blank_constraint_cell_renders_as_not_stated() -> None:
    draft = ReportDraft(
        executive_summary=[],
        ranked_constraints=[
            ConstraintDraft(
                constraint="Supported constraint.",
                deployment_mechanism="   ",
                geography="",
                claim_ids=["C001"],
                source_urls=[SOURCE_URL],
            )
        ],
        sections=[],
        uncertainty_notes=[],
    )

    composition, rejected = build_report_composition(
        _task(), draft, max_sections=4, limitations=[]
    )

    assert rejected == []
    assert composition.constraints[0].deployment_mechanism == ""
    assert composition.constraints[0].geography == ""


def test_constraint_semantics_are_provider_attested_without_text_heuristics() -> None:
    """The typed contract has no field that can prove these cell values.

    Claim and source links are still validated, but mechanism/geography remain
    provider-attested prose until a future contract carries structured evidence
    for them. Arbitrary-looking values therefore must not be accepted as local
    provenance merely because they are nonblank, nor rejected by text guesses.
    """
    draft = ReportDraft(
        executive_summary=[],
        ranked_constraints=[
            ConstraintDraft(
                constraint="Supported constraint.",
                deployment_mechanism="invented mechanism with no typed support",
                geography="Atlantis",
                claim_ids=["C001"],
                source_urls=[SOURCE_URL],
            )
        ],
        sections=[],
        uncertainty_notes=[],
    )

    composition, rejected = build_report_composition(
        _task(), draft, max_sections=4, limitations=[]
    )

    assert rejected == []
    assert composition.constraints[0].deployment_mechanism == (
        "invented mechanism with no typed support"
    )
    assert composition.constraints[0].geography == "Atlantis"


def test_uncertainty_notes_may_carry_source_free_text() -> None:
    composition, rejected = build_report_composition(
        _task(),
        ReportDraft(
            executive_summary=[],
            ranked_constraints=[],
            sections=[],
            uncertainty_notes=["  Vendor numbers remain unaudited.  ", "   "],
        ),
        max_sections=4,
        limitations=[],
    )

    assert rejected == []
    assert composition.uncertainty_notes == ["Vendor numbers remain unaudited."]


def test_build_report_composition_rejects_a_zero_cap() -> None:
    with pytest.raises(ValueError, match="max_sections"):
        build_report_composition(
            _task(), None, max_sections=0, limitations=[]
        )


# --- composition --------------------------------------------------------------


def test_a_composed_report_carries_both_artifacts_and_its_counts() -> None:
    report, errors = compose_report(
        _task(), draft=_draft(), limitations=["errors_recorded"]
    )

    assert errors == []
    for heading in REPORT_SECTIONS:
        assert heading in report.markdown
    assert report.section_count == 1
    assert report.citation_count == 1
    assert report.unique_source_count == 1
    assert report.unique_claim_count == 1
    assert report.path is None
    assert report.evidence_path == "report-session-1-0-evidence.md"
    assert report.evidence_markdown.startswith("# Evidence ledger: ")
    assert SYNTH_EXTRACTED_AT in report.markdown
    assert "Vendor numbers remain unaudited." in report.markdown
    assert "saved_findings" not in SynthesizedReport.model_fields


def test_a_refused_point_is_named_in_the_ledger_and_recorded() -> None:
    report, errors = compose_report(
        _task(),
        draft=_draft(urls=["https://invented.test/x"]),
        limitations=[],
    )

    assert "https://invented.test/x" not in report.markdown
    assert "executive summary point 1: 1 source url(s) not on those claims" in (
        report.evidence_markdown
    )
    assert [error.error_type for error in errors] == [
        "synthesizer_invalid_draft"
    ]
    assert errors[0].recoverable is True


def test_a_report_composed_without_a_model_still_declares_itself() -> None:
    report, errors = compose_report(_task(), draft=None, limitations=[])

    assert errors == []
    assert REPORT_SUMMARY_FALLBACK in report.markdown
    assert "(no finding passed validation for this pass)" in report.markdown
    assert f"**Quality status:** {QUALITY_STATUS_NOT_GATED}" in report.markdown
    # The ledger still carries the checked-claim registry.
    assert "Logical error rates fell below break-even in 2025." in (
        report.evidence_markdown
    )


def test_an_unscored_source_is_reported_as_unscored_in_the_ledger() -> None:
    unscored = ScoredSource(
        url=OTHER_URL,
        title="Unscored study",
        rationale="The provider was unavailable, so this source carries no score.",
        evaluation_status="unscored_provider",
    )
    report, _ = compose_report(
        _task(sources=[_source(), unscored]),
        draft=_draft(),
        limitations=[],
    )

    assert "unscored_provider" in report.evidence_markdown
    assert "not scored" not in report.markdown


# --- the request the writer receives ------------------------------------------


def test_report_messages_carry_every_input_the_writer_needs() -> None:
    messages = report_messages(
        _task(guidance="Close the cost gap.", limitations=["errors_recorded"]),
        finding_digest=10,
        claim_digest=10,
    )

    assert [message.role for message in messages] == ["developer", "user"]
    body = messages[1].content
    assert "# Research question" in body
    assert "# Context" in body
    assert "Close the cost gap." in body
    assert "# As of and scope" in body
    assert SYNTH_EXTRACTED_AT in body
    assert "# Checked claims to cite" in body
    assert "C001 [verified 0.80]" in body
    assert "# Retrieved findings (open questions only)" in body
    assert "# Source quality" in body
    assert "# Known limitations" in body
    assert "# Response contract" in body
    assert body.count("JSON object") == 1


def test_report_messages_state_how_many_claims_were_omitted() -> None:
    task = _task(
        claims=[
            _claim(),
            _claim(text="Cost fell tenfold.", urls=[OTHER_URL]),
        ]
    )

    body = report_messages(task, finding_digest=10, claim_digest=1)[1].content

    assert "C001" in body
    assert "C002" not in body
    assert "1 further checked claim(s) were omitted" in body


def test_live_report_messages_expose_every_required_coverage_topic(
    tracker: Tracker, tmp_path: Path
) -> None:
    live_case = cases_for("synthesizer", "live")[0]
    agent = _synthesizer(
        tracker,
        ScriptedCompleter(),
        synthesizer_tools(tracker, output_root=tmp_path),
    )
    task = agent.build_task(live_case.state)

    body = report_messages(
        task,
        finding_digest=len(task.findings),
        claim_digest=len(task.claims),
    )[1].content

    required_topics = tuple(topic.title for topic in live_case.state.sub_topics)
    assert required_topics
    assert all(topic in body for topic in required_topics)
    assert task.findings == list(live_case.state.raw_findings)


def test_report_messages_drop_the_context_section_without_guidance() -> None:
    body = report_messages(_task(), finding_digest=10, claim_digest=10)[1].content

    assert "# Context" not in body


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
    assert task.max_iterations == state.max_iterations
    assert task.as_of == SYNTH_EXTRACTED_AT
    assert "topic-01 Alpha" in task.scope
    assert [topic.coverage_id for topic in task.sub_topics] == ["topic-01"]
    assert [claim.text for claim in task.claims] == [
        "Logical error rates fell below break-even in 2025."
    ]
    assert task.limitations == ["low_confidence_sources"]
    assert task.errors == []
    assert "No cost data." in task.guidance


# --- the run writes nothing ---------------------------------------------------


@pytest.mark.asyncio
async def test_a_run_composes_both_artifacts_and_writes_nothing(
    tracker: Tracker, tmp_path: Path
) -> None:
    memory = FakeMemory()
    completer = ScriptedCompleter(outputs=[_draft()])
    agent = _synthesizer(
        tracker,
        completer,
        synthesizer_tools(tracker, output_root=tmp_path, memory=memory),
    )

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(_state())

    assert outcome.result is not None
    # Composition, not publication.
    assert outcome.result.path is None
    assert outcome.state_update["report"] == outcome.result.markdown
    assert outcome.state_update["report_evidence"] == (
        outcome.result.evidence_markdown
    )
    assert outcome.state_update["evidence_path"] == "report-session-1-0-evidence.md"
    assert outcome.state_update["unique_source_count"] == 1
    assert outcome.state_update["unique_claim_count"] == 1
    # No tool ran, no file exists, no memory entry was saved.
    assert outcome.react.tool_calls == 0
    assert completer.react_calls == []
    assert memory.saved == []
    assert list(tmp_path.iterdir()) == []
    assert "output_path" not in outcome.state_update
    assert "## Executive summary" in outcome.result.markdown
    assert "Break-even was reached in 2025." in outcome.result.markdown
    assert "Vendor numbers remain unaudited." in outcome.result.markdown
    assert outcome.react.stop_reason == "finished"
    assert outcome.errors == []
    assert [call[0] for call in completer.calls] == ["ReportDraft"]


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
    assert completed["unique_source_count"] == 1
    assert completed["unique_claim_count"] == 1
    assert completed["evidence_path"] == "report-session-1-0-evidence.md"
    assert completed["report_chars"] == len(outcome.result.markdown)
    assert completed["evidence_chars"] == len(outcome.result.evidence_markdown)
    # A completion event may never advertise an artifact that does not exist.
    assert "output_path" not in completed
    assert "saved_findings" not in completed
    assert completed["limitations"] == []


@pytest.mark.asyncio
async def test_an_invented_section_url_is_refused_and_recorded(
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
    assert [error.error_type for error in outcome.errors] == [
        "synthesizer_invalid_draft"
    ]
    assert outcome.errors[0].recoverable is True
    assert outcome.errors[0].details["rejected"]


@pytest.mark.asyncio
async def test_a_provider_failure_still_produces_a_cited_report(
    tracker: Tracker, tmp_path: Path
) -> None:
    agent = _synthesizer(
        tracker,
        ScriptedCompleter(outputs=[_output_limit_error()]),
        synthesizer_tools(tracker, output_root=tmp_path),
    )

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(_state())

    assert outcome.result is not None
    assert REPORT_SUMMARY_FALLBACK in outcome.result.markdown
    assert "The model provider failed while this report was written" in (
        outcome.result.markdown
    )
    assert outcome.react.stop_reason == "provider_error"
    errors = {error.error_type: error for error in outcome.errors}
    assert errors["synthesizer_report_provider_error"].recoverable is False
    details = errors["synthesizer_report_provider_error"].details
    assert details["operation"] == "synthesizer_report_draft"
    provider = details["provider_failure"]
    assert provider["kind"] == "output_limit"
    assert provider["configured_max_tokens"] == 4096
    assert provider["request_attempt"] == 1
    # The ledger is still composed from the recorded evidence.
    assert "Logical error rates fell below break-even in 2025." in (
        outcome.result.evidence_markdown
    )
    assert list(tmp_path.iterdir()) == []


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
    assert REPORT_SUMMARY_FALLBACK in outcome.result.markdown
    assert "no_evidence" in {
        error.error_type.removeprefix("synthesizer_") for error in outcome.errors
    }
    assert "No source behind these findings was scored" in outcome.result.markdown


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


def test_the_synthesizer_declares_the_publication_tools_only(
    tracker: Tracker, tmp_path: Path
) -> None:
    """The two persistence tools stay declared for the terminal finalizer.

    This agent's own run calls neither; the declaration is what keeps the
    graph, the assembly, and the live evaluation dependency list honest about
    which services this agent may reach.
    """
    agent = _synthesizer(
        tracker,
        ScriptedCompleter(),
        synthesizer_tools(tracker, output_root=tmp_path),
    )

    assert SynthesizerAgent.name == "synthesizer"
    assert SynthesizerAgent.allowed_tools == ("write_document", "save_to_memory")
    assert agent.output_schema is SynthesizedReport


def test_the_composed_reader_report_uses_point_level_citations() -> None:
    report, _ = compose_report(_task(), draft=_draft(), limitations=[])
    section = ReportSection(
        title="Error correction",
        points=[
            ReportPoint(
                text="Break-even was reached.",
                claim_ids=[_claim().claim_id],
                source_urls=[SOURCE_URL],
            )
        ],
    )

    assert section.points[0].claim_ids == [_claim().claim_id]
    assert "- Break-even was reached. [1]" in report.markdown
    assert "Sources: [" not in report.markdown
