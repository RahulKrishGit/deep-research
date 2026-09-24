"""Tests for shared research domain contracts."""

from datetime import datetime

import pytest
from pydantic import ValidationError

from deep_research.agents.identity import claim_fingerprint
from deep_research.utils.types import (
    INCOMPLETE_CONTENT_SHA256,
    MAX_CONSUMED_COVERAGE_IDS,
    MAX_CONSUMED_FINDING_FINGERPRINTS,
    ORIGINAL_QUESTION_OMISSION_REFERENCE,
    AnswerContract,
    BoundaryAudit,
    Claim,
    ClaimProvenance,
    Critique,
    EvidencePassage,
    EvidenceTarget,
    Finding,
    MemorySnapshot,
    ReadRecord,
    ResearchError,
    ResearchEvent,
    ResearchState,
    ScoredSource,
    SourceEvaluationStatus,
    SubTopic,
    counted_evidence_targets,
    merge_research_state,
)


def test_an_evidence_target_requires_its_obligation_and_support_policy() -> None:
    target = EvidenceTarget(
        target_id="target-1",
        coverage_id="topic-01",
        question="How much interconnection capacity was withheld in 2025?",
        required_dimensions=["fact", "time", "magnitude"],
        required=True,
        critical=True,
        support_policy="primary_attribution",
    )

    assert target.critical is True
    assert target.support_policy == "primary_attribution"

    with pytest.raises(ValidationError):
        EvidenceTarget(
            target_id="target-1",
            coverage_id="topic-01",
            question="How much?",
            required_dimensions=["fact"],
            support_policy="primary_attribution",
        )
    with pytest.raises(ValidationError):
        EvidenceTarget(
            target_id="target-1",
            coverage_id="topic-01",
            question="How much?",
            required_dimensions=[],
            required=True,
            critical=False,
            support_policy="primary_attribution",
        )
    with pytest.raises(ValidationError):
        EvidenceTarget(
            target_id="target-1",
            coverage_id="topic-01",
            question="How much?",
            required_dimensions=["fact"],
            required=True,
            critical=False,
            support_policy="probably fine",
        )


def test_an_answer_contract_freezes_the_scope_form_and_as_of_date() -> None:
    """The contract is the frozen half of Section 2.3.

    Every field is required, including the two that may hold nothing: a
    question with no stated geography still gets an explicit assumption
    rather than an empty field a later stage could read as "anywhere".
    """
    contract = AnswerContract(
        question="What are the current interconnection constraints?",
        scope_statement="Answered for the United States as of 2026-09-16.",
        geographic_scope="United States",
        as_of_date="2026-09-16",
        evidence_period_requirement=(
            "Latest available evidence as of 2026-09-16."
        ),
        assumptions=["The question does not name a jurisdiction."],
        answer_kind="constraints",
        requested_word_limit=None,
    )

    assert contract.answer_kind == "constraints"
    assert contract.as_of_date == "2026-09-16"
    assert contract.requested_word_limit is None

    with pytest.raises(ValidationError):
        AnswerContract(
            question="What are the current constraints?",
            scope_statement="Answered for the United States.",
            geographic_scope="United States",
            as_of_date="2026-09-16",
            evidence_period_requirement="Latest available evidence.",
            assumptions=[],
            answer_kind="maybe",
            requested_word_limit=None,
        )


def test_a_legacy_sub_topic_without_targets_still_loads() -> None:
    """Legacy plans load with an empty target list and require replanning.

    A snapshot written before the target contract carries no obligations, so
    nothing may be invented for it — but such a plan cannot be executed as
    if it were complete, and the empty list is what says so.
    """
    sub_topic = SubTopic(
        coverage_id="topic-01",
        title="Alpha",
        rationale="Alpha is load-bearing.",
        search_queries=["alpha 2025"],
        success_criteria=["A named source about Alpha."],
        priority=1,
    )

    assert sub_topic.evidence_targets == []

    target = EvidenceTarget(
        target_id="target-01-01",
        coverage_id="topic-01",
        question="What does Alpha measure?",
        required_dimensions=["fact", "time"],
        required=True,
        critical=True,
        support_policy="independent_pair",
    )
    stamped = SubTopic.model_validate(
        {**sub_topic.model_dump(), "evidence_targets": [target]}
    )

    assert stamped.evidence_targets == [target]


def test_a_sub_topic_carries_at_most_four_targets() -> None:
    def _target(index: int) -> EvidenceTarget:
        return EvidenceTarget(
            target_id=f"target-01-{index:02d}",
            coverage_id="topic-01",
            question=f"What does Alpha measure ({index})?",
            required_dimensions=["fact"],
            required=True,
            critical=False,
            support_policy="independent_pair",
        )

    with pytest.raises(ValidationError):
        SubTopic(
            coverage_id="topic-01",
            title="Alpha",
            rationale="Alpha is load-bearing.",
            search_queries=["alpha 2025"],
            success_criteria=["A named source about Alpha."],
            priority=1,
            evidence_targets=[_target(index) for index in range(1, 6)],
        )


def test_the_omission_reference_target_is_not_a_counted_target() -> None:
    """A reserved-reference target records an omission; it is not evidence.

    The planner uses it to say "the original question asks for something the
    plan does not yet cover". Counting it as an evidence target would let the
    plan pass a target count while the obligation it stands for is still
    unanswered.
    """
    omission = EvidenceTarget(
        target_id="target-01-01",
        coverage_id="topic-01",
        question=ORIGINAL_QUESTION_OMISSION_REFERENCE,
        required_dimensions=["original question coverage"],
        required=True,
        critical=True,
        support_policy="independent_pair",
    )
    real = EvidenceTarget(
        target_id="target-01-02",
        coverage_id="topic-01",
        question="What does Alpha measure?",
        required_dimensions=["fact"],
        required=True,
        critical=False,
        support_policy="independent_pair",
    )

    assert counted_evidence_targets([omission, real]) == [real]
    assert counted_evidence_targets([omission]) == []


def test_the_initial_target_inventory_is_immutable_under_later_updates() -> None:
    """Later planning may ADD a target and may never shrink the inventory.

    An update that names a subset is the dangerous case: replacement
    semantics would let a later pass drop a difficult critical target, which
    is exactly the smaller-denominator failure Section 2.3 forbids.
    """
    state = ResearchState(session_id="session-1", original_question="Why?")
    assert state.answer_contract is None
    assert state.initial_target_ids == []
    assert state.expanded_target_ids == []

    contract = AnswerContract(
        question="Why?",
        scope_statement="Answered for the United States as of 2026-09-16.",
        geographic_scope="United States",
        as_of_date="2026-09-16",
        evidence_period_requirement="Latest available evidence as of 2026-09-16.",
        assumptions=["The question names no geography."],
        answer_kind="explanation",
        requested_word_limit=None,
    )
    first = merge_research_state(
        state,
        {
            "answer_contract": contract,
            "initial_target_ids": ["target-01-01", "target-01-02"],
        },
    )

    assert first.answer_contract == contract
    assert first.initial_target_ids == ["target-01-01", "target-01-02"]

    added = merge_research_state(
        first,
        {"expanded_target_ids": ["target-03-01"]},
    )
    assert added.initial_target_ids == ["target-01-01", "target-01-02"]
    assert added.expanded_target_ids == ["target-03-01"]

    # A later update that names only one initial target adds nothing and
    # removes nothing: the inventory is a union, never a replacement.
    preserved = merge_research_state(
        added,
        {"initial_target_ids": ["target-01-01"]},
    )
    assert preserved.initial_target_ids == [
        "target-01-01",
        "target-01-02",
    ]
    assert preserved.expanded_target_ids == ["target-03-01"]

    # Stating an already-known id twice does not duplicate it.
    deduplicated = merge_research_state(
        preserved,
        {"expanded_target_ids": ["target-03-01", "target-04-01"]},
    )
    assert deduplicated.expanded_target_ids == [
        "target-03-01",
        "target-04-01",
    ]


def test_a_read_record_requires_a_full_hash_and_a_real_reader() -> None:
    values: dict[str, object] = {
        "read_id": "read-1",
        "requested_url": "https://lab.example/queue",
        "resolved_url": "https://lab.example/queue",
        "title": "Queue Study",
        "reader": "web_scraper",
        "retrieved_at": "2026-09-16T10:00:00+00:00",
        "content_sha256": "a" * 64,
        "extraction_complete": True,
        "passages": {"p-1": "Example Lab measured that capacity was withheld."},
        "origin_session_id": "session-1",
    }

    assert ReadRecord.model_validate(values).acquisition_kind == "network"

    for replacement in (
        {"content_sha256": ""},
        {"content_sha256": "a" * 63},
        {"content_sha256": "z" * 64},
        {"reader": "web_search"},
        {"retrieved_at": "2026-09-16T10:00:00"},
        {"passages": {}},
        {"origin_session_id": ""},
    ):
        with pytest.raises(ValidationError):
            ReadRecord.model_validate({**values, **replacement})


def test_a_read_record_keeps_extracted_passage_text_verbatim() -> None:
    """Passage text is evidence, so it is stored exactly as extracted."""
    passage = "Example Lab measured that   1,200 MW\nof capacity was withheld"
    record = ReadRecord(
        read_id="read-1",
        requested_url="https://lab.example/queue",
        resolved_url="https://lab.example/queue",
        title="Queue Study",
        reader="document_reader",
        retrieved_at="2026-09-16T10:00:00+00:00",
        content_sha256="a" * 64,
        extraction_complete=True,
        passages={"p-1": passage},
        origin_session_id="session-1",
    )

    assert record.passages["p-1"] == passage


def test_a_content_hash_and_a_completeness_flag_must_agree() -> None:
    """The partial-read escape hatch cannot be used to smuggle a digest.

    A complete read publishes the digest of its whole document; a read whose
    extraction was incomplete publishes the explicit non-digest marker, so no
    hash that would identify a document nobody fully read is ever persisted.
    """
    values: dict[str, object] = {
        "read_id": "read-1",
        "requested_url": "https://lab.example/queue.pdf",
        "resolved_url": "https://lab.example/queue.pdf",
        "title": "Queue Study",
        "reader": "document_reader",
        "retrieved_at": "2026-09-16T10:00:00+00:00",
        "content_sha256": "a" * 64,
        "extraction_complete": True,
        "passages": {"page-1-chunk-0": "Example Lab measured that capacity."},
        "origin_session_id": "session-1",
    }

    partial = ReadRecord.model_validate(
        {
            **values,
            "content_sha256": INCOMPLETE_CONTENT_SHA256,
            "extraction_complete": False,
        }
    )
    assert partial.content_sha256 == "incomplete"
    assert partial.extraction_complete is False

    for replacement in (
        # A partial extraction must not claim a document-identifying digest.
        {"extraction_complete": False},
        # Nor may a complete read hide behind the marker.
        {
            "content_sha256": INCOMPLETE_CONTENT_SHA256,
            "extraction_complete": True,
        },
    ):
        with pytest.raises(ValidationError):
            ReadRecord.model_validate({**values, **replacement})


def test_a_boundary_audit_requires_its_scalars_and_opens_every_id_list() -> None:
    values: dict[str, object] = {
        "audit_id": "audit-1",
        "job_id": "job-7",
        "agent_name": "researcher",
        "operation": "read_admission",
        "packet_fingerprint": "sha256:packet-1",
        "schema_version": "1",
        "configuration_fingerprint": "sha256:config-1",
        "status": "completed",
    }

    audit = BoundaryAudit.model_validate(values)

    assert audit.input_ids == []
    assert audit.deferred_ids == []
    assert audit.target_ids == []
    for replacement in (
        {"audit_id": ""},
        {"job_id": " "},
        {"operation": ""},
        {"packet_fingerprint": ""},
        {"configuration_fingerprint": ""},
        {"schema_version": ""},
        {"status": "finished"},
    ):
        with pytest.raises(ValidationError):
            BoundaryAudit.model_validate({**values, **replacement})


def unscored_source(*, status: SourceEvaluationStatus = "unscored_cap") -> ScoredSource:
    return ScoredSource(
        url="https://example.com/unscored",
        title="Unscored source",
        authority_score=None,
        recency_score=None,
        relevance_score=None,
        overall_score=None,
        rationale="This source was not scored.",
        evaluation_status=status,
    )


def scored_source(**overrides: object) -> ScoredSource:
    values = {
        "url": "https://example.com/source",
        "title": "Example source",
        "authority_score": 0.8,
        "recency_score": 0.7,
        "relevance_score": 0.9,
        "overall_score": 0.75,
        "rationale": "Relevant and independently supported.",
    }
    values.update(overrides)
    return ScoredSource.model_validate(values)


def critique(**overrides: object) -> Critique:
    values = {
        "score": 7,
        "gaps": [],
        "unsupported_claims": [],
        "recommended_queries": [],
        "should_continue": False,
        "rationale": "The report meets the threshold.",
    }
    values.update(overrides)
    return Critique.model_validate(values)


def test_domain_models_preserve_required_fields() -> None:
    topic = SubTopic(
        coverage_id="topic-01",
        title="Adoption",
        rationale="Measure current adoption patterns.",
        search_queries=["enterprise AI adoption 2026"],
        success_criteria=["Find two independent estimates."],
        priority=1,
    )
    finding = Finding(
        content="Adoption increased year over year.",
        source_url="not-validated-at-this-boundary",
        source_title="Industry survey",
        extracted_at="2026-07-25T12:00:00+00:00",
        confidence=0.8,
        related_sub_topic="Adoption",
    )
    claim = Claim(
        claim_id=claim_fingerprint("Adoption increased year over year."),
        text="Adoption increased year over year.",
        source_urls=["https://example.com/a", "https://example.org/b"],
        verdict="verified",
        evidence_status="verified_pair",
        confidence=0.9,
        evidence=["Two independent surveys report an increase."],
        contradictions=["One regional survey reported flat adoption."],
        verification_evidence=[
            EvidencePassage(
                source_url="https://independent.org/survey",
                source_title="Independent survey",
                locator="p. 3",
                excerpt="Two independent surveys report an increase.",
                stance="supports",
            )
        ],
    )
    memory = MemorySnapshot(
        similar_findings=[finding],
        known_source_reputations={"example.com": 0.85},
        suggested_strategies=["Compare independent surveys."],
    )

    assert topic.priority == 1
    assert finding.source_url == "not-validated-at-this-boundary"
    assert claim.contradictions == ["One regional survey reported flat adoption."]
    assert memory.similar_findings == [finding]


def test_a_claim_identity_is_the_canonical_fingerprint_of_its_text() -> None:
    """Task 5, Minor 1: a ``Claim`` fixture must not invent an id.

    ``merge_claim_snapshot`` recomputes the fingerprint from ``text``, so an
    arbitrary id passes every merge test while guarding nothing about the
    public identity field. Fixtures therefore derive it, and this asserts the
    contract they derive it against.
    """
    text = "Adoption increased year over year."
    fixture = Claim(
        claim_id=claim_fingerprint(text),
        text=text,
        source_urls=["https://example.com/a"],
        verdict="verified",
        evidence_status="verified_pair",
        confidence=0.9,
        evidence=["An independent survey reports an increase."],
        contradictions=[],
        verification_evidence=[
            EvidencePassage(
                source_url="https://independent.org/survey",
                source_title="Independent survey",
                locator="p. 3",
                excerpt="An independent survey reports an increase.",
                stance="supports",
            )
        ],
    )

    assert fixture.claim_id == claim_fingerprint(fixture.text)
    # Provenance is additive: a fixture that carries none claims none, which
    # suppresses nothing and therefore costs extra work rather than skipping
    # evidence.
    assert fixture.consumed_finding_fingerprints == []
    assert fixture.consumed_coverage_ids == []


def test_claim_provenance_is_bounded() -> None:
    """Both provenance lists are bounded, so a claim cannot grow forever."""
    with pytest.raises(ValidationError):
        Claim(
            claim_id="fingerprint",
            text="A claim.",
            source_urls=["https://example.com/a"],
            verdict="insufficient_evidence",
            evidence_status="source_supported",
            confidence=0.0,
            evidence=[],
            contradictions=[],
            verification_evidence=[],
            consumed_finding_fingerprints=[
                f"fingerprint-{index}"
                for index in range(MAX_CONSUMED_FINDING_FINGERPRINTS + 1)
            ],
        )

    with pytest.raises(ValidationError):
        Claim(
            claim_id="fingerprint",
            text="A claim.",
            source_urls=["https://example.com/a"],
            verdict="insufficient_evidence",
            evidence_status="source_supported",
            confidence=0.0,
            evidence=[],
            contradictions=[],
            verification_evidence=[],
            consumed_coverage_ids=[
                f"topic-{index}"
                for index in range(MAX_CONSUMED_COVERAGE_IDS + 1)
            ],
        )


def _claim_snapshot() -> dict[str, object]:
    """One persisted claim record, in the shape a snapshot serialises to."""
    text = "Logical error rates fell below break-even in 2025."
    return {
        "claim_id": claim_fingerprint(text),
        "text": text,
        "source_urls": ["https://example.com/a"],
        "verdict": "insufficient_evidence",
        "confidence": 0.0,
        "evidence": [],
        "contradictions": [],
        "verification_evidence": [],
        "consumed_finding_fingerprints": [],
        "consumed_coverage_ids": [],
    }


def test_a_claim_snapshot_without_an_insufficient_reason_still_validates() -> None:
    """The new field is additive, so a snapshot written before it validates.

    ``Claim`` is a shared contract read back from persisted state, so a field
    added for one agent's audit cannot make an older record unreadable. The
    omission has to mean the same thing it means on the record: no reason was
    recorded, which is not the same as a reason of "unknown".
    """
    claim = Claim.model_validate(_claim_snapshot())

    assert claim.insufficient_reason is None


def test_a_claim_snapshot_keeps_an_unenumerated_insufficient_reason() -> None:
    """The field is a bounded string, not a closed enumeration.

    ``Claim`` must not import the fact checker's ``INSUFFICIENT_REASONS`` to
    constrain this value: the contract layer cannot depend on an agent, and a
    reason coined by a later release than the reader's would otherwise turn a
    readable snapshot into a validation failure.
    """
    snapshot = {**_claim_snapshot(), "insufficient_reason": "a_later_reason"}

    claim = Claim.model_validate(snapshot)

    assert claim.insufficient_reason == "a_later_reason"


@pytest.mark.parametrize("value", [-0.01, 1.01])
def test_unit_scores_reject_out_of_range_values(value: float) -> None:
    with pytest.raises(ValidationError):
        scored_source(authority_score=value)


@pytest.mark.parametrize("value", [0, 11])
def test_critic_score_rejects_out_of_range_values(value: int) -> None:
    with pytest.raises(ValidationError):
        critique(score=value)


def test_finding_rejects_timezone_naive_timestamp() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        Finding(
            content="A finding.",
            source_url="https://example.com/source",
            source_title="Example source",
            extracted_at="2026-07-25T12:00:00",
            confidence=0.8,
            related_sub_topic="Adoption",
        )


def test_missing_required_field_fails_validation() -> None:
    with pytest.raises(ValidationError):
        SubTopic.model_validate(
            {
                "title": "Adoption",
                "rationale": "Measure adoption.",
                "search_queries": ["enterprise adoption"],
                "success_criteria": ["Find two estimates."],
            }
        )


def test_domain_models_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        scored_source(undocumented_score=0.5)


def test_research_event_serializes_and_round_trips() -> None:
    event = ResearchEvent(
        event_type="agent.started",
        source="planner",
        message="Planner started.",
        timestamp="2026-07-25T12:00:00+00:00",
        metadata={
            "iteration": 0,
            "queries": ["enterprise AI adoption"],
            "counts": {"sub_topics": 3},
        },
    )
    payload = event.model_dump(mode="json")

    assert payload == {
        "event_type": "agent.started",
        "source": "planner",
        "message": "Planner started.",
        "timestamp": "2026-07-25T12:00:00+00:00",
        "metadata": {
            "iteration": 0,
            "queries": ["enterprise AI adoption"],
            "counts": {"sub_topics": 3},
        },
    }
    assert ResearchEvent.model_validate(payload) == event


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_research_event_rejects_nested_non_finite_metadata(value: float) -> None:
    with pytest.raises(ValidationError, match="finite"):
        ResearchEvent(
            event_type="agent.completed",
            source="researcher",
            message="Researcher completed.",
            metadata={"results": [{"score": value}]},
        )


def test_research_error_serializes_and_round_trips() -> None:
    error = ResearchError(
        error_type="search_timeout",
        source="web_search",
        message="The search provider timed out.",
        recoverable=True,
        timestamp="2026-07-25T12:01:00Z",
        details={"retry_count": 2, "provider": "tavily"},
    )
    payload = error.model_dump(mode="json")
    assert payload["recoverable"] is True
    assert payload["timestamp"] == "2026-07-25T12:01:00Z"
    assert payload["details"] == {"retry_count": 2, "provider": "tavily"}
    assert ResearchError.model_validate(payload) == error


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_research_error_rejects_nested_non_finite_details(value: float) -> None:
    with pytest.raises(ValidationError, match="finite"):
        ResearchError(
            error_type="invalid_score",
            source="evaluator",
            message="Evaluator returned an invalid score.",
            details={"attempts": [{"scores": [0.5, value]}]},
        )


def test_event_and_error_defaults_are_json_safe_and_timezone_aware() -> None:
    event = ResearchEvent(
        event_type="session.started",
        source="orchestrator",
        message="Research session started.",
    )
    error = ResearchError(
        error_type="trace_failure",
        source="langsmith",
        message="Tracing failed; continuing locally.",
    )
    assert datetime.fromisoformat(event.timestamp).utcoffset() is not None
    assert datetime.fromisoformat(error.timestamp).utcoffset() is not None
    assert event.metadata == {}
    assert error.details == {}
    assert error.recoverable is True


def test_scored_source_defaults_to_not_low_confidence() -> None:
    source = ScoredSource(
        url="https://example.org/a",
        title="A",
        authority_score=0.8,
        recency_score=0.7,
        relevance_score=0.9,
        overall_score=0.76,
        rationale="Peer-reviewed and corroborated.",
    )

    assert source.low_confidence is False


def test_scored_source_records_an_explicit_low_confidence_flag() -> None:
    source = ScoredSource(
        url="https://example.org/b",
        title="B",
        authority_score=0.1,
        recency_score=0.0,
        relevance_score=0.2,
        overall_score=0.095,
        rationale="Anonymous blog with no corroboration.",
        low_confidence=True,
    )

    assert source.low_confidence is True


def test_unscored_source_accepts_null_quality_scores_and_explicit_status() -> None:
    source = unscored_source(status="unscored_cap")

    assert source.overall_score is None
    assert source.authority_score is None
    assert source.recency_score is None
    assert source.relevance_score is None
    assert source.evaluation_status == "unscored_cap"


def test_a_relays_own_date_never_prints_or_orders_as_the_issuers_release() -> (
    None
):
    """``release`` and ``as_text`` read only what was recorded as a release.

    ``release_date`` is what the attributed issuer released the figure on;
    ``statement_date`` is only the date the citing page itself carries. A
    relay that names no release must not have its own article date printed
    as if it were one, nor used to order or label revisions of a series.
    """
    relay = ClaimProvenance(attributed_issuer="EIA", statement_date="2025-03-13")
    assert relay.release == ""
    assert relay.as_text() == "EIA, stated 2025-03-13"

    released = ClaimProvenance(
        attributed_issuer="EIA",
        statement_date="2025-03-13",
        release_date="2025-03-12",
    )
    assert released.release == "2025-03-12"
    assert released.as_text() == "EIA, released 2025-03-12"

    silent = ClaimProvenance(attributed_issuer="EIA")
    assert silent.release == ""
    assert silent.as_text() == "EIA"


import pytest
from pydantic import ValidationError

from deep_research.utils.types import FindingFigure
from tests.evidence_fakes import figure, make_finding, make_read, make_target


def test_a_finding_carries_its_snippet_read_locator_and_figures() -> None:
    read = make_read()
    finding = make_finding(
        read,
        "Generators added 10.4 gigawatts (GW) of new battery storage capacity in 2024,",
        figures=[figure("10.4", "gigawatts", "2024", "actual")],
    )
    assert (finding.read_id, finding.locator) == (read.read_id, "page-1-chunk-0")
    assert finding.figures[0] == FindingFigure(
        value="10.4", unit="gigawatts", period="2024", kind="actual"
    )


def test_blank_evidence_fields_are_absent() -> None:
    finding = make_finding(make_read(), "Generators added 10.4 gigawatts").model_copy(
        update={"snippet": " ", "read_id": "", "locator": "  "}
    )
    rebuilt = type(finding).model_validate(finding.model_dump())
    assert (rebuilt.snippet, rebuilt.read_id, rebuilt.locator) == (None, None, None)


def test_a_figure_needs_a_value_and_a_unit() -> None:
    with pytest.raises(ValidationError):
        FindingFigure(value="", unit="GW")
    with pytest.raises(ValidationError):
        FindingFigure(value="10.4", unit="")


def test_a_target_carries_structured_fields() -> None:
    target = make_target(organisation="U.S. Energy Information Administration")
    assert (target.unit_dimension, target.period, target.kind) == ("power", "2024", "actual")
    assert target.organisation == "U.S. Energy Information Administration"
    with pytest.raises(ValidationError):
        make_target(unit_dimension="volts")


from deep_research.utils.types import (
    FigureContext,
    FigureResult,
    FindingVerification,
    ResearchState,
    merge_research_state,
)


def _context(**overrides: object) -> FigureContext:
    fields = dict(period="2024", scope=None, attribution="own",
                  organisation="U.S. Energy Information Administration", kind="actual")
    fields.update(overrides)
    return FigureContext(**fields)


def test_a_kept_figure_carries_its_context() -> None:
    with pytest.raises(ValidationError):
        FigureResult(figure=figure("10.4", "GW"), matched=True)
    dropped = FigureResult(figure=figure("10.4", "GW"), matched=True,
                           dropped_reason="evidence_not_on_page")
    assert not dropped.kept


def test_status_and_drop_reason_agree() -> None:
    with pytest.raises(ValidationError):
        FindingVerification(status="dropped")
    with pytest.raises(ValidationError):
        FindingVerification(status="verified", dropped_reason="snippet_not_on_page")


def test_verified_means_every_figure_confirmed() -> None:
    corrected = FigureResult(figure=figure("18.9", "GW"), matched=True,
                             context=_context(scope="all segments"), corrected=True)
    with pytest.raises(ValidationError):
        FindingVerification(status="verified", figure_results=[corrected])
    assert FindingVerification(status="verified_corrected", figure_results=[corrected])


def test_a_verified_finding_keeps_at_least_one_figure() -> None:
    dropped = FigureResult(figure=figure("10.4", "GW"), matched=False,
                           dropped_reason="figure_not_in_evidence")
    with pytest.raises(ValidationError):
        FindingVerification(status="verified_corrected", figure_results=[dropped])


def test_verified_findings_are_replaced_not_appended() -> None:
    read = make_read()
    first = make_finding(read, "Generators added 10.4 gigawatts")
    second = make_finding(read, "operators report plans to add 19.6 GW")
    state = ResearchState(session_id="s", original_question="q")
    state = merge_research_state(state, {"verified_findings": [first]})
    state = merge_research_state(state, {"verified_findings": [first, second]})
    assert state.verified_findings == [first, second]


from deep_research.utils.types import FactRow, NotFoundTarget, ReportComposition


def test_a_fact_row_and_a_not_found_target_validate() -> None:
    row = FactRow(row_id="K001", organisation="U.S. Energy Information Administration",
                  attribution="own", measure="battery storage power capacity added",
                  period="2024", value="10.4 GW", kind="actual", finding_id="f1")
    assert row.earlier == [] and row.duplicate_finding_ids == []
    assert NotFoundTarget(target_id="topic-02-target-01", question="q").searched is False


def test_a_composition_carries_fact_rows_not_found_and_labels() -> None:
    composition = ReportComposition(question="q", session_id="s")
    assert (composition.fact_rows, composition.not_found, composition.finding_labels) == ([], [], {})
