"""Tests for the graph channel, the halt discipline, and routing."""

from __future__ import annotations

import pytest

from deep_research.agents.evidence import (
    READ_ADMISSION_OPERATION,
    build_boundary_audit,
    build_evidence_unit,
    build_read_record,
    require_boundary_manifest,
)
from deep_research.agents.researcher import select_sub_topics
from deep_research.graph.state import (
    DEFAULT_MAX_EXTRA_PASSES,
    EVIDENCE_VERIFIER_NODE,
    EXTRA_PASS_NODE,
    FINALIZE_NODE,
    GRAPH_ROUTES,
    GRAPH_STATUSES,
    HALTING_ERROR_TYPES,
    NODE_NAMES,
    PLANNER_NODE,
    REDRAFT_NODE,
    REPORT_REVIEWER_NODE,
    REPORT_WRITER_NODE,
    RESEARCHER_NODE,
    ROUTE_END,
    ROUTE_EXTRA_PASS,
    ROUTE_FINALIZE,
    ROUTE_REDRAFT,
    SOURCE_EVALUATOR_NODE,
    dump_state,
    graph_quality_status,
    graph_recursion_limit,
    graph_route,
    graph_status,
    initial_graph_state,
    is_halted,
    load_state,
)
from deep_research.utils.types import (
    LEGACY_QUALITY_CONTRACT_VERSION,
    QUALITY_CONTRACT_VERSION,
    QUALITY_STATUS_ACCEPTED,
    QUALITY_STATUS_PARTIAL,
    REVIEW_DIMENSIONS,
    AcquisitionState,
    MemorySnapshot,
    ReportQualitySnapshot,
    ReportReview,
    ResearchError,
    ResearchState,
    ReviewDefect,
    SubTopic,
)
from tests.graph_fakes import (
    fake_quality,
    fake_report_review,
    fake_research_state,
    fake_sub_topic,
    fake_target,
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
        max_extra_passes=2,
        memory_context=MemorySnapshot(suggested_strategies=["start broad"]),
    )
    state = load_state(channel)

    assert set(channel) == {"state"}
    assert state.session_id == "session-1"
    assert state.original_question == "How mature is quantum error correction?"
    assert state.max_extra_passes == 2
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


def test_the_acquisition_registry_survives_the_channel_and_is_optional_in_it() -> (
    None
):
    """The registry is part of the snapshot, and a channel without one is empty.

    Two halves of one reading. ``dump_state`` renders the whole research state,
    so a checkpoint carries each target's acquisition queue and the round trip
    returns the same records — that is the guarantee the channel itself owns,
    and it is why the dump exists at all. A payload that names no registry is
    the honest empty one rather than a rejection, exactly as a pre-contract
    snapshot loads as legacy rather than as provenance.

    Neither half needs a compatibility branch in the loader: the contract
    model's own default answers the absent key, and a snapshot carrying fields
    this build does not define is refused by ``extra='forbid'`` before any
    default can apply — so no "older checkpoint" can ever reach a shim there.
    """
    queued = AcquisitionState(
        target_id="topic-01-target-01",
        candidate_urls=["https://lab.example/queue"],
        remaining_calls=2,
    )
    state = fake_research_state(
        acquisition_state_by_target={"topic-01-target-01": queued}
    )

    channel = dump_state(state)

    assert isinstance(channel["state"]["acquisition_state_by_target"], dict)
    assert load_state(channel) == state
    assert (
        load_state(channel).acquisition_state_by_target["topic-01-target-01"]
        == queued
    )

    without_registry = {
        key: value
        for key, value in channel["state"].items()
        if key != "acquisition_state_by_target"
    }
    assert load_state({"state": without_registry}).acquisition_state_by_target == {}


def test_the_initial_channel_defaults_to_an_empty_memory_snapshot() -> None:
    state = load_state(
        initial_graph_state(session_id="session-1", question="Why?")
    )

    assert state.memory_context == MemorySnapshot()
    assert state.max_extra_passes == DEFAULT_MAX_EXTRA_PASSES == 1
    assert state.extra_pass_target_ids == []


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
        report_review=fake_report_review(),
    )

    channel = dump_state(state)

    assert isinstance(channel["state"], dict)
    assert isinstance(channel["state"]["sub_topics"], list)
    assert isinstance(channel["state"]["sub_topics"][0], dict)
    assert load_state(channel) == state


def test_only_enumerated_error_types_halt_a_run() -> None:
    survivable = ResearchError(
        error_type="evidence_verifier_context_check_failed",
        source="agent.evidence_verifier",
        message="The Context Check failed for one batch.",
        recoverable=False,
    )

    assert not is_halted(fake_research_state())
    assert not is_halted(fake_research_state(errors=[survivable]))
    assert is_halted(fake_research_state(errors=[halting_error()]))


# --- routing after the Report Reviewer (spec 6.3-6.5, PD-23) ----------------


def _routed(**fields: object) -> tuple[str, str]:
    review = fields.pop(
        "review",
        ReportReview(
            status="scored",
            dimensions={d: 0.9 for d in REVIEW_DIMENSIONS},
            input_fingerprint="packet-1",
        ),
    )
    quality = fields.pop("quality", ReportQualitySnapshot())
    return graph_route(
        ResearchState(
            session_id="s",
            original_question="q",
            report_review=review,  # type: ignore[arg-type]
            quality=quality,  # type: ignore[arg-type]
            **fields,  # type: ignore[arg-type]
        )
    )


def test_a_material_defect_buys_one_writer_redraft_before_a_not_accepted_verdict() -> (
    None
):
    """A review that named what is wrong is acted on, once, before publishing.

    ``semantic_review_passes`` refuses to accept a report whose review carries
    a material defect, and nothing downstream could do anything about it: the
    run published a report its own reviewer had just named as wrong, and the
    only lever left was the next research pass — which cannot fix a
    self-contradiction or an omitted obligation that the evidence already
    supports. The defect list is addressed to the writer, so it buys exactly
    one writer re-run: no new research, one draft, and then the terminal route
    whatever the second review says.
    """
    defect = ReviewDefect(
        defect_id="review-01",
        kind="contradiction",
        severity="major",
        statement_ids=["S001"],
        problem="The report states a rule its own findings qualify.",
    )
    material = ReportReview(
        status="scored",
        dimensions={d: 0.9 for d in REVIEW_DIMENSIONS},
        input_fingerprint="packet-1",
        reviewed_statement_ids=["S001"],
        per_statement_dispositions={"S001": "supported"},
        defects=[defect],
    )

    assert _routed(review=material) == ("redraft", "redraft_requested")
    # One re-run, and the second verdict is terminal: the same review with the
    # re-run spent publishes rather than drafting again.
    assert _routed(review=material, writer_redrafts=1) == (
        "finalize",
        "report_not_accepted",
    )


def test_a_minor_defect_does_not_buy_a_redraft() -> None:
    """Only a defect that blocks acceptance is worth a draft.

    A minor observation is not a reason to re-run the writer: the review passed
    with it recorded, so the route accepts the report it judged and the
    operator still reads the defect in the record.
    """
    minor = ReportReview(
        status="scored",
        dimensions={d: 0.9 for d in REVIEW_DIMENSIONS},
        input_fingerprint="packet-1",
        reviewed_statement_ids=["S001"],
        per_statement_dispositions={"S001": "supported"},
        defects=[
            ReviewDefect(
                defect_id="review-01",
                kind="presentation",
                severity="minor",
                statement_ids=["S001"],
                problem="A sentence reads awkwardly.",
            )
        ],
    )

    assert _routed(review=minor) == ("finalize", "report_accepted")


def test_a_redraft_never_outranks_research_the_budget_can_still_buy() -> None:
    """A missing target with a pass left still buys the pass.

    The redraft cannot answer a target no verified finding answers, so the
    pass comes first and the writer re-run is what the *next* review's defects
    buy. The prefixed `extra_pass_requested` route is also what keeps the
    existing missing-target behaviour unchanged for a review that names a
    material defect beside a missing target.
    """
    both = ReportReview(
        status="scored",
        missing_required_target_ids=["t2"],
        dimensions={d: 0.9 for d in REVIEW_DIMENSIONS},
        input_fingerprint="packet-1",
        reviewed_statement_ids=["S001"],
        per_statement_dispositions={"S001": "supported"},
        defects=[
            ReviewDefect(
                defect_id="review-01",
                kind="coverage",
                severity="major",
                target_ids=["t2"],
                problem="A promised obligation is not stated.",
            )
        ],
    )

    assert _routed(review=both, iteration=0, max_extra_passes=1) == (
        "extra_pass",
        "extra_pass_requested",
    )
    assert _routed(review=both, iteration=1, max_extra_passes=1) == (
        "redraft",
        "redraft_requested",
    )


def test_a_coverage_defect_naming_a_required_target_buys_the_extra_pass() -> None:
    """A reviewer's own coverage defect is missing evidence too (D10).

    ``missing_required_target_ids`` is code-stamped from whether a verified
    finding exists for the target; a required target can carry one and still
    not answer the question, which is exactly what the reviewer's own
    ``coverage`` defect says. That defect must buy the extra pass before any
    redraft whenever the budget still holds one: a redraft cannot answer a
    target no verified finding actually establishes.
    """
    review = ReportReview(
        status="scored",
        dimensions={d: 0.9 for d in REVIEW_DIMENSIONS},
        input_fingerprint="packet-1",
        reviewed_statement_ids=["S001"],
        per_statement_dispositions={"S001": "supported"},
        defects=[
            ReviewDefect(
                defect_id="review-01",
                kind="coverage",
                severity="major",
                target_ids=["t2"],
                problem="The question's second part names no answer.",
            )
        ],
    )
    quality = ReportQualitySnapshot(required_target_ids=["t1", "t2"])

    assert _routed(
        review=review, quality=quality, iteration=0, max_extra_passes=1
    ) == ("extra_pass", "extra_pass_requested")
    assert _routed(
        review=review, quality=quality, iteration=1, max_extra_passes=1
    ) == ("redraft", "redraft_requested")


def test_an_unscored_review_buys_no_redraft() -> None:
    """No judgement is not a defect list: a partial review has nothing to feed."""
    assert _routed(review=ReportReview(status="provider_failed")) == (
        "finalize",
        "review_unavailable",
    )


def test_missing_targets_buy_one_extra_pass_then_publish() -> None:
    missing = ReportReview(
        status="scored",
        missing_required_target_ids=["t2"],
        dimensions={d: 0.9 for d in REVIEW_DIMENSIONS},
        input_fingerprint="packet-1",
    )
    assert _routed(review=missing) == ("extra_pass", "extra_pass_requested")
    # PD-23: passes spent, gates clear, reviewer accepts -> completed, the target under Not found
    assert _routed(review=missing, iteration=1) == ("finalize", "report_accepted")
    assert _routed(review=missing, max_extra_passes=0) == (
        "finalize",
        "report_accepted",
    )
    rejected = missing.model_copy(
        update={"dimensions": {d: 0.5 for d in REVIEW_DIMENSIONS}}
    )
    assert _routed(review=rejected, iteration=1) == (
        "finalize",
        "extra_passes_exhausted",
    )
    unlisted = ReportQualitySnapshot(hard_failures=["unaccounted_required_targets"])
    assert _routed(review=missing, iteration=1, quality=unlisted) == (
        "finalize",
        "extra_passes_exhausted",
    )


def test_the_final_routes_and_their_statuses() -> None:
    assert _routed() == ("finalize", "report_accepted")
    assert _routed(quality=ReportQualitySnapshot(hard_failures=["unjudged_sentences"])) == (
        "finalize",
        "report_not_accepted",
    )
    assert _routed(review=ReportReview(status="provider_failed")) == (
        "finalize",
        "review_unavailable",
    )
    assert (
        graph_status(
            ResearchState(
                session_id="s",
                original_question="q",
                report_review=ReportReview(status="provider_failed"),
                quality=ReportQualitySnapshot(),
            )
        )
        == "incomplete"
    )
    spent = ReportReview(
        status="scored",
        missing_required_target_ids=["t2"],
        dimensions={d: 0.9 for d in REVIEW_DIMENSIONS},
        input_fingerprint="packet-1",
    )
    assert (
        graph_status(
            ResearchState(
                session_id="s",
                original_question="q",
                iteration=1,
                report_review=spent,
                quality=ReportQualitySnapshot(),
            )
        )
        == "completed"
    )
    failing = spent.model_copy(
        update={"dimensions": {d: 0.5 for d in REVIEW_DIMENSIONS}}
    )
    assert (
        graph_status(
            ResearchState(
                session_id="s",
                original_question="q",
                iteration=1,
                report_review=failing,
                quality=ReportQualitySnapshot(),
            )
        )
        == "max_iterations"
    )


def test_a_halted_run_ends_whatever_the_reviewer_said() -> None:
    state = fake_research_state(
        errors=[halting_error()],
        quality=fake_quality(),
        report_review=fake_report_review(missing_required_target_ids=["t2"]),
    )

    assert graph_route(state) == (ROUTE_END, "halted")
    assert graph_status(state) == "failed"
    assert graph_quality_status(state) == QUALITY_STATUS_PARTIAL


def test_a_paused_run_cannot_buy_a_pass_it_cannot_pay_for() -> None:
    """A missing target with no pass left never routes back to the researcher."""
    state = fake_research_state(
        max_extra_passes=0,
        quality=fake_quality(),
        report_review=fake_report_review(missing_required_target_ids=["t2"]),
    )

    assert graph_route(state) == (ROUTE_FINALIZE, "report_accepted")


def test_every_routing_reason_is_enumerated_and_maps_to_a_status() -> None:
    """``graph_route`` has exactly the seven enumerated reasons (spec 6.3-6.5)."""
    missing = fake_report_review(missing_required_target_ids=["t2"])
    rejected = fake_report_review(dimensions={d: 0.5 for d in REVIEW_DIMENSIONS})
    defective = fake_report_review(
        defects=[
            ReviewDefect(
                defect_id="review-01",
                kind="contradiction",
                severity="major",
                problem="The summary contradicts the findings below it.",
            )
        ]
    )
    states = (
        fake_research_state(errors=[halting_error()]),
        fake_research_state(
            quality=fake_quality(), report_review=fake_report_review()
        ),
        fake_research_state(
            quality=fake_quality(hard_failures=["unjudged_sentences"]),
            report_review=fake_report_review(),
        ),
        fake_research_state(report_review=ReportReview(status="provider_failed")),
        fake_research_state(report_review=missing),
        fake_research_state(iteration=1, report_review=missing),
        fake_research_state(iteration=1, report_review=rejected),
        fake_research_state(quality=fake_quality(), report_review=defective),
    )

    reasons = {graph_route(state)[1] for state in states}

    assert reasons == set(GRAPH_ROUTES)
    assert set(GRAPH_ROUTES) == {
        "report_accepted",
        "report_not_accepted",
        "review_unavailable",
        "extra_pass_requested",
        "extra_passes_exhausted",
        "redraft_requested",
        "halted",
    }
    for reason in GRAPH_ROUTES:
        assert GRAPH_ROUTES[reason].strip()
        assert _reason_status(reason) in GRAPH_STATUSES


def _reason_status(reason: str) -> str:
    state = {
        "halted": fake_research_state(errors=[halting_error()]),
        "report_accepted": fake_research_state(
            quality=fake_quality(), report_review=fake_report_review()
        ),
        "report_not_accepted": fake_research_state(
            quality=fake_quality(hard_failures=["unjudged_sentences"]),
            report_review=fake_report_review(),
        ),
        "review_unavailable": fake_research_state(
            report_review=ReportReview(status="provider_failed")
        ),
        "extra_pass_requested": fake_research_state(
            report_review=fake_report_review(missing_required_target_ids=["t2"])
        ),
        "extra_passes_exhausted": fake_research_state(
            iteration=1,
            report_review=fake_report_review(
                missing_required_target_ids=["t2"],
                dimensions={d: 0.5 for d in REVIEW_DIMENSIONS},
            ),
        ),
        "redraft_requested": fake_research_state(
            quality=fake_quality(),
            report_review=fake_report_review(
                defects=[
                    ReviewDefect(
                        defect_id="review-01",
                        kind="contradiction",
                        severity="major",
                        problem="The summary contradicts the findings.",
                    )
                ]
            ),
        ),
    }[reason]
    assert graph_route(state)[1] == reason
    return graph_status(state)


def test_a_run_with_no_review_at_all_is_published_as_partial() -> None:
    """A review that was never made is not a rejection and never an acceptance."""
    state = fake_research_state(quality=fake_quality())

    assert graph_route(state) == (ROUTE_FINALIZE, "review_unavailable")
    assert graph_status(state) == "incomplete"
    assert graph_quality_status(state) == QUALITY_STATUS_PARTIAL


def test_only_a_clean_gate_the_reviewer_accepted_is_accepted() -> None:
    accepted = fake_research_state(
        quality=fake_quality(), report_review=fake_report_review()
    )
    failing_gate = fake_research_state(
        quality=fake_quality(hard_failures=["unaccounted_required_targets"]),
        report_review=fake_report_review(),
    )

    assert graph_quality_status(accepted) == QUALITY_STATUS_ACCEPTED
    assert graph_quality_status(failing_gate) == QUALITY_STATUS_PARTIAL
    assert graph_quality_status(fake_research_state()) == QUALITY_STATUS_PARTIAL


def test_a_run_no_quality_pass_judged_is_never_accepted() -> None:
    state = fake_research_state(report_review=fake_report_review())

    assert state.quality is None
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
    with pytest.raises(ValueError, match="max_extra_passes"):
        graph_recursion_limit(-1)


def test_the_node_names_are_unique_and_ordered() -> None:
    assert len(set(NODE_NAMES)) == len(NODE_NAMES)
    assert NODE_NAMES == (
        PLANNER_NODE,
        RESEARCHER_NODE,
        SOURCE_EVALUATOR_NODE,
        EVIDENCE_VERIFIER_NODE,
        REPORT_WRITER_NODE,
        REPORT_REVIEWER_NODE,
        EXTRA_PASS_NODE,
        REDRAFT_NODE,
        FINALIZE_NODE,
    )
    # The two hops and the terminal publication are the last three: the
    # extra-pass hop is the only node the loop back into the researcher passes
    # through, the redraft hop is the only one that leads to the writer alone,
    # and the finalizer is the only writer of the artifacts.
    assert NODE_NAMES[-3] == EXTRA_PASS_NODE == ROUTE_EXTRA_PASS == "extra_pass"
    assert NODE_NAMES[-2] == REDRAFT_NODE == "writer_redraft"
    assert ROUTE_REDRAFT == "redraft"
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


# --- the pass's own sub-topic selection (spec 6.5, Task 4.4) ----------------


def _two_topic_state(**overrides: object) -> ResearchState:
    return fake_research_state(
        sub_topics=[
            fake_sub_topic(
                "Battery storage",
                coverage_id="topic-01",
                priority=2,
                targets=[fake_target("topic-01-target-01", coverage_id="topic-01")],
            ),
            fake_sub_topic(
                "Grid interconnection",
                coverage_id="topic-02",
                priority=1,
                targets=[fake_target("topic-02-target-01", coverage_id="topic-02")],
            ),
        ],
        **overrides,
    )


def test_the_first_pass_runs_every_planned_sub_topic_in_priority_order() -> None:
    """A topic is planned because the question needs it; priority only orders the pass."""
    state = _two_topic_state()

    assert [topic.coverage_id for topic in select_sub_topics(state)] == [
        "topic-02",
        "topic-01",
    ]


def test_an_extra_pass_runs_only_the_topics_that_own_a_missing_target() -> None:
    """``extra_pass_target_ids`` is that pass's whole job list (D4, Task 4.4).

    Nothing is a coverage gap here: a topic outside the list is simply not
    part of the extra pass, which is why the reason is recorded as a
    confinement rather than as evidence the run failed to find.
    """
    state = _two_topic_state(iteration=1, extra_pass_target_ids=["topic-02-target-01"])

    assert [topic.coverage_id for topic in select_sub_topics(state)] == ["topic-02"]

    other = state.model_copy(update={"extra_pass_target_ids": ["topic-01-target-01"]})

    assert [topic.coverage_id for topic in select_sub_topics(other)] == ["topic-01"]
