"""Tests for research state construction, serialization, and updates."""

import pytest
from pydantic import ValidationError

from deep_research.agents.evidence import (
    READ_ADMISSION_OPERATION,
    EvidenceIdentityConflict,
    build_boundary_audit,
    build_evidence_unit,
    build_read_record,
)
from deep_research.agents.identity import claim_fingerprint
from deep_research.utils.types import (
    LEGACY_QUALITY_CONTRACT_VERSION,
    QUALITY_CONTRACT_VERSION,
    Claim,
    Critique,
    EvidenceDisposition,
    EvidenceTarget,
    Finding,
    MemorySnapshot,
    ReadRecord,
    RefinementTarget,
    ReportComposition,
    ReportPoint,
    ReportStatement,
    ResearchError,
    ResearchEvent,
    ResearchProgress,
    ResearchState,
    ScoredSource,
    SubTopic,
    advance_research_iteration,
    merge_research_state,
    progress_improved,
    target_is_answered,
    unanswered_required_targets,
)

PASSAGE = "Example Lab measured that 1,200 MW of interconnection capacity was withheld"
TEXT = (
    "Queue Study. Example Lab measured that 1,200 MW of interconnection "
    "capacity was withheld in 2025."
)


def _read_record(
    *,
    requested_url: str = "https://lab.example/queue",
    resolved_url: str = "https://lab.example/queue",
) -> ReadRecord:
    return build_read_record(
        session_id="session-1",
        reader="web_scraper",
        requested_url=requested_url,
        resolved_url=resolved_url,
        title="Queue Study",
        retrieved_at="2026-09-16T10:00:00+00:00",
        text=TEXT,
        passages={"p-1": PASSAGE},
    )


def sub_topic(title: str = "Adoption", priority: int = 1) -> SubTopic:
    return SubTopic(
        coverage_id="topic-01",
        title=title,
        rationale=f"Research {title.lower()}.",
        search_queries=[f"{title.lower()} evidence"],
        success_criteria=[f"Find evidence about {title.lower()}."],
        priority=priority,
    )


def finding(content: str = "Adoption increased.") -> Finding:
    return Finding(
        content=content,
        source_url="https://example.com/source",
        source_title="Example source",
        extracted_at="2026-07-25T12:00:00+00:00",
        confidence=0.8,
        related_sub_topic="Adoption",
    )


def source(title: str = "Example source") -> ScoredSource:
    return ScoredSource(
        url="https://example.com/source",
        title=title,
        authority_score=0.8,
        recency_score=0.7,
        relevance_score=0.9,
        overall_score=0.75,
        rationale="Relevant and independently corroborated.",
    )


def claim(text: str = "Adoption increased.") -> Claim:
    return Claim(
        claim_id=claim_fingerprint(text),
        text=text,
        source_urls=["https://example.com/source"],
        verdict="verified",
        # The fixture's premise is a claim carrying the strict badge; ``Claim``
        # refuses a verified verdict with no ``verified_pair`` behind it.
        evidence_status="verified_pair",
        confidence=0.9,
        evidence=["The source reports a year-over-year increase."],
        contradictions=[],
        verification_evidence=[],
    )


def critique(score: int = 8) -> Critique:
    return Critique(
        score=score,
        gaps=[],
        unsupported_claims=[],
        recommended_queries=[],
        should_continue=False,
        rationale="The report is complete.",
    )


def test_default_state_construction_uses_independent_values() -> None:
    first = ResearchState(session_id="session-1", original_question="Question one?")
    second = ResearchState(session_id="session-2", original_question="Question two?")

    first.sub_topics.append(sub_topic())
    first.memory_context.suggested_strategies.append("Compare surveys.")

    assert first.iteration == 0
    assert first.max_iterations == 3
    assert first.report is None
    assert first.critique is None
    assert second.sub_topics == []
    assert second.memory_context == MemorySnapshot()
    assert second.events == []
    assert second.errors == []


def test_state_round_trips_through_json_compatible_dict() -> None:
    state = ResearchState(
        session_id="session-1",
        original_question="How is enterprise AI adoption changing?",
        sub_topics=[sub_topic()],
        raw_findings=[finding()],
        evaluated_sources=[source()],
        verified_claims=[claim()],
        report="# Research report",
        critique=critique(),
        iteration=1,
        max_iterations=3,
        memory_context=MemorySnapshot(
            similar_findings=[finding("Prior adoption also increased.")],
            known_source_reputations={"example.com": 0.85},
            suggested_strategies=["Compare independent surveys."],
        ),
        events=[
            ResearchEvent(
                event_type="agent.completed",
                source="planner",
                message="Planner completed.",
                timestamp="2026-07-25T12:01:00+00:00",
                metadata={"sub_topic_count": 1},
            )
        ],
        errors=[
            ResearchError(
                error_type="search_timeout",
                source="web_search",
                message="One search request timed out.",
                timestamp="2026-07-25T12:02:00+00:00",
                details={"retry_count": 2},
            )
        ],
    )

    payload = state.model_dump(mode="json")
    restored = ResearchState.model_validate(payload)

    assert restored == state
    assert restored.model_dump(mode="json") == payload


@pytest.mark.parametrize(
    ("session_id", "question"),
    [("", "A question?"), ("session-1", "")],
)
def test_state_rejects_empty_identity_fields(
    session_id: str,
    question: str,
) -> None:
    with pytest.raises(ValidationError):
        ResearchState(session_id=session_id, original_question=question)


def test_state_rejects_iteration_above_maximum() -> None:
    with pytest.raises(ValidationError, match="iteration cannot exceed max_iterations"):
        ResearchState(
            session_id="session-1",
            original_question="A question?",
            iteration=4,
            max_iterations=3,
        )


@pytest.mark.parametrize(
    ("field_name", "item"),
    [
        ("sub_topics", sub_topic()),
        ("raw_findings", finding()),
        (
            "events",
            ResearchEvent(
                event_type="agent.started",
                source="researcher",
                message="Researcher started.",
            ),
        ),
        (
            "errors",
            ResearchError(
                error_type="search_timeout",
                source="web_search",
                message="Search timed out.",
            ),
        ),
    ],
)
def test_merge_appends_lists_without_mutating_original(
    field_name: str,
    item: object,
) -> None:
    state = ResearchState(session_id="session-1", original_question="A question?")

    merged = merge_research_state(state, {field_name: [item]})

    assert getattr(merged, field_name) == [item]
    assert getattr(state, field_name) == []


@pytest.mark.parametrize(
    ("field_name", "existing", "replacement"),
    [
        ("evaluated_sources", source("Existing"), source("Replacement")),
        ("verified_claims", claim("Existing"), claim("Replacement")),
    ],
)
def test_merge_replaces_the_canonical_snapshot_channels(
    field_name: str,
    existing: object,
    replacement: object,
) -> None:
    """These two channels carry a whole snapshot, so they replace.

    Appending them is what let one source or claim pile up once per research
    pass. The producer — Source Evaluator or Fact Checker — merges the new
    pass into the previous snapshot before it writes, so an update is always
    the complete canonical list and never a delta.
    """
    state = ResearchState(
        session_id="session-1",
        original_question="A question?",
        **{field_name: [existing]},
    )

    merged = merge_research_state(state, {field_name: [replacement]})

    assert getattr(merged, field_name) == [replacement]
    assert getattr(state, field_name) == [existing]


def test_merge_preserves_multi_item_append_order() -> None:
    state = ResearchState(
        session_id="session-1",
        original_question="A question?",
        sub_topics=[sub_topic("Existing")],
    )

    merged = merge_research_state(
        state,
        {"sub_topics": [sub_topic("First"), sub_topic("Second")]},
    )

    assert [topic.title for topic in merged.sub_topics] == [
        "Existing",
        "First",
        "Second",
    ]


def test_a_pre_contract_snapshot_loads_without_fabricated_provenance() -> None:
    """An old snapshot has no reads, and none are invented for its findings."""
    legacy = {
        "session_id": "session-1",
        "original_question": "How is enterprise AI adoption changing?",
        "raw_findings": [finding().model_dump(mode="json")],
        "report": "# Research report",
    }

    state = ResearchState.model_validate(legacy)

    assert state.quality_contract_version == LEGACY_QUALITY_CONTRACT_VERSION
    assert state.read_records == {}
    assert state.evidence_units == {}
    assert state.evidence_dispositions == []
    assert state.boundary_audits == {}
    # The finding keeps its URL and title; no read ID is minted for it.
    assert state.raw_findings[0].source_url == finding().source_url


def test_state_round_trips_the_evidence_registries_as_json() -> None:
    read = _read_record()
    unit = build_evidence_unit(
        read=read, locator="p-1", excerpt=PASSAGE, origin="researcher"
    )
    disposition = EvidenceDisposition(
        item_id="https://lab.example/other.pdf",
        stage="read-selection",
        reason="deferred_capacity",
        target_ids=["target-1"],
    )
    state = ResearchState(
        session_id="session-1",
        original_question="A question?",
        read_records={read.read_id: read},
        evidence_units={unit.evidence_id: unit},
        evidence_dispositions=[disposition],
        quality_contract_version=QUALITY_CONTRACT_VERSION,
    )

    payload = state.model_dump(mode="json")
    restored = ResearchState.model_validate(payload)

    assert restored == state
    assert restored.read_records[read.read_id].passages == read.passages
    assert restored.evidence_dispositions == [disposition]


def test_merge_folds_new_reads_into_the_registry() -> None:
    first = _read_record()
    second = _read_record(
        resolved_url="https://other.example/report",
        requested_url="https://other.example/report",
    )
    state = ResearchState(
        session_id="session-1",
        original_question="A question?",
        read_records={first.read_id: first},
    )

    merged = merge_research_state(
        state, {"read_records": {second.read_id: second}}
    )

    assert set(merged.read_records) == {first.read_id, second.read_id}
    assert merged.read_records[first.read_id] == first
    assert set(state.read_records) == {first.read_id}


def test_merge_refuses_one_read_id_carrying_two_bodies() -> None:
    """Last-write-wins would silently re-point every passage at that read."""
    stored = _read_record()
    conflicting = stored.model_copy(update={"content_sha256": "b" * 64})
    state = ResearchState(
        session_id="session-1",
        original_question="A question?",
        read_records={stored.read_id: stored},
    )

    with pytest.raises(EvidenceIdentityConflict):
        merge_research_state(
            state, {"read_records": {stored.read_id: conflicting}}
        )


def test_merge_folds_dispositions_and_manifests_by_id() -> None:
    disposition = EvidenceDisposition(
        item_id="read-1",
        stage="read-selection",
        reason="stale_for_target",
    )
    audit = build_boundary_audit(
        operation=READ_ADMISSION_OPERATION,
        job_id="job-7",
        agent_name="researcher",
        sequence=0,
        input_ids=("https://lab.example/queue",),
        packet_fingerprint="sha256:packet-1",
        configuration_fingerprint="sha256:config-1",
    )
    state = ResearchState(session_id="session-1", original_question="A question?")

    merged = merge_research_state(
        state,
        {
            "evidence_dispositions": [disposition],
            "boundary_audits": {audit.audit_id: audit},
        },
    )

    assert merged.evidence_dispositions == [disposition]
    assert merged.boundary_audits == {audit.audit_id: audit}
    assert state.evidence_dispositions == []
    assert state.boundary_audits == {}
    with pytest.raises(EvidenceIdentityConflict):
        merge_research_state(
            merged,
            {
                "evidence_dispositions": [
                    disposition.model_copy(update={"reason": "irrelevant"})
                ]
            },
        )


def test_merge_isolates_supplied_append_items() -> None:
    supplied = sub_topic("Supplied")
    state = ResearchState(session_id="session-1", original_question="A question?")

    merged = merge_research_state(state, {"sub_topics": [supplied]})
    merged.sub_topics[0].search_queries.append("new query")

    assert merged.sub_topics[0].search_queries == ["supplied evidence", "new query"]
    assert supplied.search_queries == ["supplied evidence"]


def test_merge_replaces_scalars_critique_report_and_memory() -> None:
    old_critique = critique(score=6).model_copy(update={"should_continue": True})
    new_critique = critique(score=9)
    new_memory = MemorySnapshot(
        similar_findings=[finding("A recalled finding.")],
        known_source_reputations={"example.com": 0.9},
        suggested_strategies=["Prefer primary sources."],
    )
    state = ResearchState(
        session_id="session-1",
        original_question="A question?",
        report="Old report",
        critique=old_critique,
        memory_context=MemorySnapshot(suggested_strategies=["Old strategy."]),
    )

    merged = merge_research_state(
        state,
        {
            "report": "New report",
            "critique": new_critique,
            "max_iterations": 5,
            "memory_context": new_memory,
        },
    )

    assert merged.report == "New report"
    assert merged.critique == new_critique
    assert merged.max_iterations == 5
    assert merged.memory_context == new_memory
    assert state.report == "Old report"
    assert state.critique == old_critique


def test_merge_validates_invalid_scalar_replacement() -> None:
    state = ResearchState(session_id="session-1", original_question="A question?")

    with pytest.raises(ValidationError):
        merge_research_state(state, {"max_iterations": 0})


def test_merge_deep_copies_unchanged_nested_values() -> None:
    state = ResearchState(
        session_id="session-1",
        original_question="A question?",
        memory_context=MemorySnapshot(similar_findings=[finding()]),
    )

    merged = merge_research_state(state, {"report": "Draft report"})
    merged.memory_context.similar_findings.append(finding("A new finding."))

    assert len(merged.memory_context.similar_findings) == 2
    assert len(state.memory_context.similar_findings) == 1


def test_merge_deep_copies_supplied_nested_values() -> None:
    replacement = MemorySnapshot(similar_findings=[finding()])
    state = ResearchState(session_id="session-1", original_question="A question?")

    merged = merge_research_state(state, {"memory_context": replacement})
    merged.memory_context.similar_findings.append(finding("A new finding."))

    assert len(merged.memory_context.similar_findings) == 2
    assert len(replacement.similar_findings) == 1


def test_merge_rejects_unknown_fields() -> None:
    state = ResearchState(session_id="session-1", original_question="A question?")

    with pytest.raises(ValueError, match="unknown ResearchState fields"):
        merge_research_state(state, {"unknown": "value"})


def test_merge_rejects_iteration_changes() -> None:
    state = ResearchState(session_id="session-1", original_question="A question?")

    with pytest.raises(ValueError, match="advance_research_iteration"):
        merge_research_state(state, {"iteration": 1})


def test_graph_iteration_advance_returns_a_new_state() -> None:
    state = ResearchState(
        session_id="session-1",
        original_question="A question?",
        iteration=1,
        max_iterations=3,
    )

    advanced = advance_research_iteration(state)

    assert advanced.iteration == 2
    assert state.iteration == 1


def evidence_target(
    target_id: str = "target-01",
    *,
    coverage_id: str = "topic-01",
    required_dimensions: list[str] | None = None,
    support_policy: str = "independent_pair",
    required: bool = True,
) -> EvidenceTarget:
    return EvidenceTarget(
        target_id=target_id,
        coverage_id=coverage_id,
        question="What does it cost?",
        required_dimensions=list(required_dimensions or ["cost"]),
        required=required,
        critical=True,
        support_policy=support_policy,
    )


def statement(
    *,
    statement_id: str = "S001",
    target_ids: list[str] | None = None,
    answered_dimensions: list[str] | None = None,
    mode: str = "settled",
    text: str = "It costs 40 EUR per tonne.",
) -> ReportStatement:
    return ReportStatement(
        statement_id=statement_id,
        text=text,
        mode=mode,
        answered_dimensions=list(answered_dimensions or ["cost"]),
        target_ids=list(target_ids or ["target-01"]),
    )


def composition(
    *,
    statements: list[ReportStatement] | None = None,
    claims: list[Claim] | None = None,
    sub_topics: list[SubTopic] | None = None,
) -> ReportComposition:
    rows = list(statements or [])
    return ReportComposition(
        question="What does it cost?",
        session_id="session-1",
        claims=list(claims or []),
        sub_topics=list(sub_topics or []),
        summary=[
            ReportPoint(text=row.text, statement=row) for row in rows
        ],
    )


def test_new_support_is_progress_even_before_a_verdict_changes() -> None:
    before = ResearchProgress(
        completed_target_ids=[],
        assessed_support_fingerprints=["a"],
        resolved_gap_ids=[],
        pending_work_ids=["b"],
        unresolved_major_gap_ids=["g1"],
        composition_fingerprint="old",
    )
    after = before.model_copy(
        update={
            "assessed_support_fingerprints": ["a", "b"],
            "pending_work_ids": [],
        }
    )
    assert progress_improved(before, after)
    assert not progress_improved(after, after)


def test_irrelevant_searches_and_pages_are_not_progress() -> None:
    before = ResearchProgress(
        completed_target_ids=["target-01"],
        assessed_support_fingerprints=["a"],
        resolved_gap_ids=[],
        pending_work_ids=[],
        unresolved_major_gap_ids=["g1"],
        composition_fingerprint="same",
    )
    more_work = before.model_copy(
        update={"pending_work_ids": ["https://lab.example/one", "read-2"]}
    )

    assert not progress_improved(before, more_work)
    assert not progress_improved(more_work, before)


def test_a_fixed_duplicated_paragraph_is_presentation_progress() -> None:
    before = ResearchProgress(composition_fingerprint="before-cleanup")
    after = before.model_copy(
        update={"composition_fingerprint": "after-cleanup"}
    )

    assert progress_improved(before, after)


def attributed_claim(text: str = "It costs 40 EUR per tonne.") -> Claim:
    """A claim with primary-source attribution and no independent pair."""
    return Claim(
        claim_id=claim_fingerprint(text),
        text=text,
        source_urls=["https://example.com/source"],
        verdict="insufficient_evidence",
        evidence_status="source_supported",
        confidence=0.7,
        evidence=["The ministry publishes this figure."],
        contradictions=[],
        verification_evidence=[],
    )


def test_a_settled_statement_answers_its_target() -> None:
    target = evidence_target()
    checked = claim("It costs 40 EUR per tonne.").model_copy(
        update={"target_ids": ["target-01"], "cluster_id": "cluster-01"}
    )
    row = statement().model_copy(
        update={"claim_cluster_ids": ["cluster-01"]}
    )
    state = ResearchState(
        session_id="session-1",
        original_question="A question?",
        composition=composition(statements=[row], claims=[checked]),
    )

    assert target_is_answered(state, target)


def test_a_statement_missing_a_required_dimension_does_not_answer() -> None:
    target = evidence_target(required_dimensions=["cost", "financing"])
    checked = claim("It costs 40 EUR per tonne.").model_copy(
        update={"target_ids": ["target-01"], "cluster_id": "cluster-01"}
    )
    row = statement(answered_dimensions=["cost"]).model_copy(
        update={"claim_cluster_ids": ["cluster-01"]}
    )
    state = ResearchState(
        session_id="session-1",
        original_question="A question?",
        composition=composition(statements=[row], claims=[checked]),
    )

    assert not target_is_answered(state, target)


def test_an_uncorroborated_statement_does_not_answer_an_independent_target() -> None:
    target = evidence_target(support_policy="independent_pair")
    checked = attributed_claim().model_copy(
        update={"target_ids": ["target-01"], "cluster_id": "cluster-01"}
    )
    row = statement(mode="attributed").model_copy(
        update={"claim_cluster_ids": ["cluster-01"]}
    )
    state = ResearchState(
        session_id="session-1",
        original_question="A question?",
        composition=composition(statements=[row], claims=[checked]),
    )

    assert not target_is_answered(state, target)


def test_primary_attribution_answers_a_target_that_asks_for_it() -> None:
    target = evidence_target(support_policy="primary_attribution")
    checked = attributed_claim().model_copy(
        update={"target_ids": ["target-01"], "cluster_id": "cluster-01"}
    )
    row = statement(mode="attributed").model_copy(
        update={"claim_cluster_ids": ["cluster-01"]}
    )
    state = ResearchState(
        session_id="session-1",
        original_question="A question?",
        composition=composition(statements=[row], claims=[checked]),
    )

    assert target_is_answered(state, target)


def test_a_raw_metadata_finding_completes_no_required_target() -> None:
    """A finding is not a reader statement: the target is still unanswered."""
    target = evidence_target()
    state = ResearchState(
        session_id="session-1",
        original_question="A question?",
        sub_topics=[
            sub_topic().model_copy(update={"evidence_targets": [target]})
        ],
        raw_findings=[finding("A publication date was recorded.")],
        composition=composition(
            statements=[statement(target_ids=["target-09"])],
            claims=[claim("Another target was answered.")],
        ),
    )

    assert not target_is_answered(state, target)
    assert unanswered_required_targets(state) == [target]


def test_an_answered_target_is_not_reported_as_unmet() -> None:
    answered = evidence_target("target-01")
    unmet = evidence_target("target-02", required_dimensions=["financing"])
    topic = sub_topic().model_copy(
        update={"evidence_targets": [answered, unmet]}
    )
    claim_one = claim("It costs 40 EUR per tonne.").model_copy(
        update={"target_ids": ["target-01"], "cluster_id": "cluster-01"}
    )
    state = ResearchState(
        session_id="session-1",
        original_question="A question?",
        sub_topics=[topic],
        composition=composition(
            statements=[
                statement(target_ids=["target-01"]).model_copy(
                    update={"claim_cluster_ids": ["cluster-01"]}
                )
            ],
            claims=[claim_one],
        ),
    )

    assert unanswered_required_targets(state) == [unmet]
    assert unanswered_required_targets(state, topic) == [unmet]
    assert unanswered_required_targets(state, sub_topic("Other")) == []


def test_an_optional_target_is_not_an_outstanding_obligation() -> None:
    optional = evidence_target("target-02", required=False)
    topic = sub_topic().model_copy(update={"evidence_targets": [optional]})
    state = ResearchState(
        session_id="session-1",
        original_question="A question?",
        sub_topics=[topic],
    )

    assert unanswered_required_targets(state) == []


def test_refinement_targets_are_persisted_and_replaced_per_pass() -> None:
    first = RefinementTarget(
        gap_id="gap-01",
        target_ids=["target-01"],
        action="acquire",
        problem="No cost data.",
    )
    second = RefinementTarget(
        target_ids=["target-02"],
        action="adjudicate",
        origin="unanswered_target",
        problem="The obligation was never adjudicated.",
    )
    state = ResearchState(
        session_id="session-1",
        original_question="A question?",
        refinement_targets=[first],
    )

    merged = merge_research_state(state, {"refinement_targets": [second]})

    assert merged.refinement_targets == [second]
    assert state.refinement_targets == [first]


def test_progress_history_appends_and_is_bounded_by_the_iteration_ceiling() -> None:
    state = ResearchState(
        session_id="session-1",
        original_question="A question?",
        max_iterations=2,
    )

    for index in range(5):
        state = merge_research_state(
            state,
            {
                "progress_history": [
                    ResearchProgress(composition_fingerprint=f"pass-{index}")
                ]
            },
        )

    assert [row.composition_fingerprint for row in state.progress_history] == [
        "pass-2",
        "pass-3",
        "pass-4",
    ]


def test_a_repair_stop_reason_round_trips_through_state() -> None:
    state = ResearchState(session_id="session-1", original_question="A question?")

    merged = merge_research_state(state, {"repair_stop_reason": "no_progress"})

    assert merged.repair_stop_reason == "no_progress"
    assert state.repair_stop_reason is None
    with pytest.raises(ValidationError):
        merge_research_state(state, {"repair_stop_reason": "gave_up"})


def test_graph_iteration_cannot_advance_past_maximum() -> None:
    state = ResearchState(
        session_id="session-1",
        original_question="A question?",
        iteration=3,
        max_iterations=3,
    )

    with pytest.raises(ValueError, match="max_iterations"):
        advance_research_iteration(state)
