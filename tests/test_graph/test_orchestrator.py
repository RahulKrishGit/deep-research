"""Tests for the compiled research graph: sequence, loop, bound, failure."""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path

import pytest

from deep_research.agents.errors import PlanningError
from deep_research.agents.report_writer import (
    REPORT_WRITER_NAME,
    ReportWriterAgent,
)
from deep_research.agents.evidence_verifier import (
    EVIDENCE_VERIFIER_NAME,
    EvidenceVerifierAgent,
    StatementCheckDraft,
    StatementVerdictDraft,
)
from deep_research.graph.nodes import ReportPublisher
from deep_research.graph.orchestrator import (
    AGENT_NODE_ORDER,
    build_checkpointer,
    compile_research_graph,
    session_config,
    terminal_publisher,
)
from deep_research.graph.state import (
    EVIDENCE_VERIFIER_NODE,
    EXTRA_PASS_NODE,
    FINALIZE_NODE,
    NODE_NAMES,
    NOTE_PASS_NODE,
    NOTE_REDRAFT_STEPS,
    PLANNER_NODE,
    REDRAFT_NODE,
    REPORT_REVIEWER_NODE,
    REPORT_WRITER_NODE,
    RESEARCHER_NODE,
    SOURCE_EVALUATOR_NODE,
    graph_quality_status,
    graph_status,
    initial_graph_state,
    is_halted,
    load_state,
)
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import Tracker
from deep_research.providers import ProviderResponseError
from deep_research.utils.config import AgentRuntimeConfig
from deep_research.utils.types import (
    MAX_NOTES_PER_RUN,
    QUALITY_STATUS_ACCEPTED,
    QUALITY_STATUS_PARTIAL,
    BottomLineDraft,
    ResearchState,
    SectionDraft,
    WriterPointDraft,
)
from tests.agent_fakes import ScriptedCompleter
from tests.graph_fakes import (
    SNIPPET,
    FakeAgent,
    FakePublisher,
    FakeReviewer,
    fake_report_review,
    fake_research_agents,
    fake_sub_topic,
    fake_target,
    fake_writer_update,
    verified_pass,
)
from tests.research_fakes import report_writer_tools

QUESTION = "How mature is quantum error correction?"


def _two_target_topic():
    """One planned topic with one answerable target and one still owed."""
    return fake_sub_topic(
        targets=[
            fake_target(),
            fake_target("topic-01-target-02", question="What did it cost?"),
        ]
    )


async def _run(agents, *, max_extra_passes: int = 1) -> ResearchState:
    graph = compile_research_graph(agents)
    channel = initial_graph_state(
        session_id="session-1",
        question=QUESTION,
        max_extra_passes=max_extra_passes,
    )
    result = await graph.ainvoke(
        channel,
        session_config("session-1", max_extra_passes=max_extra_passes),
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


def _statement_check_reply(messages: list, schema: type) -> StatementCheckDraft:
    """Answer every statement in the request with a plain 'consistent' verdict.

    Reads the batch's own labels back out of the request body, so it answers
    correctly whichever batch the real Statement Check hands it -- a part's
    ``P{part:02d}.{n}`` flight keys or the bottom line's ``B{n}`` (spec §6.7),
    before either is renumbered ``S001…``.
    """
    del schema
    return StatementCheckDraft(
        statements=[
            StatementVerdictDraft(
                label=label, verdict="consistent", reason="Matches the findings."
            )
            for label in re.findall(r"## (\S+)", messages[-1].content)
        ]
    )


def _writer_replies(count: int = 1) -> list[object]:
    """One writing pass's four scripted provider replies, repeated ``count`` times.

    Each pass drafts its one part's section, checks it, drafts the bottom
    line, then checks that -- the sequence ``compose_written_report`` runs
    for a single-part task (spec §6.5-§6.7). ``F01`` is the label
    ``finding_registry`` stamps on the one verified finding these fixtures
    carry, so a draft written here is exactly what the writer's own registry
    offers the model.
    """
    replies: list[object] = []
    for _ in range(count):
        replies.extend([
            SectionDraft(
                title="Findings",
                points=[WriterPointDraft(text=SNIPPET, finding_labels=["F01"])],
            ),
            _statement_check_reply,
            BottomLineDraft(
                sentences=[WriterPointDraft(text=SNIPPET, finding_labels=["F01"])]
            ),
            _statement_check_reply,
        ])
    return replies


def _real_writer(
    tracker: Tracker,
    tmp_path: Path,
    *,
    replies: Sequence[object],
) -> ReportWriterAgent:
    """The production Report Writer, scripted with one pass's worth of
    replies (``_writer_replies``) per writing pass.

    ``ScriptedCompleter`` consumes ``outputs`` in call order: a part's
    section draft, its Statement Check reply, the bottom-line draft, then its
    Statement Check reply -- the real writer's own per-pass call sequence.
    """
    return ReportWriterAgent(
        provider=ScriptedCompleter(outputs=list(replies)),
        tracker=tracker,
        scratchpad=ScratchpadMemory(
            session_id="session-1", agent_name=REPORT_WRITER_NAME, max_entries=20
        ),
        tools=report_writer_tools(tracker, output_root=tmp_path),
        config=AgentRuntimeConfig(max_iterations=2, tool_budget=0),
    )


def _writer_agents(
    tracker: Tracker,
    tmp_path: Path,
    *,
    publisher: ReportPublisher | None = None,
    replies: Sequence[object] | None = None,
    pass_: object | None = None,
    **overrides: object,
):
    """A run whose writing slot is the real Report Writer, everything else a double.

    The writer drafts through its own prompt path, composes through
    ``compose_written_report`` and runs the Statement Check; the other four
    agents and the reviewer are scripted so the test needs no provider. Every
    slot is overridable, which is how a test replaces the verifier with the
    real agent or makes the researcher find nothing on its second pass.
    """
    one = pass_ or verified_pass()
    defaults: dict[str, object] = {
        "researcher": FakeAgent("researcher", [one.update()]),  # type: ignore[attr-defined]
        "evidence_verifier": FakeAgent(
            EVIDENCE_VERIFIER_NAME, [one.verified_update()]  # type: ignore[attr-defined]
        ),
        "report_writer": _real_writer(
            tracker, tmp_path, replies=list(replies or _writer_replies())
        ),
        "publisher": publisher,
    }
    defaults.update(overrides)
    return fake_research_agents(**defaults), one



@pytest.mark.asyncio
async def test_run_publishes_when_the_context_check_fails(
    tracker: Tracker, tmp_path: Path
) -> None:
    """Review Focus 2: a failed Context Check batch never stops a run.

    The verifier's only batch raises ``ProviderError`` inside the real
    ``EvidenceVerifierAgent``, and the report is written by the real
    ``ReportWriterAgent`` — drafting through a scripted completer and having
    its drafted sentence checked. D8's keep rule then decides each figure on
    the finding's own snippet: a figure its snippet states is kept as
    *unchecked context* — its fact row carries that flag and the ledger
    names it in the finding's own record (the reader no longer has a key
    facts table to flag it in) — and the run publishes. An outage is never a
    graph failure, and never an acceptance of anything the check did not
    judge.
    """
    one = verified_pass()
    publisher = FakePublisher()
    verifier = EvidenceVerifierAgent(
        provider=ScriptedCompleter(
            outputs=[
                ProviderResponseError(
                    "provider unavailable",
                    retryable=True,
                    failure_category="http",
                    http_status_code=503,
                    failure_origin="sdk",
                )
            ]
        ),
        tracker=tracker,
        scratchpad=ScratchpadMemory(
            session_id="session-1",
            agent_name=EVIDENCE_VERIFIER_NAME,
            max_entries=20,
        ),
        tools=(),
        config=AgentRuntimeConfig(max_iterations=2, tool_budget=0),
    )
    agents, _ = _writer_agents(
        tracker,
        tmp_path,
        pass_=one,
        publisher=publisher,
        researcher=FakeAgent("researcher", [one.update()]),
        evidence_verifier=verifier,
    )

    # Production's ``run_research_graph`` opens this span around
    # ``graph.ainvoke``: a real agent's child spans raise without it, which is
    # exactly how this test differs from the ones driving scripted agents.
    async with tracker.session_span("session-1", QUESTION):
        state = await _run(agents)

    assert graph_status(state) in {"completed", "incomplete"}
    assert graph_status(state) != "failed"
    assert not is_halted(state)
    verified = state.verified_findings[0]
    assert verified.verification is not None
    assert verified.verification.status == "verified"
    assert verified.verification.context_unchecked is True
    assert [error.error_type for error in state.errors] == [
        "evidence_verifier_context_check_failed"
    ]
    assert publisher.report_writes == 3
    reader = publisher.document_named("report-session-1-0.md")[1]
    ledger = publisher.document_named("-evidence.md")[1]
    quality = publisher.document_named("-quality.json")[1]
    assert state.composition is not None
    assert state.composition.fact_rows[0].context_unchecked is True
    assert "context unchecked" in ledger
    # What these two assertions check: the sentence the drafted point carried
    # reached the reader unchanged (its citation marker lands before the
    # final stop, spec §3.1 rule 3, so the check strips it) — a sentence the
    # Statement Check refused would drop SNIPPET from the report — and every
    # sentence the report prints carries the consistent verdict that check
    # returned.
    assert SNIPPET.rstrip(".") in reader
    assert set(state.composition.statement_verdicts.values()) == {"consistent"}
    assert quality
    assert state.report_path == "report-session-1-0.md"
    assert state.quality is not None
    assert state.quality.context_unchecked_findings == 1


@pytest.mark.asyncio
async def test_extra_pass_that_finds_nothing_publishes_with_not_found(
    tracker: Tracker, tmp_path: Path
) -> None:
    """Review Focus 3: one extra pass, then the target under What we couldn't confirm.

    A required target no finding answers: the reviewer node stamps it missing,
    the graph buys exactly one extra pass confined to that target, the second
    pass finds nothing, the second review still names it, and no gate fails —
    the target is listed in the composition's ``not_found`` and printed under
    the reader report's "What we couldn't confirm" section (spec §3.1, §10).
    So the run finalizes once, accepted (PD-23), and the researcher was
    called exactly twice. Both passes' reports are composed by the real
    ``ReportWriterAgent``, which is what makes that section a real writer's
    output rather than a fixture's.
    """
    one = verified_pass()
    publisher = FakePublisher()
    researcher = FakeAgent(
        "researcher", [one.update(), {"raw_findings": []}]
    )
    agents, _ = _writer_agents(
        tracker,
        tmp_path,
        pass_=one,
        replies=_writer_replies(2),
        publisher=publisher,
        planner=FakeAgent("planner", [{"sub_topics": [_two_target_topic()]}]),
        researcher=researcher,
        report_reviewer=FakeReviewer(
            [
                fake_report_review(),
                fake_report_review(fingerprint="packet-2"),
            ]
        ),
    )

    # The real writer's own API calls need the active session span the
    # orchestrator always runs a graph inside.
    async with tracker.session_span("session-1", QUESTION):
        state = await _run(agents)

    assert len(researcher.calls) == 2
    assert researcher.calls[1].extra_pass_target_ids == ["topic-01-target-02"]
    assert state.iteration == 1
    assert _route_reasons(state) == ["extra_pass_requested", "report_accepted"]
    assert _nodes_visited(state) == [
        PLANNER_NODE,
        RESEARCHER_NODE,
        SOURCE_EVALUATOR_NODE,
        EVIDENCE_VERIFIER_NODE,
        REPORT_WRITER_NODE,
        REPORT_REVIEWER_NODE,
        EXTRA_PASS_NODE,
        RESEARCHER_NODE,
        SOURCE_EVALUATOR_NODE,
        EVIDENCE_VERIFIER_NODE,
        REPORT_WRITER_NODE,
        REPORT_REVIEWER_NODE,
        FINALIZE_NODE,
    ]
    assert graph_status(state) == "completed"
    assert graph_quality_status(state) == QUALITY_STATUS_ACCEPTED
    assert state.quality is not None
    assert state.quality.missing_required_target_ids == ["topic-01-target-02"]
    assert state.report_review is not None
    assert state.report_review.missing_required_target_ids == ["topic-01-target-02"]
    assert state.report is not None
    assert state.composition is not None
    assert [target.target_id for target in state.composition.not_found] == [
        "topic-01-target-02"
    ]
    assert "## What we couldn't confirm" in state.report
    assert "What did it cost?" in state.report
    assert SNIPPET.rstrip(".") in state.report
    # One publication: three documents, whatever the loop did before it.
    assert publisher.report_writes == 3
    assert state.report_path == "report-session-1-1.md"


def test_the_agent_node_order_matches_the_designed_sequence() -> None:
    assert AGENT_NODE_ORDER == (
        "planner",
        "researcher",
        "source_evaluator",
        "evidence_verifier",
        "report_writer",
    )
    # Order matters, not just membership: the graph's real edges read
    # ``AGENT_NODE_ORDER``, so it must be exactly the head of ``NODE_NAMES``,
    # with the reviewer, the three hops and the finalizer after it.
    assert NODE_NAMES == (
        *AGENT_NODE_ORDER,
        REPORT_REVIEWER_NODE,
        NOTE_PASS_NODE,
        EXTRA_PASS_NODE,
        REDRAFT_NODE,
        FINALIZE_NODE,
    )


@pytest.mark.asyncio
async def test_the_happy_path_runs_every_agent_once_in_order() -> None:
    agents = fake_research_agents()

    state = await _run(agents)

    assert _nodes_visited(state) == [
        *AGENT_NODE_ORDER,
        REPORT_REVIEWER_NODE,
        FINALIZE_NODE,
    ]
    assert len(agents.planner.calls) == 1
    assert len(agents.researcher.calls) == 1
    assert len(agents.evidence_verifier.calls) == 1
    assert state.iteration == 0
    assert state.extra_pass_target_ids == []
    assert _route_reasons(state) == ["report_accepted"]
    assert graph_status(state) == "completed"
    assert graph_quality_status(state) == QUALITY_STATUS_ACCEPTED
    assert state.report_path is not None


@pytest.mark.asyncio
async def test_a_review_that_never_happened_publishes_partial() -> None:
    """A review that never happened publishes honestly as partial, never failed."""
    publisher = FakePublisher()
    agents = fake_research_agents(
        publisher=publisher,
        report_reviewer=FakeReviewer([fake_report_review(status="provider_failed")]),
    )

    state = await _run(agents)

    assert _route_reasons(state) == ["review_unavailable"]
    assert graph_status(state) == "incomplete"
    assert graph_quality_status(state) == QUALITY_STATUS_PARTIAL
    assert state.report_path is not None
    assert publisher.memory_writes == 0
    assert [error.error_type for error in state.errors] == [
        "graph_report_review_unavailable"
    ]


@pytest.mark.asyncio
async def test_a_rejected_report_never_reaches_memory() -> None:
    """A scored review that refuses the report is published, never accepted."""
    publisher = FakePublisher()
    agents = fake_research_agents(
        publisher=publisher,
        report_reviewer=FakeReviewer(
            [
                fake_report_review(
                    dimensions={name: 0.4 for name in _dimension_names()}
                )
            ]
        ),
    )

    state = await _run(agents)

    assert _route_reasons(state) == ["report_not_accepted"]
    assert graph_status(state) == "incomplete"
    assert graph_quality_status(state) == QUALITY_STATUS_PARTIAL
    assert publisher.report_writes == 3
    assert publisher.memory_writes == 0


def _dimension_names() -> tuple[str, ...]:
    from deep_research.utils.types import REVIEW_DIMENSIONS

    return REVIEW_DIMENSIONS


@pytest.mark.asyncio
async def test_state_replaces_canonical_snapshots_and_appends_the_rest() -> None:
    """Evidence channels land the pass's snapshot; nothing piles up twice.

    ``evaluated_sources`` and ``verified_findings`` replace, so the producers —
    Source Evaluator and Evidence Verifier — emit their whole snapshot each
    pass, and a second pass that re-verifies the same finding leaves one entry.
    """
    one = verified_pass()
    agents = fake_research_agents(
        researcher=FakeAgent(
            "researcher",
            [
                one.update(),
                {"raw_findings": [one.finding], "read_records": {one.read.read_id: one.read}},
            ],
        ),
        evidence_verifier=FakeAgent(
            "evidence_verifier",
            [one.verified_update(), one.verified_update()],
        ),
        planner=FakeAgent("planner", [{"sub_topics": [_two_target_topic()]}]),
        report_writer=FakeAgent(
            REPORT_WRITER_NAME, [], update_factory=fake_writer_update
        ),
        report_reviewer=FakeReviewer(
            [fake_report_review(), fake_report_review(fingerprint="packet-2")]
        ),
    )

    state = await _run(agents)

    # ``verified_findings`` and ``evaluated_sources`` replace: the verifier
    # re-emitting the same snapshot leaves one entry. ``raw_findings`` is a
    # log, so the same finding recorded twice is recorded twice.
    assert len(state.verified_findings) == 1
    assert len(state.evaluated_sources) == 1
    assert len(state.raw_findings) == 2


@pytest.mark.asyncio
async def test_the_shipped_default_budget_completes_without_a_recursion_error() -> None:
    """The default one extra pass, with the graph's own superstep bound."""
    agents = fake_research_agents()

    state = await _run(agents, max_extra_passes=1)

    assert state.max_extra_passes == 1
    assert graph_status(state) == "completed"


@pytest.mark.asyncio
async def test_an_agent_failure_stops_the_run_and_keeps_what_was_collected() -> None:
    from deep_research.agents.errors import AgentConfigurationError

    agents = fake_research_agents(
        source_evaluator=FakeAgent(
            "source_evaluator", [AgentConfigurationError("no reputation source")]
        )
    )

    state = await _run(agents)

    assert is_halted(state)
    assert graph_status(state) == "failed"
    # Everything collected before the failure survives, and no later node ran.
    assert state.raw_findings
    assert state.verified_findings == []
    assert state.report_path is None
    assert _nodes_visited(state) == [
        PLANNER_NODE,
        RESEARCHER_NODE,
        SOURCE_EVALUATOR_NODE,
    ]


@pytest.mark.asyncio
async def test_a_recoverable_agent_error_never_stops_the_graph() -> None:
    from deep_research.utils.types import ResearchError

    outage = ResearchError(
        error_type="evidence_verifier_context_check_failed",
        source="agent.evidence_verifier",
        message="The Context Check failed for one batch.",
        recoverable=False,
    )
    one = verified_pass()
    agents = fake_research_agents(
        evidence_verifier=FakeAgent(
            "evidence_verifier",
            [{"errors": [outage], **one.verified_update()}],
        )
    )

    state = await _run(agents)

    assert not is_halted(state)
    assert graph_status(state) in {"completed", "incomplete"}
    assert [error.error_type for error in state.errors] == [
        "evidence_verifier_context_check_failed"
    ]


@pytest.mark.asyncio
async def test_an_agent_that_returns_invalid_state_fails_the_run() -> None:
    agents = fake_research_agents(
        researcher=FakeAgent("researcher", [{"iteration": 1}])
    )

    state = await _run(agents)

    assert is_halted(state)
    assert graph_status(state) == "failed"
    assert [error.error_type for error in state.errors] == [
        "graph_invalid_agent_state"
    ]


def test_the_session_config_pins_the_thread_and_the_superstep_bound() -> None:
    config = session_config("session-1", max_extra_passes=2)

    assert config["configurable"]["thread_id"] == "session-1"
    assert config["recursion_limit"] == (
        (2 + 1) * len(NODE_NAMES)
        + MAX_NOTES_PER_RUN * (len(NODE_NAMES) + NOTE_REDRAFT_STEPS)
        + 10
    )


def test_a_checkpointer_is_built_only_when_it_is_asked_for() -> None:
    assert build_checkpointer(enabled=False) is None
    assert build_checkpointer(enabled=True) is not None


@pytest.mark.asyncio
async def test_an_accepted_run_publishes_three_artifacts_and_one_memory_entry() -> None:
    publisher = FakePublisher()
    agents = fake_research_agents(publisher=publisher)

    state = await _run(agents)

    assert publisher.written_paths == [
        "report-session-1-0.md",
        "report-session-1-0-evidence.md",
        "report-session-1-0-quality.json",
    ]
    assert publisher.memory_writes == 1
    assert publisher.saved_findings == [SNIPPET]
    assert state.report_path == publisher.written_paths[0]


def test_the_production_writer_is_the_publisher_when_none_is_wired(
    tracker: Tracker, tmp_path: Path
) -> None:
    agents, _ = _writer_agents(tracker, tmp_path)

    assert isinstance(agents.report_writer, ReportPublisher)
    assert terminal_publisher(agents) is agents.report_writer


def test_an_explicit_publisher_slot_wins_over_the_writer(
    tracker: Tracker, tmp_path: Path
) -> None:
    publisher = FakePublisher()
    agents, _ = _writer_agents(tracker, tmp_path, publisher=publisher)

    assert terminal_publisher(agents) is publisher


def test_a_run_only_double_is_not_a_publisher() -> None:
    agents = fake_research_agents(
        report_writer=FakeAgent(REPORT_WRITER_NAME, [{}]), publisher=None
    )

    assert terminal_publisher(agents) is None


@pytest.mark.asyncio
async def test_an_unwired_graph_records_that_nothing_was_published() -> None:
    agents = fake_research_agents(
        report_writer=FakeAgent(
            REPORT_WRITER_NAME, [], update_factory=fake_writer_update
        ),
        publisher=None,
    )

    state = await _run(agents)

    assert state.report is not None
    assert state.report_path is None
    assert state.evidence_path is None
    assert state.quality_path is None
    assert "graph_publication_unavailable" in {
        error.error_type for error in state.errors
    }


@pytest.mark.asyncio
async def test_a_halted_run_publishes_nothing() -> None:
    publisher = FakePublisher()
    agents = fake_research_agents(
        evidence_verifier=FakeAgent(
            "evidence_verifier", [PlanningError("the verification pass failed")]
        ),
        publisher=publisher,
    )

    state = await _run(agents)

    assert graph_status(state) == "failed"
    assert publisher.documents == []
    assert state.report_path is None
