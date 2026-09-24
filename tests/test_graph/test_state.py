"""Tests for the graph channel, the halt discipline, and routing."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from deep_research.agents.critic import (
    CritiqueDraft,
    CritiqueGapDraft,
    build_critique,
    fallback_critique,
)
from deep_research.agents.evidence import (
    READ_ADMISSION_OPERATION,
    build_boundary_audit,
    build_evidence_unit,
    build_read_record,
    require_boundary_manifest,
)
from deep_research.agents.researcher import select_sub_topics
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
    evidence_exhausted,
    graph_quality_status,
    graph_recursion_limit,
    graph_route,
    graph_status,
    initial_graph_state,
    is_halted,
    load_state,
    pending_repair_work,
    progress_snapshot,
    repair_capacity_spent,
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
    Claim,
    Critique,
    CritiqueGap,
    EvidenceTarget,
    MemorySnapshot,
    ReportComposition,
    ReportPoint,
    ReportStatement,
    ResearchError,
    ResearchProgress,
    ResearchState,
    SubTopic,
    progress_improved,
)
from tests.graph_fakes import (
    fake_critique,
    fake_failed_critique,
    fake_quality,
    fake_rejected_report_review,
    fake_report_review,
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
            # Task 10: a *scored* semantic review that refused the report. A
            # review that was never made does not add a reason of its own — it
            # blocks acceptance without buying a pass.
            fake_research_state(
                critique=fake_critique(should_continue=False, score=9),
                quality=fake_quality(),
                report_review=fake_rejected_report_review(),
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
        report_review=fake_report_review(),
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
        report_review=fake_report_review(),
        iteration=0,
        max_iterations=3,
    )

    assert graph_route(state) == (ROUTE_FINALIZE, "critique_satisfied")
    assert graph_quality_status(state) == QUALITY_STATUS_ACCEPTED


def test_a_clean_gate_with_no_semantic_review_is_never_accepted() -> None:
    """Task 10: critic-only acceptance is not acceptance.

    Every other signal here is a clean acceptance — no hard failure, the Critic
    scored 9 and stopped asking — and the report still must not be called
    accepted, because nothing judged its substance. This is the case a missing
    semantic review has to be, and it is why acceptance reads the review
    directly rather than only the route.
    """
    state = fake_research_state(
        critique=fake_critique(should_continue=False, score=9),
        quality=fake_quality(hard_failures=[]),
        iteration=0,
        max_iterations=3,
    )

    assert state.report_review is None
    assert graph_route(state) == (ROUTE_FINALIZE, "critique_satisfied")
    assert graph_quality_status(state) == QUALITY_STATUS_PARTIAL


def test_a_scored_review_below_the_threshold_buys_one_bounded_pass() -> None:
    """A refusing review consumes the refinement opportunity, and no more.

    The review's defect is a real finding with a node to run, so it routes like
    a Critic gap while budget remains. At the ceiling it publishes `incomplete`
    with `partial` quality — never `completed`, which would claim the gates
    cleared a report the reviewer refused.
    """
    review = fake_rejected_report_review(repair_action="acquire")
    with_budget = fake_research_state(
        critique=fake_critique(should_continue=False, score=9),
        quality=fake_quality(),
        report_review=review,
        iteration=0,
        max_iterations=3,
    )
    spent = fake_research_state(
        critique=fake_critique(should_continue=False, score=9),
        quality=fake_quality(),
        report_review=review,
        iteration=3,
        max_iterations=3,
    )

    assert graph_route(with_budget) == (ROUTE_REFINE, "semantic_review_gap")
    assert graph_route(spent) == (ROUTE_FINALIZE, "semantic_review_gap")
    assert graph_status(spent) == "incomplete"
    assert graph_quality_status(spent) == QUALITY_STATUS_PARTIAL


def test_an_incomplete_review_never_buys_a_pass_and_never_accepts() -> None:
    """No judgement is not a rejection and not an acceptance."""
    review = fake_report_review(status="incomplete")
    state = fake_research_state(
        critique=fake_critique(should_continue=False, score=9),
        quality=fake_quality(),
        report_review=review,
        iteration=0,
        max_iterations=3,
    )

    assert graph_route(state) == (ROUTE_FINALIZE, "critique_satisfied")
    assert graph_quality_status(state) == QUALITY_STATUS_PARTIAL


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


def _ceiling_critique(
    *,
    score: int,
    gaps: Sequence[CritiqueGapDraft] = (),
    unsupported_claims: Sequence[str] = (),
) -> Critique:
    """The critique the production Critic records at the last allowed pass.

    Built through ``build_critique`` rather than hand-assembled. At the
    ceiling, ``route_decision`` checks the iteration bound before it looks at
    score, gaps, or unsupported claims, so the critique it returns always
    carries ``should_continue=False`` here regardless of these arguments —
    exactly the signal ``_acceptance_satisfied`` must not trust on its own.
    """
    draft = CritiqueDraft(
        score=score,
        gaps=list(gaps),
        unsupported_claims=list(unsupported_claims),
        recommended_queries=[],
        rationale="Recorded for graph tests.",
    )
    critique, _ = build_critique(draft, iteration=1, max_iterations=1)
    return critique


def _last_allowed_iteration(**overrides: object) -> ResearchState:
    """A fully satisfied acceptance at the last iteration, minus one thing.

    Every signal the deterministic gate and both reviewers emit here is a
    clean acceptance; a test that wants one blocker passes it by keyword.
    """
    settings: dict[str, object] = {
        "critique": _ceiling_critique(score=9),
        "quality": fake_quality(hard_failures=[]),
        "report_review": fake_report_review(),
        "report": "A composed reader report.",
        "iteration": 1,
        "max_iterations": 1,
    }
    settings.update(overrides)
    return fake_research_state(**settings)


def test_a_clean_acceptance_at_the_last_allowed_iteration_is_accepted() -> None:
    """A spent budget is not a defect in the report: the last pass can be it.

    One iteration is the whole budget of a short run, so a run that did
    everything asked of it — the deterministic gate found no hard failure, the
    Critic stopped asking for more, and the terminal semantic review scored the
    report at or above the threshold — is the very report those gates call
    accepted with budget to spare. The exhaustion stop used to be read first,
    so that report published as ``max_iterations`` and ``partial``: "the budget
    ran out" said of a report both reviewers had already cleared. Nothing is
    waived here — ``finalize`` is not another pass, and the acceptance is the
    full conjunction, so any one missing condition still ends the run on the
    exhausted path (the cases below).
    """
    state = _last_allowed_iteration()

    assert state.iteration == state.max_iterations
    assert graph_route(state) == (ROUTE_FINALIZE, "critique_satisfied")
    assert graph_status(state) == "completed"
    assert graph_quality_status(state) == QUALITY_STATUS_ACCEPTED


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param(
            {"quality": fake_quality(hard_failures=["duplicate_claims"])},
            id="hard-gate-failure",
        ),
        pytest.param({"quality": None}, id="no-quality-judgement"),
        pytest.param(
            {"critique": _ceiling_critique(score=4)},
            id="critic-still-asking-a-low-score",
        ),
        pytest.param(
            {
                "critique": _ceiling_critique(
                    score=9,
                    gaps=[
                        CritiqueGapDraft(
                            target_ids=["t1"],
                            kind="coverage",
                            severity="major",
                            repair_action="acquire",
                            problem="No cost data was found for the estimate.",
                        )
                    ],
                )
            },
            id="critic-still-asking-a-material-gap",
        ),
        pytest.param(
            {
                "critique": _ceiling_critique(
                    score=9,
                    unsupported_claims=[
                        "The report claims a 40% cost drop with no cited source."
                    ],
                )
            },
            id="critic-still-asking-an-unsupported-claim",
        ),
        pytest.param(
            {"critique": fake_failed_critique()}, id="critique-never-reviewed"
        ),
        pytest.param({"report_review": None}, id="no-semantic-review"),
        pytest.param(
            {"report_review": fake_report_review(status="incomplete")},
            id="incomplete-semantic-review",
        ),
        pytest.param(
            {"report_review": fake_report_review(status="provider_failed")},
            id="provider-failed-semantic-review",
        ),
        pytest.param(
            {"report_review": fake_rejected_report_review()},
            id="refused-by-the-semantic-review",
        ),
    ],
)
def test_one_unmet_condition_keeps_the_last_iteration_partial(
    overrides: dict[str, object]
) -> None:
    """The ceiling accepts the fully satisfied report and never a near miss.

    Each case is the clean acceptance above with exactly one condition unmet,
    and none of them may be called accepted: an exhausted budget is what those
    runs still ended on. None of them may buy a pass either — the ceiling is
    the ceiling — and a refused review must not be outranked by it.
    """
    state = _last_allowed_iteration(**overrides)

    assert graph_route(state)[0] == ROUTE_FINALIZE
    assert graph_status(state) != "completed"
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
    """One sub-topic's queue, keyed the way the Researcher keys it."""
    url = "https://lab.example/queue"
    return AcquisitionState(
        target_id="topic-01",
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


def _statement_composition(
    statement_id: str,
    text: str,
    *,
    duplicate: bool = False,
) -> ReportComposition:
    """One substantive reader statement, optionally printed twice."""
    row = ReportStatement(
        statement_id=statement_id,
        text=text,
        mode="settled",
        target_ids=["target-01"],
        answered_dimensions=["cost"],
    )
    rows = [row, row.model_copy(update={"statement_id": f"{statement_id}b"})]
    return ReportComposition(
        question="How mature is quantum error correction?",
        session_id="session-1",
        summary=[
            ReportPoint(text=item.text, statement=item)
            for item in (rows if duplicate else rows[:1])
        ],
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
            "topic-01": _deferred_state(remaining_calls=4)
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
            "topic-01": _deferred_state(remaining_calls=0)
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
    """A spent target is a dead end, and the attempt is read by its own key.

    ``acquisition_state_by_target`` is written by the Researcher alone, keyed
    by the sub-topic's ``coverage_id`` (``topic-01``) — one acquisition loop
    runs per sub-topic — while the obligation it answers carries a namespaced
    target id (``target-01``). Reading the attempt by the target id found
    nothing, so ``evidence_exhausted`` was constantly False and every run
    whose leads were all spent reported ``no_progress``: a stall, where the
    truth is a dead end the run had already established.
    """
    state = fake_research_state(
        sub_topics=[_unmet_topic()],
        max_iterations=3,
        iteration=1,
        acquisition_state_by_target={
            "topic-01": AcquisitionState(
                target_id="topic-01",
                remaining_calls=0,
                empty_searches=2,
                denied_urls=["https://lab.example/denied"],
            )
        },
    )
    before = progress_snapshot(state)
    after = progress_snapshot(state, previous=before)

    assert evidence_exhausted(state) is True
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
    after = progress_snapshot(state)
    # The previous snapshot was taken before this pass recorded its error.
    before = after.model_copy(update={"error_count": 0})
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


def test_a_duplicated_statement_moves_the_composition_fingerprint() -> None:
    """A repeated paragraph is structural: the same fact stated twice."""
    once = fake_research_state(
        composition=_statement_composition("S001", "The cost is 40 EUR.")
    )
    twice = fake_research_state(
        composition=_statement_composition(
            "S001", "The cost is 40 EUR.", duplicate=True
        )
    )

    assert progress_snapshot(once).composition_fingerprint != (
        progress_snapshot(twice).composition_fingerprint
    )


def test_a_reworded_report_over_the_same_statements_is_not_progress() -> None:
    """Wording is not evidence: a real synthesizer re-words every pass.

    Fingerprinting the rendered prose made every pass look like progress, so
    ``no_progress`` was reachable only against byte-identical fakes.
    """
    first = fake_research_state(
        report="# Report\n\nThe cost is 40 EUR per tonne.\n",
        composition=_statement_composition("S001", "The cost is 40 EUR."),
    )
    reworded = first.model_copy(
        update={"report": "# Report\n\nCosts run at roughly 40 EUR a tonne.\n"}
    )

    assert progress_snapshot(first).composition_fingerprint == (
        progress_snapshot(reworded).composition_fingerprint
    )
    assert not progress_improved(
        progress_snapshot(first), progress_snapshot(reworded)
    )


def test_an_old_provider_error_does_not_outrank_pending_work() -> None:
    """Only the failure of the pass just finished stops the loop as one."""
    old_error = fake_research_state(errors=[_provider_error()])
    before = progress_snapshot(old_error).model_copy(update={"error_count": 1})
    state = fake_research_state(
        sub_topics=[_unmet_topic()],
        max_iterations=3,
        iteration=1,
        errors=[_provider_error()],
        acquisition_state_by_target={
            "topic-01": _deferred_state(remaining_calls=4)
        },
    )
    after = progress_snapshot(state)

    assert after.error_count == 1
    assert repair_stop_reason(state, before=before, after=after) is None


def test_a_provider_failure_in_the_pass_just_finished_is_reported() -> None:
    state = fake_research_state(
        sub_topics=[_unmet_topic()],
        max_iterations=3,
        iteration=1,
        errors=[_provider_error()],
    )
    after = progress_snapshot(state)
    # The previous snapshot was taken before this pass recorded its error.
    before = after.model_copy(update={"error_count": 0})

    assert (
        repair_stop_reason(state, before=before, after=after)
        == "provider_failure"
    )


def test_capacity_remains_while_a_pass_can_still_be_opened() -> None:
    """``refine`` can open pass ``iteration + 1`` while ``iteration < max``."""
    state = fake_research_state(
        sub_topics=[_unmet_topic()],
        max_iterations=3,
        iteration=2,
        acquisition_state_by_target={
            "topic-01": _deferred_state(remaining_calls=4)
        },
    )
    before = progress_snapshot(state)
    after = progress_snapshot(state, previous=before)

    assert repair_capacity_spent(state) is False
    assert repair_stop_reason(state, before=before, after=after) is None


def _second_unmet_topic() -> SubTopic:
    """A second planned topic, unmet, with its own obligation id."""
    return fake_sub_topic(coverage_id="topic-02", priority=2).model_copy(
        update={
            "evidence_targets": [
                EvidenceTarget(
                    target_id="topic-02-target-01",
                    coverage_id="topic-02",
                    question="What does it cost?",
                    required_dimensions=["cost"],
                    required=True,
                    critical=True,
                    support_policy="independent_pair",
                )
            ]
        }
    )


def _answered_composition() -> ReportComposition:
    """One reader statement that satisfies topic-01's cost obligation."""
    claim = Claim(
        claim_id="claim-01",
        text="The cost is 40 EUR per tonne.",
        source_urls=["https://lab.example/cost"],
        verdict="verified",
        evidence_status="verified_pair",
        confidence=0.9,
        evidence=["An independent review states the same figure."],
        contradictions=[],
        verification_evidence=[],
        target_ids=["target-01"],
    )
    return ReportComposition(
        question="How mature is quantum error correction?",
        session_id="session-1",
        sub_topics=[_unmet_topic()],
        claims=[claim],
        summary=[
            ReportPoint(
                text=claim.text,
                claim_ids=[claim.claim_id],
                source_urls=list(claim.source_urls),
                statement=ReportStatement(
                    statement_id="S001",
                    text=claim.text,
                    mode="settled",
                    claim_cluster_ids=[claim.claim_id],
                    target_ids=["target-01"],
                    answered_dimensions=["cost"],
                ),
            )
        ],
    )


def test_leftover_work_on_an_answered_topic_does_not_hold_a_stalled_run_open() -> (
    None
):
    """Only the work a refinement pass will pick up is work the run still owes.

    ``pending_repair_work`` counted every entry in
    ``acquisition_state_by_target``, including sub-topics whose required
    targets are all answered — and an ordinary pass nearly always leaves
    unread search candidates queued. A refinement pass never revisits those
    topics (``select_sub_topics`` omits them), so their queues could never be
    drained, yet holding one kept ``repair_stop_reason`` from stopping: the run
    bought every remaining macro pass and ended ``max_iterations_reached``
    instead of reporting the dead end it had established.
    """
    state = fake_research_state(
        sub_topics=[_unmet_topic(), _second_unmet_topic()],
        composition=_answered_composition(),
        critique=fake_critique(should_continue=True, score=4),
        max_iterations=3,
        iteration=1,
        acquisition_state_by_target={
            "topic-01": AcquisitionState(
                target_id="topic-01",
                remaining_calls=3,
                candidate_urls=["https://lab.example/untouched"],
            ),
            "topic-02": AcquisitionState(
                target_id="topic-02",
                remaining_calls=0,
                empty_searches=2,
                denied_urls=["https://lab.example/denied"],
            ),
        },
    )
    before = progress_snapshot(state)
    after = progress_snapshot(state, previous=before)

    # The pass researches topic-02 only: topic-01's obligation is answered, so
    # neither its queue nor its remaining calls are work this run can spend.
    assert [topic.coverage_id for topic in select_sub_topics(state)] == [
        "topic-02"
    ]
    assert not [
        item for item in pending_repair_work(state) if "topic-01" in item
    ]
    assert repair_stop_reason(state, before=before, after=after) == (
        "evidence_unavailable"
    )

    # Control: the same leftover queued for the topic the pass *will* revisit
    # is work this run owes, and a run holding it has not run out of capacity.
    revisited = state.model_copy(
        update={
            "acquisition_state_by_target": {
                "topic-02": AcquisitionState(
                    target_id="topic-02",
                    remaining_calls=2,
                    candidate_urls=["https://lab.example/queued"],
                )
            }
        }
    )
    revisited_before = progress_snapshot(revisited)
    revisited_after = progress_snapshot(revisited, previous=revisited_before)

    assert pending_repair_work(revisited) == [
        "topic-02:candidate:https://lab.example/queued"
    ]
    assert repair_capacity_spent(revisited) is False
    assert (
        repair_stop_reason(revisited, before=revisited_before, after=revisited_after)
        is None
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
