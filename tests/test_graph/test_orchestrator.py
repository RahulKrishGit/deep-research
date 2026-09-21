"""Tests for the compiled research graph: sequence, loop, bound, failure."""

from __future__ import annotations

import io
from datetime import datetime, timezone

import pytest

from deep_research.agents.critic import CriticAgent, fallback_critique
from deep_research.agents.errors import AgentConfigurationError
from deep_research.agents.planner import (
    PlannerAgent,
    ResearchPlanDraft,
)
from deep_research.agents.synthesizer import SynthesizerAgent
from deep_research.cli import EXIT_GRAPH_FAILED
from deep_research.cli import main as cli_main
from deep_research.graph.nodes import ReportPublisher
from deep_research.graph.orchestrator import (
    AGENT_NODE_ORDER,
    GraphRun,
    build_checkpointer,
    compile_research_graph,
    run_research_graph,
    session_config,
    terminal_publisher,
)
from deep_research.graph.state import (
    CRITIC_NODE,
    DEFAULT_MAX_ITERATIONS,
    FINALIZE_NODE,
    NODE_NAMES,
    REFINE_NODE,
    REPORT_REVIEW_NODE,
    graph_quality_status,
    graph_route,
    graph_status,
    initial_graph_state,
    is_halted,
    load_state,
)
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import Tracker
from deep_research.providers import ProviderResponseError
from deep_research.runtime.outcome import build_outcome
from deep_research.utils.config import AgentRuntimeConfig
from deep_research.utils.types import (
    QUALITY_STATUS_ACCEPTED,
    QUALITY_STATUS_PARTIAL,
    QUESTION_TARGET_ID,
    Critique,
    CritiqueGap,
    ResearchError,
    ResearchState,
)
from tests.agent_fakes import ScriptedCompleter, finish
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
    progressing_fact_checker,
)
from tests.research_fakes import planner_tools, synthesizer_tools
from tests.test_agents.test_planner import _draft, _review, _sorting_plan

QUESTION = "How mature is quantum error correction?"


async def _run(agents, *, max_iterations: int = 3) -> ResearchState:
    graph = compile_research_graph(agents)
    channel = initial_graph_state(
        session_id="session-1",
        question=QUESTION,
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


def _defect(coverage_id: str, target_id: str, problem: str) -> CritiqueGap:
    return CritiqueGap(
        coverage_id=coverage_id,
        target_ids=[target_id],
        kind="missing_support",
        severity="major",
        repair_action="acquire",
        problem=problem,
    )


def _two_defect_critique() -> Critique:
    """Two open obligations, so the next review can close one of them."""
    return Critique(
        score=4,
        gaps=[
            _defect("topic-01", "topic-01-target-01", "No cost data."),
            _defect("topic-02", "topic-02-target-01", "No financing data."),
        ],
        unsupported_claims=[],
        recommended_queries=[],
        should_continue=True,
        rationale="Two obligations are open.",
    )


def _one_defect_critique() -> Critique:
    """The first obligation is answered; the second is still open."""
    return Critique(
        score=5,
        gaps=[_defect("topic-02", "topic-02-target-01", "No financing data.")],
        unsupported_claims=[],
        recommended_queries=[],
        should_continue=True,
        rationale="One obligation is still open.",
    )


def _omission_critique() -> Critique:
    """One original-question omission, typed as a plan extension."""
    return Critique(
        score=5,
        gaps=[
            CritiqueGap(
                target_ids=[QUESTION_TARGET_ID],
                kind="coverage",
                severity="critical",
                repair_action="extend_plan",
                problem="The question asks for a cost the plan never targeted.",
            )
        ],
        unsupported_claims=[],
        recommended_queries=[],
        should_continue=True,
        rationale="The plan omits an obligation the question names.",
    )


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


@pytest.mark.asyncio
async def test_a_provider_outage_never_publishes_an_accepted_report() -> None:
    """End to end: an outage stops the run without an acceptance.

    An outage used to produce ``critique_satisfied``, ``completed``,
    ``accepted``, one memory write and CLI exit 0 — byte-identical to a genuine
    acceptance, because the outage fallback's floor score and empty gap list are
    exactly what an accepted critique looks like. The run now ends
    ``critique_failed``, ``failed``, ``partial``, with nothing remembered.
    """
    publisher = FakePublisher()
    outage, _ = fallback_critique(
        reason="provider_unavailable", iteration=0, max_iterations=3
    )
    agents = fake_research_agents(
        synthesizer=FakeAgent(
            "synthesizer", [], update_factory=fake_synthesis_update
        ),
        critic=FakeAgent(
            "critic",
            [
                {
                    "critique": outage,
                    "errors": [
                        {
                            "error_type": "critic_review_provider_error",
                            "source": "agent.critic",
                            "message": "the review call failed",
                            "timestamp": "2026-08-01T00:00:00+00:00",
                            "recoverable": False,
                            "details": {
                                "operation": "critic_report_review",
                                "provider_failure": {
                                    "kind": "provider_timeout",
                                    "exception_type": "ProviderTimeoutError",
                                },
                            },
                        }
                    ],
                }
            ],
        ),
        publisher=publisher,
    )

    state = await _run(agents)

    assert _route_reasons(state) == ["critique_failed"]
    assert graph_status(state) == "failed"
    assert state.quality is not None
    assert graph_quality_status(state) == QUALITY_STATUS_PARTIAL
    assert state.report_path is not None
    assert publisher.memory_writes == 0


@pytest.mark.asyncio
async def test_a_real_critic_provider_outage_fails_closed_through_cli(
    tracker: Tracker,
) -> None:
    """The outage path end to end: the real Critic, the graph, and the CLI.

    Everything else here scripts the Critic's output, so the branch Critical 1
    lives in — the agent's own ``except ProviderError`` — was never driven
    through the graph: one test drove a real ``CriticAgent`` outage, another
    drove the compiled graph with the fallback critique wrapped in a fake
    Critic, and neither connected them. Here the provider really fails inside
    the real agent, the fallback critique it produces is what the graph routes
    on, and the run must end ``critique_failed``/``failed``/``partial`` with
    nothing remembered and the CLI's graph-failure exit code.
    """
    provider = ScriptedCompleter(
        outputs=[
            ProviderResponseError(
                "provider unavailable",
                retryable=True,
                failure_category="http",
                http_status_code=503,
                failure_origin="sdk",
            )
        ]
    )
    critic = CriticAgent(
        provider=provider,
        tracker=tracker,
        scratchpad=ScratchpadMemory(
            session_id="session-1", agent_name="critic", max_entries=20
        ),
        tools=(),
        config=AgentRuntimeConfig(max_iterations=3, tool_budget=0),
    )
    publisher = FakePublisher()
    agents = fake_research_agents(
        synthesizer=FakeAgent(
            "synthesizer", [], update_factory=fake_synthesis_update
        ),
        critic=critic,
        publisher=publisher,
    )

    # Production's ``run_research_graph`` opens this span around
    # ``graph.ainvoke``: a real agent's child spans raise without it, which is
    # exactly how this test differs from the ones driving scripted agents.
    async with tracker.session_span("session-1", QUESTION):
        state = await _run(agents)

    assert provider.calls[0][0] == "CritiqueDraft"
    assert state.critique is not None
    assert state.critique.review_status == "failed"
    assert graph_route(state) == ("finalize", "critique_failed")
    assert graph_status(state) == "failed"
    assert graph_quality_status(state) == QUALITY_STATUS_PARTIAL
    assert publisher.memory_writes == 0

    outcome = build_outcome(
        GraphRun(
            session_id="session-1",
            state=state,
            status=graph_status(state),
            trace_url=None,
        ),
        metrics=(),
    )

    assert (
        cli_main([QUESTION], runner=lambda **_: outcome, stream=io.StringIO())
        == EXIT_GRAPH_FAILED
    )


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
    # Task 10 puts the terminal semantic review between the Critic and the
    # refinement hop: the report has to be judged before the route is chosen.
    assert NODE_NAMES == (
        *AGENT_NODE_ORDER,
        CRITIC_NODE,
        REPORT_REVIEW_NODE,
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
        REPORT_REVIEW_NODE,
        FINALIZE_NODE,
    ]
    assert state.report == "# Research report: pass 1"
    assert state.iteration == 0
    assert _route_reasons(state) == ["critique_satisfied"]
    assert graph_status(state) == "completed"
    # The report this double composes carries no typed composition, so there is
    # nothing a review can be tied to: the run publishes it, and records that
    # nothing judged it rather than calling it reviewed.
    assert [error.error_type for error in state.errors] == [
        "graph_report_review_unavailable"
    ]
    assert graph_quality_status(state) == QUALITY_STATUS_PARTIAL


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
        REPORT_REVIEW_NODE,
        "researcher",
        "source_evaluator",
        "fact_checker",
        "synthesizer",
        CRITIC_NODE,
        REPORT_REVIEW_NODE,
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
        fact_checker=progressing_fact_checker(),
        critic=FakeAgent(
            "critic", [{"critique": fake_critique(should_continue=True, score=3)}]
        ),
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
    assert skipped == ["synthesizer", "critic", "report_review"]


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
        "researcher_sub_topic_without_findings",
        # The same pass composed no typed composition, so the terminal review
        # recorded that nothing judged it. That is a recoverable assessment
        # failure, not a graph failure — the run still completes.
        "graph_report_review_unavailable",
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
                # A real critic closes defects as they are repaired, and one
                # resolution per pass is what keeps this run making progress
                # rather than merely spending budget.
                {"critique": _two_defect_critique()},
                {"critique": _one_defect_critique()},
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
    assert publisher.report_writes == 3  # reader, evidence, quality: terminal only
    assert final.report_path == "report-session-1-3.md"
    assert final.evidence_path == "report-session-1-3-evidence.md"
    assert final.quality_path == "report-session-1-3-quality.json"
    assert graph_status(final) == "completed"


@pytest.mark.asyncio
async def test_a_stalled_refinement_stops_before_a_second_unchanged_pass() -> None:
    """End to end: a repair that changes nothing does not buy another pass.

    Every fake here answers with the same state on every pass, so the second
    refinement compares two identical snapshots. The run must stop there — at
    the refinement hop, without spending a third research pass — publish the
    report it has, and say why in a reason that is not a quality verdict: the
    report was reviewed, and the machine declined to repeat work that would
    change nothing.
    """
    publisher = FakePublisher()
    agents = fake_research_agents(
        critic=FakeAgent(
            "critic", [{"critique": fake_critique(should_continue=True, score=4)}]
        ),
        publisher=publisher,
    )

    state = await _run(agents, max_iterations=3)

    assert _route_reasons(state) == [
        "refinement_requested",
        "refinement_requested",
        "no_progress",
    ]
    assert state.iteration == 1
    assert len(agents.researcher.calls) == 2
    assert state.repair_stop_reason == "no_progress"
    assert graph_status(state) == "incomplete"
    assert graph_quality_status(state) == QUALITY_STATUS_PARTIAL
    # The report it had is still published; a partial run is not remembered.
    assert state.report_path is not None
    assert publisher.memory_writes == 0


@pytest.mark.asyncio
async def test_an_original_question_omission_reaches_the_planner_and_is_researched(
    tracker: Tracker,
) -> None:
    """End to end: the typed route is dispatched, so the omission is repaired.

    A real Planner runs in the graph. Pass 0 plans; the Critic then expresses an
    original-question omission as an ``extend_plan`` job over the ``question``
    sentinel. The refinement hop must route that job to the Planner, the
    Planner must *extend* rather than re-plan, the added target must join the
    expanded inventory, and the research pass that follows must see the new
    topic — the three halves of "routes to Planner extension ... increases ...
    the coverage inventory, and then researches the new target".
    """
    completer = ScriptedCompleter(
        decisions=[finish("No lookup needed.", "Three angles matter.")],
        outputs=[
            _sorting_plan(),
            _review(),
            ResearchPlanDraft(
                sub_topics=[_draft("Levelised cost per tonne", priority=4)]
            ),
        ],
    )
    planner = PlannerAgent(
        provider=completer,
        tracker=tracker,
        scratchpad=ScratchpadMemory(
            session_id="session-1", agent_name="planner", max_entries=20
        ),
        tools=planner_tools(tracker),
        config=AgentRuntimeConfig(max_iterations=3, tool_budget=3),
        clock=lambda: datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc),
    )
    agents = fake_research_agents(
        planner=planner,
        critic=FakeAgent(
            "critic",
            [
                {"critique": _omission_critique()},
                {"critique": fake_critique(should_continue=False, score=9)},
            ],
        ),
    )

    run = await run_research_graph(
        graph=compile_research_graph(agents),
        tracker=tracker,
        session_id="session-1",
        question="What limits battery storage deployment?",
        max_iterations=3,
    )
    state = run.state

    assert [name for name, _, _ in completer.calls] == [
        "ResearchPlanDraft",
        "PlanReviewDraft",
        "PlanExtensionDraft",
    ]
    assert state.expanded_target_ids == ["topic-04-target-01"]
    assert [topic.coverage_id for topic in state.sub_topics] == [
        "topic-01",
        "topic-02",
        "topic-03",
        "topic-04",
    ]
    # The pass that followed the extension researched the added topic.
    assert len(agents.researcher.calls) == 2
    assert [topic.coverage_id for topic in agents.researcher.calls[1].sub_topics] == [
        "topic-01",
        "topic-02",
        "topic-03",
        "topic-04",
    ]


@pytest.mark.asyncio
async def test_a_provider_failure_in_the_routed_extension_does_not_halt_the_run(
    tracker: Tracker,
) -> None:
    """An outage mid-loop is a recorded fact, not grounds to discard a report.

    The extension runs after a report already exists, so halting would publish
    nothing: ``failed`` with ``report_path=None``. Every other agent records a
    provider outage and survives it, and the repair vocabulary this task added
    says ``provider_failure`` → ``incomplete`` — the planner is not exempt just
    because its failure arrives as a ``PlanningError``.
    """
    completer = ScriptedCompleter(
        decisions=[finish("No lookup needed.", "Three angles matter.")],
        outputs=[
            _sorting_plan(),
            _review(),
            ProviderResponseError(
                "the provider returned a 503",
                failure_origin="sdk",
                retryable=True,
                failure_category="http",
                http_status_code=503,
            ),
        ],
    )
    planner = PlannerAgent(
        provider=completer,
        tracker=tracker,
        scratchpad=ScratchpadMemory(
            session_id="session-1", agent_name="planner", max_entries=20
        ),
        tools=planner_tools(tracker),
        config=AgentRuntimeConfig(max_iterations=3, tool_budget=3),
        clock=lambda: datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc),
    )
    publisher = FakePublisher()
    agents = fake_research_agents(
        planner=planner,
        critic=FakeAgent(
            "critic",
            # The same omission every pass: nothing but the failed extension
            # moves, so the pass just finished has no progress to report and
            # the outage is what the stop reason must name.
            [{"critique": _omission_critique()}],
        ),
        publisher=publisher,
    )

    run = await run_research_graph(
        graph=compile_research_graph(agents),
        tracker=tracker,
        session_id="session-1",
        question="What limits battery storage deployment?",
        max_iterations=3,
    )
    state = run.state

    assert is_halted(state) is False
    assert graph_status(state) == "incomplete"
    assert state.repair_stop_reason == "provider_failure"
    assert any("provider" in error.error_type for error in state.errors)
    # The report the run already held is still published.
    assert state.report_path is not None
    assert publisher.report_writes == 3


@pytest.mark.asyncio
async def test_a_gate_failure_sends_a_critic_approved_report_back() -> None:
    """Step 2 end to end: the deterministic gate outranks the model's score."""
    agents = fake_research_agents(
        source_evaluator=FakeAgent(
            "source_evaluator",
            [
                # A source evaluator that makes progress — it assesses a new
                # source — while the duplicate-row defect it was sent back for
                # persists. Both halves matter: the gate must keep failing, and
                # the pass must not look stalled, because the assessed set did
                # change.
                {
                    "evaluated_sources": [
                        fake_scored_source("https://example.org/a"),
                        fake_scored_source("https://example.org/a"),
                    ]
                },
                {
                    "evaluated_sources": [
                        fake_scored_source("https://example.org/a"),
                        fake_scored_source("https://example.org/a"),
                        fake_scored_source("https://example.org/b"),
                    ]
                },
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
    assert published[0].metadata["document_writes"] == 3
    assert publisher.memory_writes == 1
    assert "**Quality status:** accepted" in (state.report or "")
    # The judgement that made it accepted must still be on the state. A review
    # the merge dropped would leave the published badge reading `accepted`
    # beside a run whose own status computes `partial` — and nothing asserted
    # this, so a double that never carried the composition fingerprint could
    # silently discard every review the graph recorded.
    from deep_research.agents.report_review import semantic_review_passes

    assert state.report_review is not None
    assert state.report_review.status == "scored"
    assert semantic_review_passes(state.report_review)


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
        "graph_report_review_unavailable",
        "graph_publication_unavailable",
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
