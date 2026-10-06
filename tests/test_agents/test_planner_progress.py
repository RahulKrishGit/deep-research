"""Planning's live progress.

The planner publishes one live ``planner.progress`` right before each plan-side
request -- the draft, a repair, each review -- and stamps every slot's final
state on ``planner.planning.completed``. Progress events are live-only: they
never reach the planner's state update.
"""

from __future__ import annotations

import json

import pytest

from deep_research.agents.base import AgentRun
from deep_research.agents.planner import (
    ResearchPlan,
    ResearchPlanDraft,
    flagged_topic_ids,
    plan_progress,
    planning_completed_event,
)
from deep_research.agents.steps import ReActRun
from deep_research.graph.live import bind_live_sink
from deep_research.observability import Tracker
from deep_research.providers import ProviderTimeoutError
from deep_research.utils.types import ResearchEvent, SubTopic
from tests.agent_fakes import ScriptedCompleter, finish
from tests.test_agents.test_planner import (
    _draft,
    _plan,
    _planner,
    _review,
    _stale_draft,
    _state,
    _target,
)

SECRET = "SECRET-REVIEW-WORDS"


def _sub_topic(coverage_id: str, title: str) -> SubTopic:
    return SubTopic(
        coverage_id=coverage_id, title=title, rationale="r",
        search_queries=["q"], success_criteria=["c"], priority=1,
    )


def _topics(*titles: str) -> list[SubTopic]:
    return [_sub_topic(f"topic-{n:02d}", title) for n, title in enumerate(titles, start=1)]


def _states(metadata: dict) -> list[tuple[str, str]]:
    return [(entry["coverage_id"], entry["state"]) for entry in metadata["sub_topics"]]


def test_plan_progress_reads_each_slot_as_the_request_about_to_start_leaves_it() -> None:
    """Drafted before any review, checking while one runs, being_fixed while a
    flagged slot's repair runs, then passed -- or fixed when a repair changed it."""
    topics = _topics("Alpha", "Beta", "Gamma")

    assert plan_progress((), flagged=set(), repaired=set(), step="drafting", check_round=0) == {
        "step": "drafting", "check_round": 0, "sub_topics": [],
    }
    assert _states(plan_progress(topics, flagged={"topic-02"}, repaired=set(), step="fixing", check_round=0)) == [
        ("topic-01", "drafted"), ("topic-02", "being_fixed"), ("topic-03", "drafted"),
    ]
    assert _states(plan_progress(topics, flagged=set(), repaired={"topic-01"}, step="checking", check_round=1)) == [
        ("topic-01", "checking"), ("topic-02", "checking"), ("topic-03", "checking"),
    ]
    assert _states(plan_progress(topics, flagged={"topic-03"}, repaired={"topic-01"}, step="fixing", check_round=1)) == [
        ("topic-01", "fixed"), ("topic-02", "passed"), ("topic-03", "being_fixed"),
    ]
    entry = plan_progress(topics, flagged=set(), repaired=set(), step="checking", check_round=2)["sub_topics"][0]
    assert entry == {"coverage_id": "topic-01", "title": "Alpha", "state": "checking"}


def test_a_slot_title_is_capped_at_160_characters() -> None:
    [entry] = plan_progress(
        [_sub_topic("topic-01", "Battery " * 40)], flagged=set(), repaired=set(),
        step="checking", check_round=1,
    )["sub_topics"]
    assert len(entry["title"]) <= 160


def test_flagged_ids_are_the_plans_own_coverage_ids_named_in_the_text() -> None:
    """Only ids leave the helper; a target id names its topic, an unknown id is dropped."""
    topics = _topics("Alpha", "Beta")
    texts = [
        "topic-02-target-01 anchors currency to 2019",
        f"Split topic-01 into two; {SECRET}",
        "topic-09 is not in this plan; nor is subtopic-01",
    ]
    assert flagged_topic_ids(texts, topics) == {"topic-01", "topic-02"}


def test_planning_completed_carries_each_slots_final_state() -> None:
    """Every entry gains its final state; a note topic reads ``planned``; without
    states the event is exactly what it was."""
    plan = ResearchPlan(sub_topics=_topics("Alpha", "Beta"))
    note = _sub_topic("note-n1", "Your note: pastries at the cafés")
    outcome = AgentRun(
        agent_name="planner", result=plan,
        react=ReActRun(agent_name="planner", stop_reason="finished"),
        errors=[], state_update={},
    )

    plain = planning_completed_event(outcome, note_topics=[note])
    stamped = planning_completed_event(
        outcome, note_topics=[note], states={"topic-01": "fixed"},
    )

    assert all("state" not in entry for entry in plain.metadata["sub_topics"])
    assert [(e["coverage_id"], e["state"]) for e in stamped.metadata["sub_topics"]] == [
        ("topic-01", "fixed"), ("topic-02", "not_checked"), ("note-n1", "planned"),
    ]
    assert stamped.metadata["sub_topics"][2]["note_id"] == "n1"


def _flow(name: str) -> tuple[list[object], list[tuple[str, int, list[tuple[str, str]]]], list[tuple[str, str]]]:
    """(scripted outputs, expected progress events, expected final states) for one flow."""
    three = _plan("Cryptography", "Hardware timelines", "Mitigations")
    flagged_review = _review(
        sound=False,
        atomicity_defects=["topic-02 asks for two measures at once"],
        repair_instruction=f"Split topic-02-target-01 into two targets. {SECRET}",
    )
    repaired_three = ResearchPlanDraft(sub_topics=[
        _draft("Cryptography", priority=1),
        _draft("Hardware timelines", priority=2,
               evidence_targets=[_target("What qubit count does Alpha report for 2025?")]),
        _draft("Mitigations", priority=3),
    ])
    ids = ["topic-01", "topic-02", "topic-03"]
    if name == "sound on round 1":
        return (
            [three, _review()],
            [("drafting", 0, []), ("checking", 1, [(i, "checking") for i in ids])],
            [(i, "passed") for i in ids],
        )
    if name == "a local repair":
        stale_fixed = ResearchPlanDraft(sub_topics=[
            _draft("Interconnection", priority=1,
                   evidence_targets=[_target("What are the latest interconnection figures?")]),
            _draft("Queue totals", priority=2),
            _draft("Reforms", priority=3),
        ])
        return (
            [_stale_draft(), stale_fixed, _review()],
            [
                ("drafting", 0, []),
                ("fixing", 0, [("topic-01", "being_fixed"), ("topic-02", "drafted"), ("topic-03", "drafted")]),
                ("checking", 1, [(i, "checking") for i in ids]),
            ],
            [("topic-01", "fixed"), ("topic-02", "passed"), ("topic-03", "passed")],
        )
    if name == "a review repair, then sound":
        return (
            [three, flagged_review, repaired_three, _review()],
            [
                ("drafting", 0, []),
                ("checking", 1, [(i, "checking") for i in ids]),
                ("fixing", 1, [("topic-01", "passed"), ("topic-02", "being_fixed"), ("topic-03", "passed")]),
                ("checking", 2, [(i, "checking") for i in ids]),
            ],
            [("topic-01", "passed"), ("topic-02", "fixed"), ("topic-03", "passed")],
        )
    if name == "a failed review":
        return (
            [three, ProviderTimeoutError("timed out")],
            [("drafting", 0, []), ("checking", 1, [(i, "checking") for i in ids])],
            [(i, "not_checked") for i in ids],
        )
    if name == "a failed review repair":
        return (
            [three, flagged_review, ProviderTimeoutError("timed out")],
            [
                ("drafting", 0, []),
                ("checking", 1, [(i, "checking") for i in ids]),
                ("fixing", 1, [("topic-01", "passed"), ("topic-02", "being_fixed"), ("topic-03", "passed")]),
            ],
            [("topic-01", "passed"), ("topic-02", "flagged"), ("topic-03", "passed")],
        )
    raise AssertionError(name)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "flow",
    [
        "sound on round 1",
        "a local repair",
        "a review repair, then sound",
        "a failed review",
        "a failed review repair",
    ],
)
async def test_planner_progress_states(tracker: Tracker, flow: str) -> None:
    """One live planner.progress before each plan-side request,
    with the per-slot states, and each slot's final state on planning.completed."""
    outputs, expected_progress, expected_final = _flow(flow)
    completer = ScriptedCompleter(
        decisions=[finish("No lookup needed.", "Three angles matter.")], outputs=outputs,
    )
    agent = _planner(tracker, completer)
    question = (
        "What are the current interconnection constraints?"
        if flow == "a local repair" else "What are the security implications of quantum computing?"
    )
    received: list[ResearchEvent] = []

    async with tracker.session_span("session-1", "q"):
        with bind_live_sink(received.append):
            outcome = await agent.run(_state(question))

    progress = [e for e in received if e.event_type == "planner.progress"]
    assert [(e.metadata["step"], e.metadata["check_round"], _states(e.metadata)) for e in progress] == expected_progress
    completed = outcome.state_update["events"][-1]
    assert completed.event_type == "planner.planning.completed"
    assert _states(completed.metadata) == expected_final
    # Live-only, and never the review's own words.
    returned = {e.event_id for e in outcome.state_update["events"]}
    assert all(e.event_id not in returned for e in progress)
    assert all(SECRET not in json.dumps(e.metadata) for e in received)
