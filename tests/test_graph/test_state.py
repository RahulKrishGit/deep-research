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
)
from deep_research.utils.types import (
    LEGACY_QUALITY_CONTRACT_VERSION,
    QUALITY_CONTRACT_VERSION,
    QUALITY_STATUS_ACCEPTED,
    QUALITY_STATUS_PARTIAL,
    MemorySnapshot,
    ResearchError,
    SubTopic,
)
from tests.graph_fakes import (
    fake_critique,
    fake_quality,
    fake_research_state,
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
        )
    }

    assert reasons == set(GRAPH_ROUTES)
    for reason in GRAPH_ROUTES:
        assert GRAPH_ROUTES[reason].strip()


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
