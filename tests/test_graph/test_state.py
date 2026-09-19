"""Tests for the graph channel, the halt discipline, and routing."""

from __future__ import annotations

import pytest

from deep_research.agents.critic import fallback_critique
from deep_research.agents.evidence import (
    READ_ADMISSION_OPERATION,
    build_boundary_audit,
    build_evidence_unit,
    build_read_record,
    require_boundary_manifest,
)
from deep_research.graph.state import (
    DEFAULT_MAX_ITERATIONS,
    FINALIZE_NODE,
    GRAPH_ROUTES,
    GRAPH_STATUSES,
    HALTING_ERROR_TYPES,
    NODE_NAMES,
    ROUTE_END,
    ROUTE_FINALIZE,
    ROUTE_REFINE,
    dump_state,
    graph_quality_status,
    graph_recursion_limit,
    graph_route,
    graph_status,
    initial_graph_state,
    is_halted,
    load_state,
    pending_repair_work,
    progress_snapshot,
    repair_stop_reason,
)
from deep_research.utils.types import (
    LEGACY_QUALITY_CONTRACT_VERSION,
    QUALITY_CONTRACT_VERSION,
    QUALITY_STATUS_ACCEPTED,
    QUALITY_STATUS_PARTIAL,
    REPAIR_STOP_REASONS,
    AcquisitionState,
    CandidateRecord,
    Critique,
    CritiqueGap,
    EvidenceTarget,
    MemorySnapshot,
    ResearchError,
    ResearchProgress,
    SubTopic,
    progress_improved,
)
from tests.graph_fakes import (
    fake_critique,
    fake_failed_critique,
    fake_quality,
    fake_research_state,
    fake_sub_topic,
    halting_error,
)

QUEUE_TEXT = (
    "Queue Study. Example Lab measured that 1,200 MW of interconnection "
    "capacity was withheld in 2025."
)
QUEUE_PASSAGE = (
    "Example Lab measured that 1,200 MW of interconnection capacity was "
    "withheld"
)


def test_the_initial_channel_carries_the_question_and_the_budget() -> None:
    channel = initial_graph_state(
        session_id="session-1",
        question="  How mature is quantum error correction?  ",
        max_iterations=2,
        memory_context=MemorySnapshot(suggested_strategies=["start broad"]),
    )
    state = load_state(channel)

    assert set(channel) == {"state"}
    assert state.session_id == "session-1"
    assert state.original_question == "How mature is quantum error correction?"
    assert state.max_iterations == 2
    assert state.iteration == 0
    assert state.memory_context.suggested_strategies == ["start broad"]


def test_a_new_session_stamps_the_current_evidence_contract() -> None:
    state = load_state(initial_graph_state(session_id="session-1", question="Why?"))

    assert state.quality_contract_version == QUALITY_CONTRACT_VERSION
    assert state.read_records == {}
    assert state.evidence_units == {}
    assert state.evidence_dispositions == []
    assert state.boundary_audits == {}


def test_a_pre_contract_snapshot_loads_as_legacy_not_as_provenance() -> None:
    """An old channel has no read registry and must not be given one."""
    channel = initial_graph_state(session_id="session-1", question="Why?")
    del channel["state"]["quality_contract_version"]
    del channel["state"]["read_records"]
    del channel["state"]["evidence_units"]
    del channel["state"]["evidence_dispositions"]
    del channel["state"]["boundary_audits"]

    state = load_state(channel)

    assert state.quality_contract_version == LEGACY_QUALITY_CONTRACT_VERSION
    assert state.read_records == {}
    assert state.boundary_audits == {}


def test_the_channel_round_trips_reads_and_boundary_manifests() -> None:
    read = build_read_record(
        session_id="session-1",
        reader="web_scraper",
        requested_url="https://lab.example/queue",
        resolved_url="https://lab.example/queue",
        title="Queue Study",
        retrieved_at="2026-09-16T10:00:00+00:00",
        text=QUEUE_TEXT,
        passages={"p-1": QUEUE_PASSAGE},
    )
    unit = build_evidence_unit(
        read=read, locator="p-1", excerpt=QUEUE_PASSAGE, origin="researcher"
    )
    audit = build_boundary_audit(
        operation=READ_ADMISSION_OPERATION,
        job_id="job-7",
        agent_name="researcher",
        sequence=0,
        input_ids=(read.requested_url,),
        accepted_ids=(read.read_id,),
        packet_fingerprint="sha256:packet-1",
        configuration_fingerprint="sha256:config-1",
    )
    state = fake_research_state(
        quality_contract_version=QUALITY_CONTRACT_VERSION,
        read_records={read.read_id: read},
        evidence_units={unit.evidence_id: unit},
        boundary_audits={audit.audit_id: audit},
    )

    channel = dump_state(state)

    assert isinstance(channel["state"]["read_records"], dict)
    assert isinstance(channel["state"]["read_records"][read.read_id], dict)
    assert isinstance(channel["state"]["boundary_audits"][audit.audit_id], dict)
    reloaded = load_state(channel)
    assert reloaded.read_records == {read.read_id: read}
    assert reloaded.evidence_units == {unit.evidence_id: unit}
    assert reloaded.boundary_audits == {audit.audit_id: audit}
    # The manifest a replay looks for is resolvable after the round trip.
    assert require_boundary_manifest(reloaded.boundary_audits, audit.audit_id) == audit


def test_the_initial_channel_defaults_to_an_empty_memory_snapshot() -> None:
    state = load_state(
        initial_graph_state(session_id="session-1", question="Why?")
    )

    assert state.memory_context == MemorySnapshot()
    assert state.max_iterations == DEFAULT_MAX_ITERATIONS


def test_the_channel_round_trips_a_populated_state_as_plain_json() -> None:
    state = fake_research_state(
        sub_topics=[
            SubTopic(
                coverage_id="topic-01",
                title="Error correction",
                rationale="It is the bottleneck.",
                search_queries=["qec 2025"],
                success_criteria=["a logical error rate is quoted"],
                priority=1,
            )
        ],
        report="# Research report",
        critique=fake_critique(should_continue=False, score=8),
    )

    channel = dump_state(state)

    assert isinstance(channel["state"], dict)
    assert isinstance(channel["state"]["sub_topics"], list)
    assert isinstance(channel["state"]["sub_topics"][0], dict)
    assert load_state(channel) == state


def test_only_enumerated_error_types_halt_a_run() -> None:
    survivable = ResearchError(
        error_type="critic_review_provider_error",
        source="agent.critic",
        message="The model provider failed while the report was reviewed.",
        recoverable=False,
    )

    assert not is_halted(fake_research_state())
    assert not is_halted(fake_research_state(errors=[survivable]))
    assert is_halted(fake_research_state(errors=[halting_error()]))


def test_a_halted_run_ends_whatever_the_critic_recommended() -> None:
    state = fake_research_state(
        errors=[halting_error()],
        critique=fake_critique(should_continue=True),
    )

    assert graph_route(state) == (ROUTE_END, "halted")
    assert graph_status(state) == "failed"


def test_a_run_with_no_critique_ends_as_incomplete() -> None:
    assert graph_route(fake_research_state()) == (
        ROUTE_FINALIZE,
        "missing_critique",
    )
    assert graph_status(fake_research_state()) == "incomplete"


def test_a_satisfied_critic_ends_the_run() -> None:
    state = fake_research_state(
        critique=fake_critique(should_continue=False, score=9),
        quality=fake_quality(),
    )

    assert graph_route(state) == (ROUTE_FINALIZE, "critique_satisfied")
    assert graph_status(state) == "completed"


def test_an_unsatisfied_critic_buys_a_refinement_while_budget_remains() -> None:
    state = fake_research_state(
        critique=fake_critique(should_continue=True),
        iteration=1,
        max_iterations=3,
    )

    assert graph_route(state) == (ROUTE_REFINE, "refinement_requested")


def test_the_iteration_bound_beats_the_critics_recommendation() -> None:
    state = fake_research_state(
        critique=fake_critique(should_continue=True),
        iteration=2,
        max_iterations=2,
    )

    assert graph_route(state) == (ROUTE_FINALIZE, "max_iterations_reached")
    assert graph_status(state) == "max_iterations"


def test_every_routing_reason_is_enumerated_and_maps_to_a_status() -> None:
    reasons = {
        graph_route(state)[1]
        for state in (
            fake_research_state(errors=[halting_error()]),
            fake_research_state(),
            fake_research_state(critique=fake_critique(should_continue=False)),
            fake_research_state(
                critique=fake_critique(should_continue=True),
                max_iterations=2,
            ),
            fake_research_state(
                critique=fake_critique(should_continue=True),
                iteration=1,
                max_iterations=1,
            ),
            fake_research_state(
                critique=fake_critique(should_continue=False),
                quality=fake_quality(hard_failures=["duplicate_claims"]),
                max_iterations=3,
            ),
            # Task 8's reason: a review that never validated.
            fake_research_state(critique=fake_failed_critique()),
            # Task 9's reasons: a repair loop that stopped for its own
            # recorded cause while the report still wanted another pass.
            fake_research_state(
                critique=fake_critique(should_continue=True),
                repair_stop_reason="no_progress",
            ),
            fake_research_state(
                critique=fake_critique(should_continue=True),
                repair_stop_reason="pending_capacity",
            ),
            fake_research_state(
                critique=fake_critique(should_continue=True),
                repair_stop_reason="evidence_unavailable",
            ),
            fake_research_state(
                critique=fake_critique(should_continue=True),
                repair_stop_reason="provider_failure",
            ),
        )
    }

    assert reasons == set(GRAPH_ROUTES)
    for reason in GRAPH_ROUTES:
        assert GRAPH_ROUTES[reason].strip()


def test_a_failed_review_is_not_an_acceptance() -> None:
    """The Critical 2 fix: a failed review is never published as accepted.

    The failed critique carries every signal a clean acceptance carries —
    ``should_continue=False``, an empty gap list, the score floor — which is
    exactly why ``should_continue`` alone used to route it to
    ``critique_satisfied`` and stamp the report ``accepted``. ``review_status``
    is what tells the two apart, and it is now read here.
    """
    accepted = fake_research_state(
        critique=fake_critique(should_continue=False, score=9),
        quality=fake_quality(),
    )
    failed = fake_research_state(
        critique=fake_failed_critique(),
        quality=fake_quality(),
    )

    # The two states carry the signals the old router read identically.
    assert failed.critique.should_continue == accepted.critique.should_continue
    assert failed.critique.gaps == accepted.critique.gaps

    assert graph_route(accepted) == (ROUTE_FINALIZE, "critique_satisfied")
    assert graph_quality_status(accepted) == QUALITY_STATUS_ACCEPTED

    assert graph_route(failed) == (ROUTE_FINALIZE, "critique_failed")
    assert graph_status(failed) == "failed"
    assert graph_quality_status(failed) == QUALITY_STATUS_PARTIAL


def test_a_failed_review_is_named_even_on_the_last_iteration() -> None:
    """The honest cause beats the exhausted bound.

    Both reasons end the run without acceptance, but "the budget ran out"
    would describe a report that was in fact never judged, and the recorded
    reason is what a reader uses to tell those apart.
    """
    failed = fake_research_state(
        critique=fake_failed_critique(),
        quality=fake_quality(),
        iteration=3,
        max_iterations=3,
    )

    assert graph_route(failed) == (ROUTE_FINALIZE, "critique_failed")
    assert graph_status(failed) == "failed"
    assert graph_quality_status(failed) == QUALITY_STATUS_PARTIAL


def test_a_provider_outage_is_not_an_acceptance() -> None:
    """Critical 1: the outage fallback is a review that never happened.

    Its own signals — the score floor, the empty gap list,
    ``should_continue=False`` — are byte-identical to a clean acceptance, so a
    terminal default of ``reviewed`` turned an outage into an accepted, and then
    a remembered, report. The fallback now says ``failed`` for exactly that
    reason, while the missing-report fallback stays ``reviewed`` because it
    records a real gap and is allowed to buy another pass.
    """
    outage, _ = fallback_critique(
        reason="provider_unavailable", iteration=0, max_iterations=3
    )
    missing_report, _ = fallback_critique(
        reason="missing_report", iteration=0, max_iterations=3
    )

    assert outage.review_status == "failed"
    assert outage.should_continue is False
    assert missing_report.review_status == "reviewed"
    assert missing_report.should_continue is True

    state = fake_research_state(critique=outage, quality=fake_quality())

    assert graph_route(state) == (ROUTE_FINALIZE, "critique_failed")
    assert graph_status(state) == "failed"
    assert graph_quality_status(state) == QUALITY_STATUS_PARTIAL
    assert not is_halted(state)


def test_a_hard_quality_failure_forces_a_pass_the_critic_did_not_ask_for() -> None:
    """Step 2: the deterministic gate outranks the model's own acceptance.

    A report the Critic scored 9 can still carry duplicate claims or an
    uncited settled point. While the budget remains, the gate sends it back
    rather than publishing it.
    """
    state = fake_research_state(
        critique=fake_critique(should_continue=False, score=9),
        quality=fake_quality(hard_failures=["duplicate_claims"]),
        iteration=0,
        max_iterations=3,
    )

    assert graph_route(state) == (ROUTE_REFINE, "quality_gate_failed")


def test_a_hard_quality_failure_with_no_budget_left_is_partial_not_accepted() -> None:
    state = fake_research_state(
        critique=fake_critique(should_continue=False, score=9),
        quality=fake_quality(hard_failures=["uncited_settled_points"]),
        iteration=2,
        max_iterations=2,
    )

    assert graph_route(state) == (ROUTE_FINALIZE, "max_iterations_reached")
    assert graph_quality_status(state) == QUALITY_STATUS_PARTIAL
    assert graph_status(state) == "max_iterations"


def test_only_a_clean_gate_the_critic_accepted_is_accepted() -> None:
    state = fake_research_state(
        critique=fake_critique(should_continue=False, score=9),
        quality=fake_quality(hard_failures=[]),
        iteration=0,
        max_iterations=3,
    )

    assert graph_route(state) == (ROUTE_FINALIZE, "critique_satisfied")
    assert graph_quality_status(state) == QUALITY_STATUS_ACCEPTED


def test_a_run_no_quality_pass_judged_is_never_accepted() -> None:
    state = fake_research_state(
        critique=fake_critique(should_continue=False, score=9)
    )

    assert state.quality is None
    assert graph_quality_status(state) == QUALITY_STATUS_PARTIAL


def test_a_critic_still_asking_for_a_pass_is_never_accepted() -> None:
    """A clean gate cannot accept a report the reviewer sent back."""
    state = fake_research_state(
        critique=fake_critique(should_continue=True, score=4),
        quality=fake_quality(hard_failures=[]),
        iteration=2,
        max_iterations=2,
    )

    assert graph_route(state) == (ROUTE_FINALIZE, "max_iterations_reached")
    assert graph_quality_status(state) == QUALITY_STATUS_PARTIAL


def test_the_status_vocabulary_is_closed() -> None:
    assert set(GRAPH_STATUSES) == {
        "completed",
        "max_iterations",
        "incomplete",
        "failed",
    }


def test_the_recursion_limit_covers_every_planned_pass() -> None:
    assert graph_recursion_limit(1) == 2 * len(NODE_NAMES) + 10
    assert graph_recursion_limit(3) > graph_recursion_limit(1)
    with pytest.raises(ValueError, match="max_iterations"):
        graph_recursion_limit(0)


def test_the_node_names_are_unique_and_ordered() -> None:
    assert len(set(NODE_NAMES)) == len(NODE_NAMES)
    assert NODE_NAMES[0] == "planner"
    # The refinement hop is no longer last: the terminal finalizer publishes
    # both artifacts and must be the last node the graph runs.
    assert NODE_NAMES[-2] == "refine"
    assert NODE_NAMES[-1] == FINALIZE_NODE == "finalize_report"


def test_the_halting_error_types_are_all_graph_owned() -> None:
    assert HALTING_ERROR_TYPES
    assert all(name.startswith("graph_") for name in HALTING_ERROR_TYPES)


def test_the_request_attempt_limit_error_type_halts_a_run() -> None:
    """A spent request budget stops the run; it is never merely recoverable."""
    error_type = "graph_request_attempt_limit_exceeded"

    assert error_type in HALTING_ERROR_TYPES

    state = fake_research_state(
        errors=[
            ResearchError(
                error_type=error_type,
                source="graph.researcher",
                message=(
                    "The request attempt budget for this run was exhausted, "
                    "so the research run stopped."
                ),
                recoverable=False,
            )
        ]
    )

    assert is_halted(state)
    assert graph_route(state) == (ROUTE_END, "halted")
    assert graph_status(state) == "failed"


# --- Task 9: repair progress and repair stop reasons ------------------------


def _unmet_topic(target_id: str = "target-01") -> SubTopic:
    return fake_sub_topic().model_copy(
        update={
            "evidence_targets": [
                EvidenceTarget(
                    target_id=target_id,
                    coverage_id="topic-01",
                    question="What does it cost?",
                    required_dimensions=["cost"],
                    required=True,
                    critical=True,
                    support_policy="independent_pair",
                )
            ]
        }
    )


def _deferred_state(
    *,
    remaining_calls: int,
    status: str = "deferred",
) -> AcquisitionState:
    url = "https://lab.example/queue"
    return AcquisitionState(
        target_id="target-01",
        remaining_calls=remaining_calls,
        candidate_records={
            url: CandidateRecord(
                candidate_id="candidate-01",
                url=url,
                discovered_via="search",
                status=status,
            )
        },
        candidate_urls=[] if status == "deferred" else [url],
    )


def _provider_error() -> ResearchError:
    return ResearchError(
        error_type="researcher_extraction_provider_error",
        source="researcher",
        message="The provider failed while extracting findings.",
        recoverable=False,
    )


def test_the_repair_stop_reasons_are_exactly_the_five_named_ones() -> None:
    assert REPAIR_STOP_REASONS == (
        "no_progress",
        "pending_capacity",
        "evidence_unavailable",
        "provider_failure",
        "max_iterations",
    )
    for reason in REPAIR_STOP_REASONS:
        if reason == "max_iterations":
            # The ceiling already has its own route reason, and that is what
            # the router reports; the field still names the ceiling.
            continue
        assert reason in GRAPH_ROUTES


def test_an_unchanged_pass_after_a_processed_repair_is_no_progress() -> None:
    state = fake_research_state(
        sub_topics=[_unmet_topic()],
        max_iterations=3,
        iteration=1,
    )
    before = progress_snapshot(state)
    after = progress_snapshot(state, previous=before)

    assert repair_stop_reason(state, before=before, after=after) == "no_progress"


def test_an_identical_snapshot_stops_a_run_that_still_wants_a_pass() -> None:
    state = fake_research_state(
        critique=fake_critique(should_continue=True, score=4),
        max_iterations=3,
        iteration=1,
        repair_stop_reason="no_progress",
    )

    assert graph_route(state) == (ROUTE_FINALIZE, "no_progress")
    assert graph_status(state) == "incomplete"
    # A graph stop is never a failed review: the report was judged, and the
    # machine stopped buying passes that change nothing.
    assert graph_status(state) != "failed"


def test_a_completed_repair_with_budget_left_keeps_going() -> None:
    state = fake_research_state(max_iterations=3, iteration=1)
    before = ResearchProgress(composition_fingerprint="pass-1")
    after = ResearchProgress(
        assessed_support_fingerprints=["a"],
        composition_fingerprint="pass-1",
    )

    assert repair_stop_reason(state, before=before, after=after) is None


def test_a_repair_that_worked_and_ran_out_of_budget_names_the_budget() -> None:
    state = fake_research_state(max_iterations=2, iteration=1)
    before = ResearchProgress(composition_fingerprint="pass-1")
    after = ResearchProgress(
        assessed_support_fingerprints=["a"],
        composition_fingerprint="pass-1",
    )

    assert repair_stop_reason(state, before=before, after=after) == "max_iterations"


def test_a_new_independent_support_fingerprint_is_progress() -> None:
    """The support is progress before the Critic's verdict moves."""
    before = ResearchProgress(
        assessed_support_fingerprints=["claim-1|verified_pair"]
    )
    after = ResearchProgress(
        assessed_support_fingerprints=[
            "claim-1|verified_pair",
            "claim-2|verified_pair",
        ]
    )

    assert progress_improved(before, after)
    assert not progress_improved(after, before)


def test_pending_deferred_evidence_is_not_labelled_no_progress() -> None:
    """Unprocessed deferred evidence is work owed, not a stall."""
    state = fake_research_state(
        sub_topics=[_unmet_topic()],
        max_iterations=3,
        iteration=1,
        acquisition_state_by_target={
            "target-01": _deferred_state(remaining_calls=4)
        },
    )
    before = progress_snapshot(state)
    after = progress_snapshot(state, previous=before)

    assert pending_repair_work(state)
    assert repair_stop_reason(state, before=before, after=after) is None
    assert graph_route(
        state.model_copy(
            update={"critique": fake_critique(should_continue=True, score=4)}
        )
    ) == (ROUTE_REFINE, "refinement_requested")


def test_deferred_evidence_that_cannot_fit_is_capacity_limited() -> None:
    state = fake_research_state(
        sub_topics=[_unmet_topic()],
        max_iterations=3,
        iteration=1,
        acquisition_state_by_target={
            "target-01": _deferred_state(remaining_calls=0)
        },
        critique=fake_critique(should_continue=True, score=4),
    )
    before = progress_snapshot(state)
    after = progress_snapshot(state, previous=before)
    reason = repair_stop_reason(state, before=before, after=after)

    assert reason == "pending_capacity"
    stopped = state.model_copy(update={"repair_stop_reason": reason})
    assert graph_route(stopped) == (ROUTE_FINALIZE, "pending_capacity")
    assert graph_status(stopped) == "incomplete"


def test_exhausted_leads_are_evidence_unavailable_rather_than_a_stall() -> None:
    state = fake_research_state(
        sub_topics=[_unmet_topic()],
        max_iterations=3,
        iteration=1,
        acquisition_state_by_target={
            "target-01": AcquisitionState(
                target_id="target-01",
                remaining_calls=0,
                empty_searches=2,
                denied_urls=["https://lab.example/denied"],
            )
        },
    )
    before = progress_snapshot(state)
    after = progress_snapshot(state, previous=before)

    assert repair_stop_reason(state, before=before, after=after) == (
        "evidence_unavailable"
    )


def test_a_provider_outage_is_never_reported_as_a_stalled_repair() -> None:
    state = fake_research_state(
        sub_topics=[_unmet_topic()],
        max_iterations=3,
        iteration=1,
        errors=[_provider_error()],
        critique=fake_critique(should_continue=True, score=4),
    )
    before = progress_snapshot(state)
    after = progress_snapshot(state, previous=before)
    reason = repair_stop_reason(state, before=before, after=after)

    assert reason == "provider_failure"
    stopped = state.model_copy(update={"repair_stop_reason": reason})
    assert graph_route(stopped) == (ROUTE_FINALIZE, "provider_failure")
    # Distinct from the review that never happened: the report was reviewed,
    # the repair is what could not run.
    assert graph_route(stopped)[1] != "critique_failed"
    assert graph_status(stopped) == "incomplete"


def test_a_failed_review_still_outranks_every_repair_stop_reason() -> None:
    state = fake_research_state(
        critique=fake_failed_critique(),
        max_iterations=3,
        iteration=1,
        repair_stop_reason="no_progress",
    )

    assert graph_route(state) == (ROUTE_FINALIZE, "critique_failed")
    assert graph_status(state) == "failed"


def test_a_fixed_duplicated_paragraph_moves_the_composition_fingerprint() -> None:
    state = fake_research_state(report="# Report\n\nSame paragraph.\n")
    duplicated = state.model_copy(
        update={"report": "# Report\n\nSame paragraph.\n\nSame paragraph.\n"}
    )

    assert progress_snapshot(state).composition_fingerprint != (
        progress_snapshot(duplicated).composition_fingerprint
    )


def test_the_progress_snapshot_names_completed_targets_and_open_defects() -> None:
    state = fake_research_state(
        sub_topics=[_unmet_topic()],
        critique=Critique(
            score=5,
            gaps=[
                CritiqueGap(
                    gap_id="gap-01",
                    coverage_id="topic-01",
                    target_ids=["target-01"],
                    kind="missing_support",
                    severity="major",
                    repair_action="acquire",
                    problem="No cost data.",
                )
            ],
            unsupported_claims=[],
            recommended_queries=[],
            should_continue=True,
            rationale="Recorded for progress tests.",
        ),
    )

    snapshot = progress_snapshot(state)

    assert snapshot.completed_target_ids == []
    assert snapshot.unresolved_major_gap_ids == [
        "acquire|topic-01|target-01||"
    ]
    assert snapshot.composition_fingerprint
    assert not snapshot.pending_work_ids
