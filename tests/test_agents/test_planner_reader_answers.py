"""The reader's answers to the one-time check, as the planner reads them (live-briefs spec §4.4)."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from deep_research.agents.planner import (
    derive_answer_contract,
    plan_messages,
    plan_review_messages,
    render_reader_answers,
)
from deep_research.agents.prompts import AgentTask
from deep_research.e2e_evaluation.replay import (
    build_replay_runtime,
    production_config_path,
    replay_settings,
)
from deep_research.e2e_evaluation.replay_matrix import scenario_by_id
from deep_research.main import run_research
from deep_research.observability import Tracker
from deep_research.utils.config import ConfigSettings
from deep_research.utils.types import ReaderAnswer, ResearchState
from tests.agent_fakes import ScriptedCompleter, finish
from tests.test_api.replay_support import EXTRA_PASS_CASE, guarded
from tests.test_agents.test_planner import (
    _CLOCK_NOW,
    _planner,
    _review,
    _run,
    _sorting_plan,
)

QUESTION = "What are the current constraints on grid-scale battery storage deployment?"
REGION = ReaderAnswer(
    question_id="q1", dimension="geography", text="Which region should this cover?",
    short="Region", value="European Union", source="chosen",
)
PERIOD = ReaderAnswer(
    question_id="q2", dimension="period", text="How recent should the sources be?",
    short="Period", value="since 2021", source="typed",
)
PURPOSE = ReaderAnswer(
    question_id="q3", dimension="purpose", text="What will you use it for?",
    short="For", value="General understanding", source="best_guess",
)
ANSWERS = (REGION, PERIOD, PURPOSE)


def test_the_contract_takes_the_readers_scope_period_and_assumption_lines() -> None:
    plain = derive_answer_contract(question=QUESTION, now=_CLOCK_NOW)
    contract = derive_answer_contract(question=QUESTION, now=_CLOCK_NOW, reader_answers=ANSWERS)

    assert plain.geographic_scope == "unspecified"
    assert contract.geographic_scope == "European Union"
    assert contract.evidence_period_requirement == "since 2021"
    assert contract.assumptions == [
        "Reader said: Region = European Union",
        "Reader said: Period = since 2021",
        "Assumed (best guess): For = General understanding",
    ]
    assert "answered for European Union" in contract.scope_statement
    assert contract.scope_statement.endswith("evidence period: since 2021.")
    assert (contract.question, contract.as_of_date, contract.answer_kind) == (
        plain.question, plain.as_of_date, plain.answer_kind,
    )


def test_a_question_that_names_its_geography_keeps_its_assumptions_and_gains_the_lines() -> None:
    question = "What limits battery storage deployment in the United States?"
    purpose_only = derive_answer_contract(question=question, now=_CLOCK_NOW, reader_answers=(PURPOSE,))
    plain = derive_answer_contract(question=question, now=_CLOCK_NOW)

    assert purpose_only.geographic_scope == plain.geographic_scope == "United States"
    assert purpose_only.evidence_period_requirement == plain.evidence_period_requirement
    assert purpose_only.assumptions == [*plain.assumptions, "Assumed (best guess): For = General understanding"]


def test_no_answers_derive_exactly_the_contract_the_question_alone_yields() -> None:
    assert derive_answer_contract(question=QUESTION, now=_CLOCK_NOW, reader_answers=()) == derive_answer_contract(
        question=QUESTION, now=_CLOCK_NOW
    )


def test_the_reader_answers_section_says_who_gave_each_answer() -> None:
    assert render_reader_answers(ANSWERS).splitlines() == [
        "Before planning, the reader answered a short check about what the question "
        "leaves open. Plan within these answers: a narrowing the reader asked for is "
        "not a missing part of the question.",
        "- Which region should this cover? European Union (the reader's answer)",
        "- How recent should the sources be? since 2021 (the reader's answer)",
        "- What will you use it for? General understanding (a best guess; the reader did not answer)",
    ]


def test_the_plan_request_carries_reader_answers_after_the_contract_only_when_there_are_any() -> None:
    contract = derive_answer_contract(question=QUESTION, now=_CLOCK_NOW, reader_answers=ANSWERS)
    task = AgentTask(instruction=QUESTION)
    with_answers = plan_messages(task, _run(), contract=contract, reader_answers=ANSWERS)[1].content
    without = plan_messages(task, _run(), contract=contract)[1].content

    assert with_answers.index("# Answer contract\n") < with_answers.index("# Reader answers\n") < with_answers.index("# Scoping notes\n")
    assert f"# Reader answers\n{render_reader_answers(ANSWERS)}\n" in with_answers
    assert "# Reader answers" not in without
    assert plan_messages(task, _run(), contract=contract, reader_answers=()) == plan_messages(task, _run(), contract=contract)


def test_the_plan_review_carries_the_same_section_only_when_there_are_answers() -> None:
    contract = derive_answer_contract(question=QUESTION, now=_CLOCK_NOW, reader_answers=ANSWERS)
    reviewed = plan_review_messages(contract, [], reader_answers=ANSWERS)[1].content

    assert reviewed.index("# Answer contract\n") < reviewed.index("# Reader answers\n") < reviewed.index("# Plan under review\n")
    assert f"# Reader answers\n{render_reader_answers(ANSWERS)}\n\n# Plan under review" in reviewed
    assert plan_review_messages(contract, [], reader_answers=()) == plan_review_messages(contract, [])
    assert "# Reader answers" not in plan_review_messages(contract, [])[1].content


@pytest.mark.asyncio
async def test_the_planner_reads_the_answers_from_its_state_into_both_requests_and_the_contract(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter(
        decisions=[finish("No lookup needed.", "Three angles matter.")],
        outputs=[_sorting_plan(), _review()],
    )
    agent = _planner(tracker, completer)
    state = ResearchState(session_id="session-1", original_question=QUESTION, reader_answers=list(ANSWERS))

    async with tracker.session_span("session-1", "q"):
        outcome = await agent.run(state)

    plan_request = completer.calls[0][2][1].content
    review_request = completer.calls[1][2][1].content
    assert [call[0] for call in completer.calls] == ["ResearchPlanDraft", "PlanReviewDraft"]
    assert f"# Reader answers\n{render_reader_answers(ANSWERS)}" in plan_request
    assert f"# Reader answers\n{render_reader_answers(ANSWERS)}" in review_request
    assert "- Scope: European Union" in plan_request
    contract = outcome.state_update["answer_contract"]
    assert contract.geographic_scope == "European Union"
    assert "Reader said: Region = European Union" in contract.assumptions


# The two answer-contract consumers' packets for the default case, session "s1",
# with no answers, as sha256[:16] of the exact text: identical before this work
# (8994d5a, a00ef0a) and after it, because the check adds sections only when
# there are answers (spec §4.4 "Replay").
PINNED_PACKETS = {
    # notes-progress-report Phase C re-pinned these values: its writer requests and the report
    # the review reads changed (spec §7.1, §7.4, §7.5).
    "planner:react": "a420821fa50ed937",
    "planner:ResearchPlanDraft": "1f7f8426a5be29de",
    "planner:PlanReviewDraft": "918178396a7380e5",
    "report_reviewer:ReportReviewDraft": "ba466f328de6eb67",
}


async def _replay_packets(tmp_path: Path, reader_answers: tuple[ReaderAnswer, ...]) -> tuple[str, dict[str, str]]:
    scenario = scenario_by_id(EXTRA_PASS_CASE)
    held: dict[str, Any] = {}

    async def builder(current: ConfigSettings, *, session_id: str) -> Any:
        effective = replay_settings(scenario, root=tmp_path, base=current)
        replay = await build_replay_runtime(scenario, root=tmp_path, session_id=session_id, settings=effective)
        held["completer"] = replay.completer
        return replay.runtime

    outcome = await run_research(
        question=scenario.question, session_id="s1", config_path=str(production_config_path()),
        max_extra_passes=scenario.max_extra_passes, runtime_builder=builder, reader_answers=reader_answers,
    )
    return outcome.status, dict(held["completer"].packets)


@pytest.mark.asyncio
async def test_without_answers_the_replay_packets_are_byte_identical(tmp_path: Path) -> None:
    with guarded():
        status, packets = await _replay_packets(tmp_path, ())

    assert status == "completed"
    assert {key: hashlib.sha256(packets[key].encode("utf-8")).hexdigest()[:16] for key in PINNED_PACKETS} == PINNED_PACKETS
    # The section, not the bottom line's rule that names it (notes-progress-report spec §7.1).
    assert not any("\n# Reader answers\n" in text for text in packets.values())


@pytest.mark.asyncio
async def test_the_readers_answers_reach_the_planners_packets(tmp_path: Path) -> None:
    """AC11: the planner's packet contains ``# Reader answers``, and the run completes."""
    with guarded():
        status, packets = await _replay_packets(tmp_path, (REGION.model_copy(update={"value": "United States"}),))

    assert status == "completed"
    for key in ("planner:ResearchPlanDraft", "planner:PlanReviewDraft"):
        assert "# Reader answers\n" in packets[key]
        assert "- Which region should this cover? United States (the reader's answer)" in packets[key]
    assert "- Scope: United States" in packets["planner:ResearchPlanDraft"]
    assert "# Reader answers" not in packets["planner:react"]
