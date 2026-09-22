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
    LIMITATION_REASONS,
    QUALITY_STATUS_NOT_GATED,
    REPORT_SECTIONS,
    REPORT_SUMMARY_FALLBACK,
    ReportComposition,
    ReportPoint,
    ReportSection,
    composition_statements,
    fit_report_composition,
    reader_word_count,
    render_evidence_ledger,
    render_reader_report,
)
from deep_research.agents.steps import ReActRun
from deep_research.agents.synthesizer import (
    _COMMON_ABBREVIATIONS,
    DEFAULT_MEMORY_CONFIDENCE,
    STATEMENT_DISPOSITIONS,
    SYNTHESIS_OPEN_QUESTIONS_CHARS,
    AnswerRowDraft,
    ConstraintDraft,
    DraftContext,
    ReportDraft,
    ReportPointDraft,
    ReportSectionDraft,
    SynthesisTask,
    SynthesizedReport,
    SynthesizerAgent,
    _cited_evidence,
    _corpus_tokens,
    _selected_ids,
    _significant_figures,
    _strip_unsupported_figures,
    bounded_claim_packet,
    bounded_finding_digest,
    build_canonical_packet,
    build_report_composition,
    claim_label,
    claim_registry,
    compose_limitations,
    compose_report,
    evidence_report_filename,
    high_confidence_claims,
    limitation_reasons,
    memory_payload,
    ordered_claims_for_report,
    render_canonical_packet,
    render_revision_guidance,
    report_filename,
    report_messages,
    unattested_atoms,
    unattested_words,
)
from deep_research.evaluation.cases import cases_for
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import TokenUsage, Tracker
from deep_research.providers import (
    ProviderOutputLimitError,
    ProviderResponseTelemetry,
    ProviderTimeoutError,
)
from deep_research.tools.base import BaseTool
from deep_research.utils.config import AgentRuntimeConfig
from deep_research.utils.types import (
    AnswerContract,
    AtomicProposition,
    Claim,
    ClaimCluster,
    Critique,
    EvidencePassage,
    EvidenceTarget,
    EvidenceUnit,
    Finding,
    ResearchError,
    ResearchState,
    ScoredSource,
    SourceTemporal,
    SubTopic,
    target_is_answered,
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
    temporal: SourceTemporal | None = None,
) -> ScoredSource:
    payload: dict[str, object] = {
        "url": url,
        "title": "QEC 2025",
        "authority_score": 0.8,
        "recency_score": 0.7,
        "relevance_score": 0.9,
        "overall_score": overall,
        "rationale": "Peer-reviewed and corroborated.",
        "low_confidence": low_confidence,
    }
    if temporal is not None:
        payload["temporal"] = temporal
    return ScoredSource.model_validate(payload)


def _passage(url: str = OTHER_URL) -> EvidencePassage:
    return EvidencePassage(
        source_url=url,
        source_title="Independent review",
        locator="p. 1",
        excerpt="An independent review states the same figure.",
        stance="supports",
    )


def _claim(
    *,
    text: str = "Logical error rates fell below break-even in 2025.",
    verdict: str = "verified",
    confidence: float = 0.8,
    urls: list[str] | None = None,
    coverage_ids: list[str] | None = None,
    finding_fingerprints: list[str] | None = None,
    contradictions: list[str] | None = None,
    passages: list[EvidencePassage] | None = None,
    target_ids: list[str] | None = None,
) -> Claim:
    return Claim(
        claim_id=claim_fingerprint(text),
        text=text,
        source_urls=urls or [SOURCE_URL],
        verdict=verdict,
        evidence_status=(
            "verified_pair"
            if verdict == "verified"
            else "source_supported"
            if verdict == "insufficient_evidence"
            else None
        ),
        confidence=confidence,
        evidence=[],
        contradictions=contradictions or [],
        verification_evidence=passages or [],
        consumed_finding_fingerprints=finding_fingerprints or [],
        consumed_coverage_ids=coverage_ids or [],
        target_ids=target_ids or [],
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
        "evidence_units": {EVIDENCE_ID: _unit()},
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
        "evidence_units": {EVIDENCE_ID: _unit()},
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
        "executive summary point 2: repeats an earlier statement"
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


def test_constraint_cells_are_checked_against_the_evidence_their_row_cites() -> None:
    """An uncited factual table cell is repaired, not printed.

    The typed contract still has no field that *proves* a cell's semantics, so
    the check is provenance rather than guesswork: the wording of a published
    cell must come from the evidence the row cites. A cell the evidence does
    not carry is replaced with ``not stated`` and the repair is recorded —
    printing it as provider-attested prose is the defect this replaces.
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

    row = composition.constraints[0]
    assert row.deployment_mechanism == ""
    assert row.geography == ""
    assert rejected == [
        "constraint 1 deployment mechanism: no evidence for this cell",
        "constraint 1 geography: no evidence for this cell",
    ]
    assert "unsupported_cell" in composition.statement_dispositions


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


def test_the_composed_composition_is_the_reader_the_gates_judge() -> None:
    """The fit runs where the composition is built, not inside the renderer.

    A statement the word ceiling drops never reaches the reader, so it must
    not be in the composition the gates, the reviewer and the quality record
    read. ``compose_report`` therefore returns — and ``state_update`` stores —
    the fitted composition, with the fit reasons recorded where the ledger
    already reads this pass's dispositions. Before the fix the gates judged
    the unfitted draft: a critical target whose only answer the ceiling
    dropped was still counted as answered.
    """
    claim = _claim(urls=[SOURCE_URL, OTHER_URL], target_ids=["t1"])
    cluster = ClaimCluster(
        cluster_id=CLUSTER_ID,
        proposition=AtomicProposition(
            text=claim.text, value="1,200", unit="physical qubits"
        ),
        evidence_ids=[EVIDENCE_ID],
        member_claim_ids=[claim.claim_id],
        target_ids=["t1"],
        source_urls=list(claim.source_urls),
        verdicts=["verified"],
        verdict_evidence={"verified": list(claim.source_urls)},
        verdict_evidence_status={"verified": "verified_pair"},
    )
    filler = _claim(text="An unrelated measurement was recorded.")
    target = _target("t1", dimensions=["scale"])
    sub_topic = SubTopic(
        coverage_id="topic-01",
        title="Alpha",
        rationale="The first thing to establish.",
        search_queries=["alpha evidence"],
        success_criteria=["a measured alpha result"],
        priority=1,
        evidence_targets=[target],
    )
    task = _grounded_task(
        claims=[claim, filler],
        claim_clusters={CLUSTER_ID: cluster},
        sub_topics=[sub_topic],
        answer_contract=AnswerContract(
            question="How mature is quantum error correction?",
            scope_statement="What the recorded evidence establishes.",
            geographic_scope="unspecified",
            as_of_date="2026-08-01",
            evidence_period_requirement="current reported maturity",
            assumptions=["the question names no geography"],
            answer_kind="factual",
            requested_word_limit=250,
        ),
    )
    labels = {item.claim_id: label for label, item in claim_registry(task.claims)}
    openers = (
        "Another",
        "A further",
        "One more",
        "Yet another",
        "An additional",
        "A subsequent",
        "A related",
        "A comparable",
        "A later",
        "A recent",
        "A final",
    )
    points = [
        _point_draft(
            f"{opener} finding reports a measured result from the study that "
            "was read for this topic and nothing beyond it.",
            claim_ids=[labels[filler.claim_id]],
        )
        for opener in openers
    ]
    points.append(
        _point_draft(
            "Only this finding answers the obligation, and it is the one the "
            "ceiling drops because it is the longest statement in the report "
            "by a wide margin, with a great many more words than any other "
            "finding in this section carries.",
            claim_ids=[labels[claim.claim_id]],
            source_urls=[OTHER_URL],
        )
    )
    draft = ReportDraft(
        executive_summary=[],
        ranked_constraints=[],
        sections=[ReportSectionDraft(title="Error correction", points=points)],
        uncertainty_notes=[],
    )
    unfitted, _ = build_report_composition(
        task, draft, max_sections=1, limitations=[]
    )
    report, _ = compose_report(task, draft=draft, limitations=[])
    fitted = report.composition

    assert len(composition_statements(fitted)) < len(
        composition_statements(unfitted)
    )
    assert any(
        reason.startswith("length_budget_dropped")
        for reason in fitted.statement_dispositions
    )
    assert "length_budget_dropped" in report.evidence_markdown
    assert reader_word_count(report.markdown) <= 250
    assert points[-1].text not in report.markdown

    state = ResearchState(
        session_id="session-1",
        original_question=task.instruction,
        composition=fitted,
        sub_topics=list(fitted.sub_topics),
    )

    assert not target_is_answered(state, target)
    # The unfitted composition is what the gates used to read: the same
    # statement answered the target there, which is how a published report
    # could omit its only answer and still pass.
    assert target_is_answered(
        state.model_copy(update={"composition": unfitted}), target
    )


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


# --- Task 7: the answer, not the claim inventory -------------------------------

EVIDENCE_ID = "e1"
CLUSTER_ID = "cluster-1"
GROUNDING_EXCERPT = (
    "The 2025 review reports that logical error rates fell below break-even, "
    "and that the London pilot used an area licence with camera enforcement."
)


def _unit(
    *,
    evidence_id: str = EVIDENCE_ID,
    url: str = SOURCE_URL,
    excerpt: str = GROUNDING_EXCERPT,
    origin: str = "fact_checker",
) -> EvidenceUnit:
    return EvidenceUnit(
        evidence_id=evidence_id,
        read_id=f"read-{evidence_id}",
        source_url=url,
        source_title="QEC 2025",
        locator="p. 4",
        excerpt=excerpt,
        target_ids=["t1"],
        origin=origin,
    )


def _cluster(
    *,
    cluster_id: str = CLUSTER_ID,
    claim_ids: list[str] | None = None,
    evidence_ids: list[str] | None = None,
    verdicts: list[str] | None = None,
    contradictions: list[str] | None = None,
) -> ClaimCluster:
    claim = _claim()
    return ClaimCluster(
        cluster_id=cluster_id,
        proposition=AtomicProposition(text=claim.text),
        evidence_ids=evidence_ids if evidence_ids is not None else [EVIDENCE_ID],
        member_claim_ids=claim_ids if claim_ids is not None else [claim.claim_id],
        target_ids=["t1"],
        source_urls=list(claim.source_urls),
        verdicts=verdicts if verdicts is not None else ["verified"],
        verdict_evidence={"verified": list(claim.source_urls)},
        verdict_evidence_status={"verified": "verified_pair"},
    )


def _target(
    target_id: str = "t1",
    *,
    critical: bool = True,
    dimensions: list[str] | None = None,
) -> EvidenceTarget:
    return EvidenceTarget(
        target_id=target_id,
        coverage_id="topic-01",
        question=f"What does {target_id} require?",
        required_dimensions=dimensions or ["mechanism", "scale"],
        required=True,
        critical=critical,
        support_policy="independent_pair",
    )


def _grounded_task(**overrides: object) -> SynthesisTask:
    payload: dict[str, object] = {
        "evidence_units": {EVIDENCE_ID: _unit()},
        "claim_clusters": {CLUSTER_ID: _cluster()},
    }
    payload.update(overrides)
    return _task(**payload)


def _dispositions(composition: ReportComposition) -> list[str]:
    return list(composition.statement_dispositions)


def test_a_correctly_cited_but_unsupported_mechanism_is_repaired() -> None:
    draft = ReportDraft(
        executive_summary=[],
        ranked_constraints=[
            ConstraintDraft(
                constraint="Charge for road use inside the measured zone.",
                deployment_mechanism="a carbon levy on freight",
                geography="not stated",
                claim_ids=["C001"],
                source_urls=[SOURCE_URL],
            )
        ],
        sections=[],
        uncertainty_notes=[],
    )

    composition, _ = build_report_composition(
        _grounded_task(), draft, max_sections=4, limitations=[]
    )

    row = composition.constraints[0]
    assert row.deployment_mechanism == ""
    assert "unsupported_cell" in _dispositions(composition)
    assert "returned_to_fact_checker" in _dispositions(composition)
    mechanism = row.mechanism_statement
    assert mechanism is not None
    assert mechanism.mode == "context"


def test_an_attested_mechanism_and_geography_are_published_with_their_evidence() -> (
    None
):
    draft = ReportDraft(
        executive_summary=[],
        ranked_constraints=[
            ConstraintDraft(
                constraint="Charge for road use inside the measured zone.",
                deployment_mechanism="area licence with camera enforcement",
                geography="London",
                claim_ids=["C001"],
                source_urls=[SOURCE_URL],
            )
        ],
        sections=[],
        uncertainty_notes=[],
    )

    composition, rejected = build_report_composition(
        _grounded_task(), draft, max_sections=4, limitations=[]
    )

    row = composition.constraints[0]
    assert rejected == []
    assert row.deployment_mechanism == "area licence with camera enforcement"
    assert row.geography == "London"
    assert row.mechanism_statement is not None
    assert row.mechanism_statement.mode == "attributed"
    assert row.mechanism_statement.evidence_ids == [EVIDENCE_ID]


def test_a_wrong_geographic_extrapolation_is_repaired() -> None:
    draft = ReportDraft(
        executive_summary=[],
        ranked_constraints=[
            ConstraintDraft(
                constraint="Charge for road use inside the measured zone.",
                deployment_mechanism="not stated",
                geography="Germany",
                claim_ids=["C001"],
                source_urls=[SOURCE_URL],
            )
        ],
        sections=[],
        uncertainty_notes=[],
    )

    composition, _ = build_report_composition(
        _grounded_task(), draft, max_sections=4, limitations=[]
    )

    row = composition.constraints[0]
    assert row.geography == ""
    assert "unsupported_cell" in _dispositions(composition)


def test_a_wrong_geographic_extrapolation_in_prose_is_refused() -> None:
    """The evidence names London; a statement carrying it to Germany does not.

    A named place the cited evidence never states is a new fact whether it
    sits in a table cell or in a sentence, so the same attestation check
    applies to both.
    """
    draft = ReportDraft(
        executive_summary=[
            ReportPointDraft(
                text="The London pilot's result applies across Germany.",
                claim_ids=["C001"],
                source_urls=[SOURCE_URL],
            )
        ],
        ranked_constraints=[],
        sections=[],
        uncertainty_notes=[],
    )

    composition, rejected = build_report_composition(
        _grounded_task(), draft, max_sections=4, limitations=[]
    )

    assert composition.summary == []
    assert rejected == [
        "executive summary point 1: a name or place the evidence does not state"
    ]
    assert "unsupported_extrapolation" in _dispositions(composition)
    assert "returned_to_fact_checker" in _dispositions(composition)


def test_a_made_up_recommendation_is_refused() -> None:
    draft = ReportDraft(
        executive_summary=[],
        ranked_constraints=[],
        sections=[
            ReportSectionDraft(
                title="Error correction",
                points=[
                    ReportPointDraft(
                        text=(
                            "Policymakers should ban error-corrected qubits "
                            "until 2035."
                        ),
                        claim_ids=["C001"],
                        source_urls=[SOURCE_URL],
                    )
                ],
            )
        ],
        uncertainty_notes=[],
    )

    composition, rejected = build_report_composition(
        _grounded_task(), draft, max_sections=4, limitations=[]
    )

    assert composition.sections == []
    assert rejected == [
        "section 1 point 1: a recommendation outside the answer",
        "section 1: no printable point",
    ]
    assert "unsupported_recommendation" in _dispositions(composition)


def test_an_invented_truncation_limitation_is_refused() -> None:
    draft = ReportDraft(
        executive_summary=[],
        ranked_constraints=[],
        sections=[],
        uncertainty_notes=[
            "The source document was truncated before its capacity table."
        ],
    )

    composition, rejected = build_report_composition(
        _grounded_task(), draft, max_sections=4, limitations=[]
    )

    assert composition.uncertainty_statements == []
    assert rejected == [
        "uncertainty note 1: no recorded disposition for a truncated read"
    ]
    assert "unsupported_limitation" in _dispositions(composition)


def test_a_truncation_limitation_with_a_recorded_disposition_is_kept() -> None:
    draft = ReportDraft(
        executive_summary=[],
        ranked_constraints=[],
        sections=[],
        uncertainty_notes=[
            "The source document was truncated before its capacity table."
        ],
    )

    composition, rejected = build_report_composition(
        _grounded_task(
            errors=[
                ResearchError(
                    error_type="document_truncated",
                    source="agent.researcher",
                    message="The document reader stopped at the page limit.",
                )
            ]
        ),
        draft,
        max_sections=4,
        limitations=[],
    )

    assert rejected == []
    assert len(composition.uncertainty_statements) == 1


def test_an_uncertainty_sentence_cannot_print_an_unchecked_figure() -> None:
    """The reader must explain the gap without printing the figure."""
    draft = ReportDraft(
        executive_summary=[],
        ranked_constraints=[],
        sections=[],
        uncertainty_notes=[
            "The 890 GW of installed storage was not reported by any read "
            "source."
        ],
    )

    report, _ = compose_report(
        _grounded_task(), draft=draft, limitations=[]
    )

    assert "890" not in report.markdown
    assert "GW" not in report.markdown
    assert "installed storage" in report.markdown
    assert "unsupported_figure" in _dispositions(report.composition)
    assert "returned_to_fact_checker" in _dispositions(report.composition)


def test_a_settled_statement_cannot_carry_an_unattested_figure() -> None:
    draft = ReportDraft(
        executive_summary=[
            ReportPointDraft(
                text="Capacity reached 890 GW in 2025.",
                claim_ids=["C001"],
                source_urls=[SOURCE_URL],
            )
        ],
        ranked_constraints=[],
        sections=[],
        uncertainty_notes=[],
    )

    composition, rejected = build_report_composition(
        _grounded_task(), draft, max_sections=4, limitations=[]
    )

    assert composition.summary == []
    assert rejected == ["executive summary point 1: an unsupported figure"]
    assert "unsupported_figure" in _dispositions(composition)


def test_an_attested_figure_is_published() -> None:
    draft = ReportDraft(
        executive_summary=[
            ReportPointDraft(
                text="Logical error rates fell below break-even in 2025.",
                claim_ids=["C001"],
                source_urls=[SOURCE_URL],
            )
        ],
        ranked_constraints=[],
        sections=[],
        uncertainty_notes=[],
    )

    composition, rejected = build_report_composition(
        _grounded_task(), draft, max_sections=4, limitations=[]
    )

    assert rejected == []
    assert composition.summary[0].statement is not None
    assert composition.summary[0].statement.mode == "settled"
    assert composition.summary[0].statement.evidence_ids == [EVIDENCE_ID]


def test_a_checked_derivation_passes_through_recorded_premises() -> None:
    evidence = {
        EVIDENCE_ID: _unit(excerpt="The pilot covered 1200 hectares in 2025.")
    }
    draft = ReportDraft(
        executive_summary=[
            ReportPointDraft(
                text="The pilot covered 12 square kilometres.",
                basis="unit conversion: 1200 hectares = 12 square kilometres",
                claim_ids=["C001"],
                source_urls=[SOURCE_URL],
            )
        ],
        ranked_constraints=[],
        sections=[],
        uncertainty_notes=[],
    )

    composition, rejected = build_report_composition(
        _grounded_task(evidence_units=evidence),
        draft,
        max_sections=4,
        limitations=[],
    )

    assert rejected == []
    statement = composition.summary[0].statement
    assert statement is not None
    assert statement.mode == "inference"
    assert "1200 hectares" in (statement.basis or "")


def test_a_derivation_without_premises_is_refused() -> None:
    evidence = {
        EVIDENCE_ID: _unit(excerpt="The pilot covered 1200 hectares in 2025.")
    }
    draft = ReportDraft(
        executive_summary=[
            ReportPointDraft(
                text="The pilot covered 12 square kilometres.",
                claim_ids=["C001"],
                source_urls=[SOURCE_URL],
            )
        ],
        ranked_constraints=[],
        sections=[],
        uncertainty_notes=[],
    )

    composition, rejected = build_report_composition(
        _grounded_task(evidence_units=evidence),
        draft,
        max_sections=4,
        limitations=[],
    )

    assert composition.summary == []
    assert rejected == ["executive summary point 1: an unsupported figure"]
    assert "unsupported_figure" in _dispositions(composition)


def test_a_repeated_fact_cannot_inflate_the_report() -> None:
    repeated = ReportPointDraft(
        text="Break-even was reached in 2025.",
        claim_ids=["C001"],
        source_urls=[SOURCE_URL],
    )
    draft = ReportDraft(
        executive_summary=[repeated],
        ranked_constraints=[],
        sections=[
            ReportSectionDraft(title="Error correction", points=[repeated]),
        ],
        uncertainty_notes=[],
    )

    composition, rejected = build_report_composition(
        _grounded_task(), draft, max_sections=4, limitations=[]
    )

    assert len(composition.summary) == 1
    # The fact behind both copies is counted once, and the copy that would
    # have added nothing to the reader's information is refused.
    assert composition.distinct_statement_count == 1
    assert "duplicate_statement" in _dispositions(composition)
    assert "section 1 point 1: repeats an earlier statement" in rejected


def test_a_summary_restatement_and_its_body_discussion_are_counted_once() -> None:
    """Repetition is not itself the defect; inflation is.

    A brief restatement in the summary and a longer discussion in the findings
    are two statements of one fact. Both render, and the fact is counted once.
    """
    draft = ReportDraft(
        executive_summary=[
            ReportPointDraft(
                text="Break-even was reached in 2025.",
                claim_ids=["C001"],
                source_urls=[SOURCE_URL],
            )
        ],
        ranked_constraints=[],
        sections=[
            ReportSectionDraft(
                title="Error correction",
                points=[
                    ReportPointDraft(
                        text=(
                            "Logical error rates fell below break-even in the "
                            "2025 review."
                        ),
                        claim_ids=["C001"],
                        source_urls=[SOURCE_URL],
                    )
                ],
            )
        ],
        uncertainty_notes=[],
    )

    composition, rejected = build_report_composition(
        _grounded_task(), draft, max_sections=4, limitations=[]
    )

    assert rejected == []
    assert len(composition.summary) == 1
    assert len(composition.sections[0].points) == 1
    assert composition.distinct_statement_count == 1


def test_an_uncertainty_note_may_name_the_questions_own_recorded_dates() -> None:
    """A note about the question's own horizon states a recorded fact.

    The frozen contract's period and the sources' recorded dates are records
    this pass made, so naming them is reporting the pass rather than asserting
    the world. Removing those figures would make the note say something else.
    """
    draft = ReportDraft(
        executive_summary=[],
        ranked_constraints=[],
        sections=[],
        uncertainty_notes=[
            "The 2035 forecast horizon is outside this pass's scope."
        ],
    )

    composition, _ = build_report_composition(
        _grounded_task(
            sources=[
                _source(
                    temporal=SourceTemporal(
                        publication_date="2026-01-01",
                        forecast_horizon="2035",
                    )
                )
            ]
        ),
        draft,
        max_sections=4,
        limitations=[],
    )

    assert composition.uncertainty_statements[0].text == (
        "The 2035 forecast horizon is outside this pass's scope."
    )
    assert "unsupported_figure" not in _dispositions(composition)


def test_a_repair_keeps_the_subject_of_the_sentence_it_strips() -> None:
    """A year is not a unit: the noun after it survives the repair."""
    draft = ReportDraft(
        executive_summary=[],
        ranked_constraints=[],
        sections=[],
        uncertainty_notes=["The 2035 horizon was not reported by any read."],
    )

    composition, _ = build_report_composition(
        _grounded_task(), draft, max_sections=4, limitations=[]
    )

    assert composition.uncertainty_statements[0].text == (
        "The horizon was not reported by any read."
    )
    assert "2035" not in composition.uncertainty_statements[0].text
    assert "unsupported_figure" in _dispositions(composition)


def test_every_recorded_disposition_comes_from_the_enumerated_vocabulary() -> None:
    """A disposition is a token a consumer can group on, not free prose."""
    draft = ReportDraft(
        executive_summary=[],
        ranked_constraints=[
            ConstraintDraft(
                constraint="Charge for road use inside the measured zone.",
                deployment_mechanism="a carbon levy on freight",
                geography="Germany",
                claim_ids=["C001"],
                source_urls=[SOURCE_URL],
            )
        ],
        sections=[
            ReportSectionDraft(
                title="Error correction",
                points=[
                    ReportPointDraft(
                        text="Policymakers should ban error-corrected qubits.",
                        claim_ids=["C001"],
                        source_urls=[SOURCE_URL],
                    )
                ],
            )
        ],
        uncertainty_notes=[
            "The source document was truncated before its capacity table.",
            "The 890 GW of installed storage was not reported by any read.",
        ],
    )

    composition, _ = build_report_composition(
        _grounded_task(), draft, max_sections=4, limitations=[]
    )

    assert composition.statement_dispositions
    assert set(composition.statement_dispositions) <= set(
        STATEMENT_DISPOSITIONS
    )


def test_each_uncertainty_kind_reaches_its_own_reader_group() -> None:
    """The note vocabulary and the renderer's groups are one vocabulary.

    The basis a note carries is the group key the renderer reads. Writing the
    matched token instead ("disagree", "no read") left six of nine note kinds
    in the unheaded list, so a reader could not tell a live disagreement from
    a gap nobody retrieved.
    """
    uncertain = "### Uncertain or conflicting"
    for note, heading in (
        ("The sources disagree about the projection.", uncertain),
        ("The evidence conflicts on this point.", uncertain),
        ("An independent source contradicts the finding.", uncertain),
        ("No read was acquired for transmission costs.", "### Not acquired"),
        ("That data was not retrieved this pass.", "### Not acquired"),
        ("The topic was not acquired.", "### Not acquired"),
        ("Retail tariffs are out of scope for this pass.", "### Outside scope"),
        ("That question is outside scope for the pass.", "### Outside scope"),
    ):
        composition = _compose_uncertainty(note)

        assert heading in render_reader_report(composition), note


def test_an_unclassifiable_uncertainty_note_still_reads_as_uncertainty() -> None:
    """The fallback group is a group, not an unheaded orphan."""
    composition = _compose_uncertainty("Confidence is low here.")

    uncertainty = render_reader_report(composition)
    assert "### Uncertain or conflicting" in uncertainty
    assert "- Confidence is low here." in uncertainty


def _compose_uncertainty(note: str) -> ReportComposition:
    """One validated composition whose only uncertainty is ``note``.

    The pass records the acquisition failure a "not acquired" note describes,
    because a note may only assert an evidence state this pass recorded.
    """
    composition, _ = build_report_composition(
        _grounded_task(
            errors=[
                ResearchError(
                    error_type="no_read_acquired",
                    source="agent.researcher",
                    message="No read was acquired for a target.",
                )
            ]
        ),
        ReportDraft(
            executive_summary=[],
            ranked_constraints=[],
            sections=[],
            uncertainty_notes=[note],
        ),
        max_sections=4,
        limitations=[],
    )
    assert len(composition.uncertainty_statements) == 1
    return composition


def test_a_cell_word_is_not_attested_by_a_longer_word_that_contains_it() -> None:
    """"generation" does not vouch for "gen": the cell check is whole-token."""
    assert unattested_words("gen", "carbon capture generation") == ["gen"]
    assert unattested_words("car gen", "carbon capture generation") == [
        "car",
        "gen",
    ]
    # Positive counterpart: the words the corpus does carry stay attested, so
    # the check cannot pass by refusing every cell.
    assert unattested_words("carbon capture", "carbon capture generation") == []


def test_a_figure_that_ends_a_sentence_in_the_evidence_is_attested() -> None:
    """An excerpt is an exact sentence, so its figures often carry a period."""
    corpus = "Installed capacity reached 1,200."

    assert unattested_atoms("capacity was 1,200 in total", corpus) == []
    assert (
        _strip_unsupported_figures("Growth to 1,200 was not examined.", corpus)
        == "Growth to 1,200 was not examined."
    )
    # The lookup side and the corpus side agree on both ends of the token.
    assert _corpus_tokens(corpus) == {"installed", "capacity", "reached", "1,200"}
    # A figure the evidence does not carry is still refused, so the repair is
    # not simply disabled.
    assert unattested_atoms("capacity was 2,900 in total", corpus) == ["2,900"]


def test_a_figure_token_does_not_swallow_the_word_after_it() -> None:
    """A malformed figure token would land in the ledger's disposition text."""
    assert _significant_figures("capacity was 1,200 in total") == ["1,200"]
    assert _significant_figures("reached 12 sites") == ["12"]
    # A recognised unit still travels with its figure, so the strip path can
    # remove "890 GW of" as one phrase.
    assert _significant_figures("capacity reached 890 GW") == ["890 GW"]


def test_the_cited_evidence_is_the_passage_the_selected_id_names() -> None:
    """The corpus is looked up by evidence id, not by the stance beside it.

    Read the wrong way round, the lookup asks the registry for evidence called
    "supports", finds nothing, and contributes no text at all — a statement
    drafted from a passage the packet does hold reads as unattested.
    """
    excerpt = "The London pilot used an area licence with camera enforcement."
    claim = _claim().model_copy(
        update={"evidence_selection": {EVIDENCE_ID: "supports"}}
    )
    context = DraftContext(evidence={EVIDENCE_ID: _unit(excerpt=excerpt)})

    assert excerpt.casefold() in _cited_evidence([claim], context)


def test_a_two_letter_place_the_evidence_never_names_is_refused() -> None:
    assert unattested_atoms("Deployment grew in the EU.", "output rose") == ["EU"]


def test_a_sentence_initial_acronym_the_evidence_never_names_is_refused() -> None:
    """No ordinary sentence opener is all-caps.

    The position exemption covers a mixed-case opener, not an acronym, so a
    place named only at the start of a sentence is still checked.
    """
    assert unattested_atoms("EU capacity rose.", "output rose") == ["EU"]
    assert unattested_atoms("Deployment grew in the UK.", "output rose") == ["UK"]


def test_a_sentence_initial_ordinary_word_is_not_an_unattested_name() -> None:
    """The positive half of the accepted residual: ordinary openers stay safe.

    "Charge" cannot be told from "California" without an English lexicon, so a
    sentence-initial mixed-case word is exempt by position. A sentence-initial
    mixed-case place name in prose therefore stays undetected — carried to
    Task 10 — which is strictly narrower than missing "UK"/"EU" everywhere.
    """
    assert unattested_atoms("The output rose.", "output rose") == []
    assert (
        unattested_atoms("Charge for driving inside the zone.", "output rose") == []
    )
    assert unattested_atoms("Supported constraint.", "output rose") == []
    assert unattested_atoms("California added capacity.", "output rose") == []


def test_a_mid_sentence_place_the_evidence_never_names_is_still_refused() -> None:
    assert unattested_atoms("Output rose in California.", "output rose") == [
        "California"
    ]
    # And a place the evidence does carry is not refused.
    assert (
        unattested_atoms(
            "Output rose in California.", "output rose in california"
        )
        == []
    )


def test_a_unit_abbreviation_is_not_read_as_a_name() -> None:
    """A unit is not a name, whatever case the writer gave it."""
    assert (
        unattested_atoms(
            "capacity reached 890 GW", "capacity reached 890 gigawatts"
        )
        == []
    )


def test_an_answer_row_label_the_evidence_does_not_carry_is_repaired() -> None:
    """A Subject or Period cell is a factual assertion like a mechanism cell."""
    composition, rejected = _compose_comparison(subject="Patagonia")

    row = composition.answer_rows[0]
    assert row.cells[0].text == "not stated"
    assert row.cells[0].mode == "context"
    assert "answer row 1 subject: no evidence for this cell" in rejected
    assert "unsupported_cell" in composition.statement_dispositions


def test_an_answer_row_label_the_evidence_carries_is_published() -> None:
    """Positive counterpart: an attested label publishes and stays the label."""
    composition, rejected = _compose_comparison(subject="2025")

    row = composition.answer_rows[0]
    assert rejected == []
    assert row.cells[0].text == "2025"
    assert row.labels == list(row.cells[:-1])
    assert row.statement is row.cells[-1]
    assert row.statement is not None
    assert row.statement.text.startswith("Logical error rates")


def test_an_answer_row_period_the_evidence_does_not_carry_is_repaired() -> None:
    """A Period cell is a figure; the cell check attests figures, not only words.

    ``unattested_words`` alone leaves a cell with no words in it attested
    vacuously, so a fabricated period would publish as an attributed statement
    carrying the row's evidence ids — a date the reader has no reason to
    distrust, attached to evidence that never states it.
    """
    for subject in ("2031", "1999", "2031-2040", "12,000"):
        composition, rejected = _compose_comparison(subject=subject)

        row = composition.answer_rows[0]
        assert row.cells[0].text == "not stated", subject
        assert row.cells[0].mode == "context", subject
        assert "answer row 1 subject: no evidence for this cell" in rejected


def test_an_evidence_spelled_out_abbreviation_is_not_refused() -> None:
    """The domain writes "EVs" where its evidence writes "electric vehicles"."""
    corpus = (
        "electric vehicles and solar photovoltaic output rose; carbon dioxide "
        "fell; gross domestic product grew; battery electric vehicles and "
        "plug-in hybrid electric vehicles; levelised cost of energy; battery "
        "energy storage systems; carbon capture and storage; carbon capture "
        "utilisation and storage; small modular reactors; capital expenditure "
        "and operating expenditure; large language models; graphics processing "
        "units; compound annual growth rate; internal combustion engine; "
        "distributed energy resources; transmission system operators; "
        "nationally determined contributions; coronavirus disease; application "
        "programming interface; nitrogen oxides; greenhouse gases"
    )
    for token in (
        "EVs",
        "PV",
        "CO2",
        "GDP",
        "AI",
        "OK",
        "HVDC",
        "PPAs",
        "BEVs",
        "PHEVs",
        "LCOE",
        "BESS",
        "CCS",
        "CCUS",
        "SMRs",
        "CAPEX",
        "OPEX",
        "LLMs",
        "GPUs",
        "CAGR",
        "ICE",
        "DERs",
        "TSOs",
        "NDCs",
        "COVID",
        "API",
        "NOx",
        # The plural of a listed singular is the same noun.
        "GHGs",
    ):
        text = f"Output included {token} last year."
        assert unattested_atoms(text, corpus) == [], token
    # "R&D" can never be a name candidate: the pattern needs a letter in the
    # second position, so the entry would be unreachable.
    assert "r&d" not in _COMMON_ABBREVIATIONS


def test_a_place_acronym_is_still_refused_after_the_abbreviation_carve_out() -> None:
    """The carve-out is for technology and quantities, not for places.

    "UK", "EU", "US" and "IEA" stay out of the list on purpose: catching a
    place or an agency the evidence never names is what the acronym check is
    for.
    """
    for text, expected in (
        ("Deployment grew in the UK.", ["UK"]),
        ("Deployment grew in the EU.", ["EU"]),
        ("Output rose in the US.", ["US"]),
        ("Output rose per IEA.", ["IEA"]),
    ):
        assert unattested_atoms(text, "output rose") == expected, text
    for abbreviation in ("uk", "eu", "us", "iea"):
        assert abbreviation not in _COMMON_ABBREVIATIONS


def test_a_capitalised_opener_after_markup_is_still_an_opener() -> None:
    """A bullet, a quote and a bracket open a sentence too.

    Model prose arrives with markup in front of it, and a word capitalised
    after a bullet is capitalised by position exactly as it is after a full
    stop.
    """
    for text in (
        "- California added capacity.",
        "  - California added capacity.",
        "Line one.\n- California added capacity.",
        "> California added capacity.",
        '"California added capacity."',
        "(California added capacity.)",
        "- Charge for driving inside the zone.",
    ):
        assert unattested_atoms(text, "output rose") == [], text
    # A name that is not at an opener is still checked.
    assert unattested_atoms("- Output rose in California.", "output rose") == [
        "California"
    ]
    # A dash inside a line, a hyphenated compound and a closing bracket are
    # not openers either: the word after them is capitalised because it is a
    # name, and an em-dash mid-sentence is the commonest construction in model
    # prose. An earlier, looser opener rule hid every one of these.
    for text, expected in (
        ("Output rose \u2014 California added capacity.", ["California"]),
        ("Output rose - California added capacity.", ["California"]),
        ("The London\u2013California route.", ["London", "California"]),
        ("Capacity in mid-California rose.", ["California"]),
        ("Output rose (est.) California added.", ["California"]),
    ):
        assert unattested_atoms(text, "output rose") == expected, text


def _compose_comparison(
    *, subject: str
) -> tuple[ReportComposition, list[str]]:
    """One comparison composition with the given row subject."""
    task = _grounded_task(
        answer_contract=AnswerContract(
            question="How mature is quantum error correction?",
            scope_statement="2025 logical error rates.",
            geographic_scope="unspecified",
            as_of_date="2026-09-16",
            evidence_period_requirement="the 2025 reported rate",
            assumptions=["no geography named"],
            answer_kind="comparison",
        )
    )
    draft = ReportDraft(
        executive_summary=[],
        ranked_constraints=[],
        sections=[],
        uncertainty_notes=[],
        answer_rows=[
            AnswerRowDraft(
                subject=subject,
                dimension="break-even",
                finding="Logical error rates fell below break-even in 2025.",
                claim_ids=["C001"],
                source_urls=[SOURCE_URL],
            )
        ],
    )
    return build_report_composition(task, draft, max_sections=4, limitations=[])


def test_the_reader_shows_a_qualitative_strength_not_a_confidence_number() -> None:
    """The brief: derive labels from status; do not print 0.90 as a probability."""
    composition, _ = _compose_comparison(subject="2025")
    reader = render_reader_report(composition)

    assert "| Option | Dimension | Evidenced finding | Evidence strength |" in reader
    assert "Confidence" not in reader
    assert "0.80" not in reader
    assert "independently corroborated" in reader


def test_the_ledger_keeps_the_confidence_number_beside_its_caveat() -> None:
    """The number is not deleted, it moves to where the caveat can sit."""
    composition, _ = _compose_comparison(subject="2025")
    ledger = render_evidence_ledger(composition)
    registry = ledger.split("## Checked claim registry", 1)[1]

    assert "0.80" in registry
    assert "Confidence" in registry


def test_a_new_factual_assertion_is_returned_to_the_fact_checker() -> None:
    draft = ReportDraft(
        executive_summary=[],
        ranked_constraints=[
            ConstraintDraft(
                constraint="Charge for road use inside the measured zone.",
                deployment_mechanism="a carbon levy on freight",
                geography="not stated",
                claim_ids=["C001"],
                source_urls=[SOURCE_URL],
            )
        ],
        sections=[],
        uncertainty_notes=[],
    )

    composition, _ = build_report_composition(
        _grounded_task(), draft, max_sections=4, limitations=[]
    )
    returned = composition.returned_to_fact_checker

    assert returned == ["a carbon levy on freight"]
    # Detection and disposition only: nothing here routes a new claim.
    assert "returned_to_fact_checker" in _dispositions(composition)


def test_the_canonical_packet_is_balanced_across_critical_targets() -> None:
    claims = [
        _claim(
            text="Alpha fact was measured.",
            urls=[OTHER_URL],
            target_ids=["t2"],
        ),
        _claim(text="Beta fact was measured.", target_ids=["t3"]),
        _claim(text="Gamma fact was measured.", target_ids=["t1"]),
    ]

    packet = build_canonical_packet(
        claims=claims,
        clusters={},
        evidence={EVIDENCE_ID: _unit()},
        targets=[_target("t1"), _target("t2"), _target("t3")],
        sources=[_source()],
        limit=2,
    )

    covered = {target for entry in packet.entries for target in entry.target_ids}
    assert len(covered) == 2
    assert claims[2].text in render_canonical_packet(packet)
    assert packet.omitted_ids
    assert packet.continuation_batches


def test_the_canonical_packet_carries_support_counterevidence_and_dates() -> None:
    claim = _claim(
        contradictions=["A vendor report disagrees."],
        passages=[_passage()],
        target_ids=["t1"],
    )
    cluster = _cluster(verdicts=["verified", "contradicted"])

    packet = build_canonical_packet(
        claims=[claim],
        clusters={CLUSTER_ID: cluster},
        evidence={
            EVIDENCE_ID: _unit(),
            "e2": _unit(
                evidence_id="e2",
                url=OTHER_URL,
                excerpt="A vendor report disagrees.",
            ),
        },
        targets=[_target()],
        sources=[
            _source(
                temporal=SourceTemporal(
                    publication_date="2026-01-01",
                    data_period="2024",
                )
            )
        ],
        limit=10,
    )
    rendered = render_canonical_packet(packet)

    assert "supports:" in rendered
    assert "contradicts:" in rendered
    assert "publication=2026-01-01" in rendered
    assert "data_period=2024" in rendered
    assert "obligation:" in rendered


def test_the_canonical_packet_lists_omitted_ids_and_continuation_batches() -> None:
    claims = [
        _claim(
            text=f"Measured result {index} was reported.",
            urls=[SOURCE_URL],
            target_ids=["t1"],
        )
        for index in range(6)
    ]
    packet = build_canonical_packet(
        claims=claims,
        clusters={},
        evidence={EVIDENCE_ID: _unit()},
        targets=[_target()],
        sources=[_source()],
        limit=2,
        batch_size=2,
    )
    rendered = render_canonical_packet(packet)

    assert len(packet.entries) == 2
    assert len(packet.omitted_ids) == 4
    assert len(packet.continuation_batches) == 2
    assert "continuation batch 1:" in rendered
    assert "cannot be cited by this draft" in rendered


def test_the_packet_selects_the_evidence_ids_and_not_the_stances() -> None:
    """``evidence_selection`` is keyed by evidence id, valued with its stance.

    Read the wrong way round, the packet's selected-passage lines name a
    stance where an id belongs. Nothing in the registry answers to "supports",
    so the claim is presented with no support block at all — and every other
    test in the suite stays green while it is.
    """
    claim = _claim().model_copy(
        update={"evidence_selection": {EVIDENCE_ID: "supports"}}
    )

    assert _selected_ids(claim, None) == [EVIDENCE_ID]
    # An empty cluster is not a cluster with evidence: the claim's own
    # selection is what the fallback reads.
    assert _selected_ids(claim, _cluster(evidence_ids=[])) == [EVIDENCE_ID]


def test_report_messages_carry_the_canonical_packet_and_the_answer_form() -> None:
    body = report_messages(
        _grounded_task(
            answer_contract=AnswerContract(
                question="How mature is quantum error correction?",
                scope_statement="A comparison of the recorded approaches.",
                geographic_scope="unspecified",
                as_of_date="2026-09-16",
                evidence_period_requirement="current reported maturity",
                assumptions=["the question names no geography"],
                answer_kind="comparison",
            )
        ),
        finding_digest=10,
        claim_digest=10,
    )[1].content

    assert "# Canonical evidence packet" in body
    assert "# Answer form" in body
    assert "comparison" in body
    assert "answer_rows" in body


def test_build_task_carries_the_frozen_contract_and_the_registries(
    tracker: Tracker, tmp_path: Path
) -> None:
    contract = AnswerContract(
        question="How mature is quantum error correction?",
        scope_statement="What the recorded evidence establishes.",
        geographic_scope="unspecified",
        as_of_date="2026-09-16",
        evidence_period_requirement="current reported maturity",
        assumptions=["the question names no geography"],
        answer_kind="factual",
        requested_word_limit=9000,
    )
    agent = _synthesizer(
        tracker,
        ScriptedCompleter(),
        synthesizer_tools(tracker, output_root=tmp_path),
    )
    state = _state(
        answer_contract=contract,
        evidence_units={EVIDENCE_ID: _unit()},
        claim_clusters={CLUSTER_ID: _cluster()},
    )

    task = agent.build_task(state)

    assert task.answer_contract is not None
    assert task.answer_contract.answer_kind == "factual"
    assert task.answer_contract.requested_word_limit == 9000
    assert list(task.evidence_units) == [EVIDENCE_ID]
    assert list(task.claim_clusters) == [CLUSTER_ID]


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
    assert outcome.result.evidence_path == "report-session-1-0-evidence.md"
    # A composed future filename is not a publication record: the terminal
    # finalizer is the sole writer of ``state.evidence_path``.
    assert "evidence_path" not in outcome.state_update
    assert outcome.state_update["unique_source_count"] == 1
    assert outcome.state_update["unique_claim_count"] == 1
    # The typed composition the artifacts render travels with them, so the
    # graph's quality pass judges the exact points this pass composed.
    composition = outcome.state_update["composition"]
    assert isinstance(composition, ReportComposition)
    assert composition is outcome.result.composition
    # Both artifacts are exactly this composition rendered. ``ContractModel``
    # strips surrounding whitespace from every string field, so the report
    # record holds the render without its trailing newline.
    assert render_reader_report(composition).strip() == outcome.result.markdown
    assert (
        render_evidence_ledger(composition).strip()
        == outcome.result.evidence_markdown
    )
    assert composition.quality_status == QUALITY_STATUS_NOT_GATED
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
async def test_a_report_provider_failure_discloses_one_limitation_list(
    tracker: Tracker, tmp_path: Path
) -> None:
    """The event and the artifacts disclose the same limitations.

    ``run`` used to recompute ``list(task.limitations) +
    ["report_generation_failed"]`` inline while ``compose_limitations``
    computed the same list for the artifacts. They agreed by inspection only,
    and the two copies land in a user-visible artifact and in telemetry, so the
    invariant is pinned here rather than assumed.
    """
    agent = _synthesizer(
        tracker,
        ScriptedCompleter(outputs=[ProviderTimeoutError("timed out")]),
        synthesizer_tools(tracker, output_root=tmp_path),
    )
    state = _state()

    async with tracker.session_span("session-1", "question"):
        outcome = await agent.run(state)

    assert outcome.result is not None
    completed = outcome.state_update["events"][-1].metadata
    task = agent.build_task(state)
    expected = compose_limitations(task, provider_failed=True)

    assert expected == [*task.limitations, "report_generation_failed"]
    assert completed["limitations"] == expected
    assert LIMITATION_REASONS["report_generation_failed"] in (
        outcome.result.markdown
    )


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


# --- the agent owns the terminal write tools ----------------------------------


@pytest.mark.asyncio
async def test_the_synthesizer_can_act_as_the_terminal_publisher(
    tracker: Tracker, tmp_path: Path
) -> None:
    """The agent that declares the write tools is the writer the graph uses.

    Synthesis itself still calls neither tool: ``run`` publishes nothing. The
    two methods below are what the terminal finalizer reaches the filesystem
    and memory through, which is why the agent satisfies the graph's
    ``ReportPublisher`` protocol without the graph importing agent internals.
    """
    memory = FakeMemory()
    agent = _synthesizer(
        tracker,
        ScriptedCompleter(outputs=[_draft()]),
        synthesizer_tools(tracker, output_root=tmp_path, memory=memory),
    )

    async with tracker.session_span("session-1", "question"):
        written = await agent.publish_document(
            filename="report-session-1-0.md", content="# Reader report"
        )
        saved = await agent.publish_claim(
            content="Break-even was reached in 2025.",
            metadata={"entry_type": "finding", "session_id": "session-1"},
        )

    assert written.success is True
    assert written.data == {
        "path": "report-session-1-0.md",
        "bytes_written": len(b"# Reader report"),
    }
    assert (tmp_path / "report-session-1-0.md").read_text(
        encoding="utf-8"
    ) == "# Reader report"
    assert saved.success is True
    assert memory.saved == [
        (
            "Break-even was reached in 2025.",
            {"entry_type": "finding", "session_id": "session-1"},
        )
    ]


@pytest.mark.asyncio
async def test_a_failed_publish_returns_the_tools_own_failure(
    tracker: Tracker, tmp_path: Path
) -> None:
    """A failed write is a result, never an exception: the finalizer records it."""
    agent = _synthesizer(
        tracker,
        ScriptedCompleter(outputs=[_draft()]),
        synthesizer_tools(tracker, output_root=tmp_path),
    )

    async with tracker.session_span("session-1", "question"):
        written = await agent.publish_document(
            filename="../escape.md", content="# Reader report"
        )

    assert written.success is False
    assert written.error is not None
    assert written.error.type == "ValidationError"
    assert list(tmp_path.iterdir()) == []


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
