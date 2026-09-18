"""Tests for the compiled research graph: sequence, loop, bound, failure."""

from __future__ import annotations

import pytest

from deep_research.agents.errors import AgentConfigurationError
from deep_research.agents.synthesizer import SynthesizerAgent
from deep_research.graph.nodes import ReportPublisher
from deep_research.graph.orchestrator import (
    AGENT_NODE_ORDER,
    build_checkpointer,
    compile_research_graph,
    session_config,
    terminal_publisher,
)
from deep_research.graph.state import (
    CRITIC_NODE,
    DEFAULT_MAX_ITERATIONS,
    FINALIZE_NODE,
    NODE_NAMES,
    REFINE_NODE,
    graph_quality_status,
    graph_status,
    initial_graph_state,
    is_halted,
    load_state,
)
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.utils.config import AgentRuntimeConfig
from deep_research.utils.types import (
    QUALITY_STATUS_ACCEPTED,
    QUALITY_STATUS_PARTIAL,
    ResearchError,
    ResearchState,
)
from tests.agent_fakes import ScriptedCompleter
from tests.graph_fakes import (
    FakeAgent,
    FakePublisher,
    fake_claim,
    fake_critique,
    fake_failed_critique,
    fake_finding,
    fake_research_agents,
    fake_scored_source,
    fake_sub_topic,
    fake_synthesis_update,
)
from tests.research_fakes import synthesizer_tools


async def _run(agents, *, max_iterations: int = 3) -> ResearchState:
    graph = compile_research_graph(agents)
    channel = initial_graph_state(
        session_id="session-1",
        question="How mature is quantum error correction?",
        max_iterations=max_iterations,
    )
    result = await graph.ainvoke(
        channel, session_config("session-1", max_iterations=max_iterations)
    )
    return load_state(result)


def _nodes_visited(state: ResearchState) -> list[str]:
    return [
        event.metadata["node"]
        for event in state.events
        if event.event_type == "graph.node.started"
    ]


def _route_reasons(state: ResearchState) -> list[str]:
    return [
        event.metadata["reason"]
        for event in state.events
        if event.event_type == "graph.route.decided"
    ]


@pytest.mark.asyncio
async def test_a_failed_review_is_never_reported_as_accepted() -> None:
    """End to end: an exhausted repair must not publish an accepted report.

    The graph ran every agent and the quality pass judged the composition, so
    every gate the old code consulted is clean. The Critic's own run then
    produced a critique whose ``should_continue`` is ``False`` with no gaps —
    the same signals a real acceptance carries — and only ``review_status``
    tells the two apart. The run that carries it must end ``failed`` with the
    artifacts ``partial``, and a partial run saves no claim to memory: an
    unreviewed report is not accepted, and it is not remembered as though it
    had been.
    """
    publisher = FakePublisher()
    agents = fake_research_agents(
        synthesizer=FakeAgent(
            "synthesizer", [], update_factory=fake_synthesis_update
        ),
        critic=FakeAgent("critic", [{"critique": fake_failed_critique()}]),
        publisher=publisher,
    )

    state = await _run(agents)

    assert _route_reasons(state) == ["critique_failed"]
    assert graph_status(state) == "failed"
    # The deterministic quality pass did run, so ``partial`` is a verdict
    # about the review rather than the absence of one.
    assert state.quality is not None
    assert graph_quality_status(state) == QUALITY_STATUS_PARTIAL
    assert state.report_path is not None
    assert publisher.memory_writes == 0


def test_the_agent_node_order_matches_the_designed_sequence() -> None:
    assert AGENT_NODE_ORDER == (
        "planner",
        "researcher",
        "source_evaluator",
        "fact_checker",
        "synthesizer",
    )
    # Order matters, not just membership: the graph's real edges read
    # ``AGENT_NODE_ORDER``, so it must be exactly the head of ``NODE_NAMES``.
    assert NODE_NAMES == (
        *AGENT_NODE_ORDER,
        CRITIC_NODE,
        REFINE_NODE,
        FINALIZE_NODE,
    )


@pytest.mark.asyncio
async def test_the_happy_path_runs_every_agent_once_in_order() -> None:
    agents = fake_research_agents()

    state = await _run(agents)

    assert _nodes_visited(state) == [
        *AGENT_NODE_ORDER,
        CRITIC_NODE,
        FINALIZE_NODE,
    ]
    assert state.report == "# Research report: pass 1"
    assert state.iteration == 0
    assert _route_reasons(state) == ["critique_satisfied"]
    assert graph_status(state) == "completed"
    assert not state.errors


@pytest.mark.asyncio
async def test_the_critic_can_send_the_graph_back_to_the_researcher() -> None:
    agents = fake_research_agents(
        researcher=FakeAgent(
            "researcher",
            [
                {"raw_findings": [fake_finding("Break-even was reached.")]},
                {"raw_findings": [fake_finding("Costs fell tenfold.")]},
            ],
        ),
        synthesizer=FakeAgent(
            "synthesizer",
            [{"report": "# pass 1"}, {"report": "# pass 2"}],
        ),
        critic=FakeAgent(
            "critic",
            [
                {"critique": fake_critique(should_continue=True, score=4)},
                {"critique": fake_critique(should_continue=False, score=9)},
            ],
        ),
    )

    state = await _run(agents)

    assert _nodes_visited(state) == [
        *AGENT_NODE_ORDER,
        CRITIC_NODE,
        "researcher",
        "source_evaluator",
        "fact_checker",
        "synthesizer",
        CRITIC_NODE,
        FINALIZE_NODE,
    ]
    assert len(agents.planner.calls) == 1
    assert len(agents.researcher.calls) == 2
    assert _route_reasons(state) == [
        "refinement_requested",
        "critique_satisfied",
    ]
    assert graph_status(state) == "completed"


@pytest.mark.asyncio
async def test_a_refinement_pass_advances_the_macro_iteration() -> None:
    agents = fake_research_agents(
        critic=FakeAgent(
            "critic",
            [
                {"critique": fake_critique(should_continue=True, score=4)},
                {"critique": fake_critique(should_continue=False, score=9)},
            ],
        )
    )

    state = await _run(agents)

    assert state.iteration == 1
    refinements = [
        event
        for event in state.events
        if event.event_type == "graph.refinement.started"
    ]
    assert [event.metadata["iteration"] for event in refinements] == [1]
    # The Researcher's second pass sees the critique that asked for it.
    assert agents.researcher.calls[1].critique is not None


@pytest.mark.asyncio
async def test_state_replaces_canonical_snapshots_and_appends_the_rest() -> None:
    """Evidence channels land the pass's snapshot; nothing piles up twice.

    ``evaluated_sources`` and ``verified_claims`` replace, so the producers —
    Source Evaluator and Fact Checker — emit their whole snapshot each pass.
    The fakes below do the same: an append would leave three entries after
    two passes, and a snapshot that dropped the earlier pass's evidence would
    leave one.
    """
    first_source = fake_scored_source("https://example.org/a")
    second_source = fake_scored_source("https://example.org/b")
    first_claim = fake_claim("Break-even was reached in 2025.")
    second_claim = fake_claim("Logical error rates fell in 2025.")
    agents = fake_research_agents(
        researcher=FakeAgent(
            "researcher",
            [
                {"raw_findings": [fake_finding("first")]},
                {"raw_findings": [fake_finding("second")]},
            ],
        ),
        source_evaluator=FakeAgent(
            "source_evaluator",
            [
                {"evaluated_sources": [first_source]},
                {"evaluated_sources": [first_source, second_source]},
            ],
        ),
        fact_checker=FakeAgent(
            "fact_checker",
            [
                {"verified_claims": [first_claim]},
                {"verified_claims": [first_claim, second_claim]},
            ],
        ),
        synthesizer=FakeAgent(
            "synthesizer",
            [{"report": "# pass 1"}, {"report": "# pass 2"}],
        ),
        critic=FakeAgent(
            "critic",
            [
                {"critique": fake_critique(should_continue=True, score=4)},
                {"critique": fake_critique(should_continue=False, score=9)},
            ],
        ),
    )

    state = await _run(agents)

    assert [finding.content for finding in state.raw_findings] == [
        "first",
        "second",
    ]
    assert state.report == "# pass 2"
    assert state.critique is not None
    assert state.critique.score == 9
    assert [source.url for source in state.evaluated_sources] == [
        "https://example.org/a",
        "https://example.org/b",
    ]
    assert [claim.text for claim in state.verified_claims] == [
        "Break-even was reached in 2025.",
        "Logical error rates fell in 2025.",
    ]


@pytest.mark.asyncio
async def test_the_iteration_bound_forces_an_end_the_critic_did_not_want(
) -> None:
    agents = fake_research_agents(
        critic=FakeAgent(
            "critic", [{"critique": fake_critique(should_continue=True, score=3)}]
        )
    )

    state = await _run(agents, max_iterations=1)

    assert len(agents.researcher.calls) == 2
    assert state.iteration == 1
    assert _route_reasons(state) == [
        "refinement_requested",
        "max_iterations_reached",
    ]
    assert graph_status(state) == "max_iterations"
    assert state.critique is not None
    assert state.critique.should_continue is True


@pytest.mark.asyncio
async def test_the_shipped_default_budget_exhausts_without_a_recursion_error(
) -> None:
    agents = fake_research_agents(
        critic=FakeAgent(
            "critic", [{"critique": fake_critique(should_continue=True, score=3)}]
        )
    )

    state = await _run(agents, max_iterations=DEFAULT_MAX_ITERATIONS)

    # The default budget buys exactly three refinement passes; the fourth
    # critic pass ends the run. The _RECURSION_MARGIN head-room must cover
    # this shape through a real ainvoke, or the halt discipline dies in a
    # GraphRecursionError instead.
    refinements = [
        event
        for event in state.events
        if event.event_type == "graph.refinement.started"
    ]
    assert [event.metadata["iteration"] for event in refinements] == [1, 2, 3]
    assert state.iteration == DEFAULT_MAX_ITERATIONS
    assert graph_status(state) == "max_iterations"
    assert len(agents.researcher.calls) == DEFAULT_MAX_ITERATIONS + 1


@pytest.mark.asyncio
async def test_an_agent_failure_stops_the_run_and_keeps_what_was_collected(
) -> None:
    agents = fake_research_agents(
        fact_checker=FakeAgent(
            "fact_checker", [AgentConfigurationError("bad scratchpad")]
        )
    )

    state = await _run(agents)

    assert _nodes_visited(state) == [
        "planner",
        "researcher",
        "source_evaluator",
        "fact_checker",
    ]
    assert agents.synthesizer.calls == []
    assert agents.critic.calls == []
    assert is_halted(state)
    assert graph_status(state) == "failed"
    assert _route_reasons(state) == ["halted"]
    # Everything gathered before the failure survives.
    assert len(state.raw_findings) == 1
    assert len(state.evaluated_sources) == 1
    skipped = [
        event.metadata["node"]
        for event in state.events
        if event.event_type == "graph.node.skipped"
    ]
    assert skipped == ["synthesizer", "critic"]


@pytest.mark.asyncio
async def test_a_recoverable_agent_error_never_stops_the_graph() -> None:
    recoverable = ResearchError(
        error_type="researcher_sub_topic_without_findings",
        source="agent.researcher",
        message="A high-priority sub-topic produced no findings.",
    )
    agents = fake_research_agents(
        researcher=FakeAgent(
            "researcher",
            [{"raw_findings": [fake_finding()], "errors": [recoverable]}],
        )
    )

    state = await _run(agents)

    assert graph_status(state) == "completed"
    assert [error.error_type for error in state.errors] == [
        "researcher_sub_topic_without_findings"
    ]


@pytest.mark.asyncio
async def test_an_agent_that_returns_invalid_state_fails_the_run() -> None:
    agents = fake_research_agents(
        synthesizer=FakeAgent("synthesizer", [{"iteration": 5}])
    )

    state = await _run(agents)

    assert graph_status(state) == "failed"
    assert [error.error_type for error in state.errors] == [
        "graph_invalid_agent_state"
    ]


def test_the_session_config_pins_the_thread_and_the_superstep_bound() -> None:
    config = session_config("session-1", max_iterations=2)

    assert config["configurable"]["thread_id"] == "session-1"
    assert config["recursion_limit"] > 0


def test_a_checkpointer_is_built_only_when_it_is_asked_for() -> None:
    assert build_checkpointer(enabled=False) is None
    assert build_checkpointer(enabled=True) is not None


# --- the terminal publication -------------------------------------------------


@pytest.mark.asyncio
async def test_the_observed_report_shape_publishes_once_after_three_refinements(
) -> None:
    """Step 8: the exact regression for the observed report.

    Four synthesis passes each rediscover the same two claims and the same two
    sources. Under the old append contract the state would hold eight records
    and the report would print every pair twice; under the canonical-snapshot
    contract the two are rediscovered, not re-added, so the gates see no
    duplicate at all. Publication happens once, at the terminal node: two
    documents and one memory entry for the whole run, never once per pass.

    Both channels emit their *complete* canonical snapshot on every pass. A
    one-record delta per pass would model the append contract this fixture
    exists to disprove.
    """
    first_source = fake_scored_source("https://example.org/a")
    second_source = fake_scored_source("https://example.org/b")
    remembered = fake_claim(
        "Break-even was reached in 2025.", url="https://example.org/a"
    )
    unproven = fake_claim(
        "Costs fell tenfold.",
        url="https://example.org/b",
        verdict="insufficient_evidence",
    )
    publisher = FakePublisher()
    agents = fake_research_agents(
        planner=FakeAgent(
            "planner",
            [
                {
                    "sub_topics": [
                        fake_sub_topic("Error correction", coverage_id="topic-01"),
                        fake_sub_topic("Cost", coverage_id="topic-02", priority=2),
                    ]
                }
            ],
        ),
        researcher=FakeAgent(
            "researcher",
            [{"raw_findings": [fake_finding(), fake_finding("Costs fell.")]}],
        ),
        source_evaluator=FakeAgent(
            "source_evaluator",
            [{"evaluated_sources": [first_source, second_source]}],
        ),
        fact_checker=FakeAgent(
            "fact_checker",
            [{"verified_claims": [remembered, unproven]}],
        ),
        synthesizer=FakeAgent(
            "synthesizer", [], update_factory=fake_synthesis_update
        ),
        critic=FakeAgent(
            "critic",
            [
                {"critique": fake_critique(should_continue=True, score=4)},
                {"critique": fake_critique(should_continue=True, score=4)},
                {"critique": fake_critique(should_continue=True, score=4)},
                {"critique": fake_critique(should_continue=False, score=9)},
            ],
        ),
        publisher=publisher,
    )

    final = await _run(agents, max_iterations=4)

    assert final.iteration == 3
    assert len(agents.researcher.calls) == 4
    assert len(final.evaluated_sources) == 2
    assert len(final.verified_claims) == 2
    assert final.quality is not None
    assert final.quality.duplicate_source_rows == 0
    assert final.quality.duplicate_claims == 0
    assert final.quality.hard_failures == []
    assert publisher.memory_writes == 1
    assert publisher.report_writes == 2  # reader + evidence, terminal only
    assert final.report_path == "report-session-1-3.md"
    assert final.evidence_path == "report-session-1-3-evidence.md"
    assert graph_status(final) == "completed"


@pytest.mark.asyncio
async def test_a_gate_failure_sends_a_critic_approved_report_back() -> None:
    """Step 2 end to end: the deterministic gate outranks the model's score."""
    agents = fake_research_agents(
        source_evaluator=FakeAgent(
            "source_evaluator",
            [
                # The same canonical URL twice: a duplicate source row, which
                # is a hard failure however good the report looks.
                {
                    "evaluated_sources": [
                        fake_scored_source("https://example.org/a"),
                        fake_scored_source("https://example.org/a"),
                    ]
                }
            ],
        ),
        synthesizer=FakeAgent(
            "synthesizer", [], update_factory=fake_synthesis_update
        ),
        critic=FakeAgent(
            "critic",
            [{"critique": fake_critique(should_continue=False, score=9)}],
        ),
    )

    state = await _run(agents, max_iterations=2)

    # The critic never asked for a pass; the gate forced every refinement, and
    # the run ended partial rather than accepted.
    reasons = _route_reasons(state)
    assert reasons[0] == "quality_gate_failed"
    assert reasons[-1] == "max_iterations_reached"
    assert "critique_satisfied" not in reasons
    assert state.iteration == 2
    assert state.quality is not None
    assert "duplicate_source_rows" in state.quality.hard_failures
    published = [
        event
        for event in state.events
        if event.event_type == "graph.report.published"
    ]
    assert len(published) == 1
    assert published[0].metadata["quality_status"] != QUALITY_STATUS_ACCEPTED


@pytest.mark.asyncio
async def test_an_accepted_run_publishes_both_artifacts_and_one_memory_entry(
) -> None:
    publisher = FakePublisher()
    agents = fake_research_agents(
        synthesizer=FakeAgent(
            "synthesizer", [], update_factory=fake_synthesis_update
        ),
        publisher=publisher,
    )

    state = await _run(agents)

    published = [
        event
        for event in state.events
        if event.event_type == "graph.report.published"
    ]
    assert len(published) == 1
    assert published[0].metadata["quality_status"] == QUALITY_STATUS_ACCEPTED
    assert published[0].metadata["report_path"] == "report-session-1-0.md"
    assert published[0].metadata["document_writes"] == 2
    assert publisher.memory_writes == 1
    assert "**Quality status:** accepted" in (state.report or "")


# --- resolving the one writer -------------------------------------------------


def _real_synthesizer(tracker, tmp_path) -> SynthesizerAgent:
    """A production Synthesizer, which owns the two write tools.

    ``runtime/assembly.py`` builds exactly this agent and passes no
    ``publisher=``, so this is the object the publication fallback has to
    recognise for a real run to write anything at all.
    """
    return SynthesizerAgent(
        provider=ScriptedCompleter(),
        tracker=tracker,
        scratchpad=ScratchpadMemory(
            session_id="session-1",
            agent_name="synthesizer",
            max_entries=20,
        ),
        tools=synthesizer_tools(tracker, output_root=tmp_path),
        config=AgentRuntimeConfig(max_iterations=2, tool_budget=0),
    )


def test_the_production_synthesizer_is_the_publisher_when_none_is_wired(
    tracker, tmp_path
) -> None:
    """The fallback is the only reason a real run publishes at all.

    ``ResearchAgents`` is built from agent names alone in
    ``runtime/assembly.py``, so nothing injects ``publisher=`` in production.
    If this recognition breaks, every real run silently degrades to
    ``graph_publication_unavailable`` — a *recoverable* error — so nothing
    would fail loudly.
    """
    synthesizer = _real_synthesizer(tracker, tmp_path)
    agents = fake_research_agents(synthesizer=synthesizer, publisher=None)

    assert isinstance(synthesizer, ReportPublisher) is True
    assert terminal_publisher(agents) is synthesizer


def test_an_explicit_publisher_slot_wins_over_the_synthesizer(
    tracker, tmp_path
) -> None:
    explicit = FakePublisher()
    synthesizer = _real_synthesizer(tracker, tmp_path)
    agents = fake_research_agents(
        synthesizer=synthesizer, publisher=explicit
    )

    assert terminal_publisher(agents) is explicit


def test_a_run_only_double_is_not_a_publisher() -> None:
    """The protocol is structural and method-based, so a double is excluded."""
    synthesizer = FakeAgent("synthesizer", [{"report": "# pass 1"}])
    agents = fake_research_agents(synthesizer=synthesizer, publisher=None)

    assert isinstance(synthesizer, ReportPublisher) is False
    assert terminal_publisher(agents) is None


@pytest.mark.asyncio
async def test_an_unwired_graph_records_that_nothing_was_published() -> None:
    """No writer means no paths, no writes, and an honest recoverable error."""
    agents = fake_research_agents(publisher=None)

    state = await _run(agents)

    assert [error.error_type for error in state.errors] == [
        "graph_publication_unavailable"
    ]
    assert state.errors[0].recoverable is True
    assert state.report_path is None
    assert state.evidence_path is None
    # The Markdown is still the session's report, gate status and all.
    assert state.report == "# Research report: pass 1"
    published = [
        event
        for event in state.events
        if event.event_type == "graph.report.published"
    ]
    assert len(published) == 1
    assert published[0].metadata["report_path"] is None
    assert published[0].metadata["document_writes"] == 0


@pytest.mark.asyncio
async def test_a_halted_run_publishes_nothing() -> None:
    """A failed run ends at END, so the finalizer never runs."""
    publisher = FakePublisher()
    agents = fake_research_agents(
        fact_checker=FakeAgent(
            "fact_checker", [AgentConfigurationError("bad scratchpad")]
        ),
        publisher=publisher,
    )

    state = await _run(agents)

    assert state.errors
    assert _nodes_visited(state) == [
        "planner",
        "researcher",
        "source_evaluator",
        "fact_checker",
    ]
    assert publisher.documents == []
    assert publisher.claims == []
    assert state.report_path is None
    assert state.evidence_path is None
