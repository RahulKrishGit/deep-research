"""Controlled campaign runner and command-contract tests."""

from __future__ import annotations

import asyncio
import dataclasses
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import deep_research.agents.planner as planner_agents
import deep_research.agents.researcher as researcher_agents
import deep_research.e2e_evaluation.runner as campaign_runner
from deep_research.e2e_evaluation.cases import (
    CONTROLLED_CASE_IDS,
    LIVE_CASE_IDS,
    ControlledDependencyError,
    ScriptedGraphPublisher,
    case_by_id,
    dependencies_for,
    publication_operation_for,
    scripted_research_agents,
)
from deep_research.e2e_evaluation.evaluators import deterministic_evaluation
from deep_research.e2e_evaluation.models import (
    CaseCampaignResult,
    ReplayCaseResult,
    ReplayRepetitionResult,
    ReplaySuiteResult,
)
from deep_research.e2e_evaluation.replay import (
    REPLAY_CLOCK_INSTANT,
    network_denied,
    run_replay_scenario,
)
from deep_research.e2e_evaluation.replay_matrix import (
    GRAPH_ONLY_HISTORICAL_MANIFEST,
    REPLAY_CASE_IDS,
    REPLAY_CASE_MANIFEST,
    ReplayCaseEntry,
    manifest_entry,
    scenario_by_id,
)
from deep_research.e2e_evaluation.runner import (
    LIVE_TIER_NOT_RUN,
    build_judge_metadata,
    build_parser,
    graph_historical_suite_lines,
    network_line,
    real_agent_suite_lines,
    run_case,
    run_replay_suite,
    run_suite,
)
from deep_research.graph.orchestrator import compile_research_graph, run_research_graph
from deep_research.observability import LangSmithRuntimeConfig, Tracker
from deep_research.runtime.outcome import build_outcome


def test_a_published_artifact_is_classified_by_name_not_by_default() -> None:
    """An unclassifiable publication must fail, not be recorded as the reader.

    The scripted publisher used to record every name that was not the ledger as
    ``reader_document``. When Task 11 added the quality record as a third
    artifact, that default silently recorded it as a second reader write, so the
    publication-order gate compared a sequence that no longer described the run
    and failed a correct run. This is the regression guard for that class of
    bug: the third artifact is named for what it is, and a name this double
    cannot classify raises instead of being absorbed by the reader fallback.
    """
    stem = "report-controlled-broad-constraints-r1-1"

    assert publication_operation_for(f"{stem}.md") == "reader_document"
    assert publication_operation_for(f"{stem}-evidence.md") == "evidence_document"
    assert publication_operation_for(f"{stem}-quality.json") == "quality_document"

    # Not an artifact this double knows: a fallback here is what hid the bug.
    with pytest.raises(ControlledDependencyError):
        publication_operation_for(f"{stem}-appendix.json")
    with pytest.raises(ControlledDependencyError):
        publication_operation_for(f"{stem}-quality.jsonl")


def test_controlled_case_runs_exactly_three_repetitions_and_writes_artifact(
    tmp_path,
) -> None:
    result = run_case(
        CONTROLLED_CASE_IDS[0],
        tier="controlled",
        repetitions=3,
        output_directory=tmp_path,
    )

    assert result.accepted
    assert len(result.repetitions) == 3
    assert {item.repetition for item in result.repetitions} == {1, 2, 3}
    assert result.repetitions[0].report
    assert result.repetitions[0].evidence_ledger
    assert tmp_path.joinpath("broad-constraints", "case.json").is_file()
    # The published names are the finalizer's own, not the double's ordinal
    # placeholders: the campaign writes each artifact under the filename the
    # terminal publisher asked for.
    repetition_root = tmp_path.joinpath("broad-constraints", "repetition-1")
    assert repetition_root.joinpath(
        "report-controlled-broad-constraints-r1-1.md"
    ).is_file()
    assert repetition_root.joinpath(
        "report-controlled-broad-constraints-r1-1-evidence.md"
    ).is_file()
    assert repetition_root.joinpath(
        "report-controlled-broad-constraints-r1-1-quality.json"
    ).is_file()
    assert result.repetitions[0].metadata.request_counts["query_memory"] == 1
    # Three artifacts, one write each: the reader Markdown, the evidence ledger,
    # and the quality record Task 11 added. The count is asserted together with
    # the three names above so a run that published a different set — or one
    # artifact twice under two names — cannot satisfy it.
    assert result.repetitions[0].metadata.request_counts["write_document"] == 3
    restored = json.loads(
        tmp_path.joinpath("broad-constraints", "case.json").read_text()
    )
    assert CaseCampaignResult.model_validate(restored) is not None


def test_controlled_campaign_exercises_production_graph_boundaries(tmp_path) -> None:
    """The campaign must invoke, rather than merely import, the real graph."""
    assert hasattr(campaign_runner, "compile_research_graph")
    assert hasattr(campaign_runner, "run_research_graph")

    compile_calls: list[str] = []
    run_calls: list[str] = []
    original_compile = campaign_runner.compile_research_graph
    original_run = campaign_runner.run_research_graph

    def record_compile(*args, **kwargs):
        compile_calls.append("compile")
        return original_compile(*args, **kwargs)

    async def record_run(*args, **kwargs):
        run_calls.append("run")
        return await original_run(*args, **kwargs)

    # The attributes are intentionally patched at the campaign boundary so a
    # direct dependency fixture can never satisfy this sensitivity test.
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(campaign_runner, "compile_research_graph", record_compile)
    monkeypatch.setattr(campaign_runner, "run_research_graph", record_run)
    try:
        result = campaign_runner.run_case(
            CONTROLLED_CASE_IDS[0],
            tier="controlled",
            repetitions=3,
            output_directory=tmp_path,
        )
    finally:
        monkeypatch.undo()

    assert result.accepted
    assert compile_calls == ["compile", "compile", "compile"]
    assert run_calls == ["run", "run", "run"]


def test_controlled_repetition_is_derived_from_graph_events_and_snapshots(
    tmp_path,
) -> None:
    result = campaign_runner.run_case(
        "refinement-evidence-recovery",
        tier="controlled",
        repetitions=3,
        output_directory=tmp_path,
    )

    state = result.repetitions[0].state
    completed_nodes = {
        event.metadata.get("node")
        for event in state.events
        if event.event_type == "graph.node.completed"
    }
    assert completed_nodes >= {
        "planner",
        "researcher",
        "source_evaluator",
        "fact_checker",
        "synthesizer",
        "critic",
        "finalize_report",
    }
    assert any(
        event.event_type == "graph.refinement.started" for event in state.events
    )
    assert any(
        event.event_type == "graph.route.decided"
        and event.metadata.get("reason") == "quality_gate_failed"
        for event in state.events
    )
    assert any(
        event.event_type.endswith(".snapshot.completed")
        and event.metadata.get("snapshot_kind") == "complete"
        for event in state.events
    )
    source_snapshots = [
        event
        for event in state.events
        if event.event_type == "agent.source_evaluator.snapshot.completed"
    ]
    claim_snapshots = [
        event
        for event in state.events
        if event.event_type == "agent.fact_checker.snapshot.completed"
    ]
    assert [event.metadata["count"] for event in source_snapshots] == [6, 10]
    assert [event.metadata["count"] for event in claim_snapshots] == [3, 5]
    assert len(state.raw_findings) == 5
    assert len({finding.content for finding in state.raw_findings}) == 5
    assert len(state.evaluated_sources) == 10
    assert len(state.verified_claims) == 5
    assert result.repetitions[0].deterministic.new_evidence_in_refinement == 2


def test_snapshot_replacement_is_proven_by_agent_histories_and_final_state(
    tmp_path,
) -> None:
    """Check canonical replacement through the actual graph, not checkpoints."""
    case = case_by_id("refinement-evidence-recovery")
    dependencies = dependencies_for(case)
    publisher = ScriptedGraphPublisher(dependencies, tmp_path)
    agents = scripted_research_agents(case, dependencies, publisher)
    graph = compile_research_graph(agents)
    run = asyncio.run(
        run_research_graph(
            graph=graph,
            tracker=Tracker(
                LangSmithRuntimeConfig(
                    tracing_enabled=False,
                    project="controlled-e2e-test",
                    api_key=None,
                )
            ),
            session_id="controlled-snapshot-history",
            question=case.question,
            max_iterations=2,
        )
    )

    source_agent = agents.source_evaluator
    claim_agent = agents.fact_checker
    assert [len(snapshot.sources) for snapshot in source_agent.output_snapshots] == [
        6,
        10,
    ]
    assert [len(snapshot.claims) for snapshot in claim_agent.output_snapshots] == [
        3,
        5,
    ]
    assert len(source_agent.input_states) == len(source_agent.output_snapshots) == 2
    assert len(claim_agent.input_states) == len(claim_agent.output_snapshots) == 2
    assert run.state.evaluated_sources == source_agent.output_snapshots[-1].sources
    assert run.state.verified_claims == claim_agent.output_snapshots[-1].claims
    assert any(
        event.event_type == "graph.route.decided"
        and event.metadata.get("reason") == "quality_gate_failed"
        for event in run.state.events
    )


def test_a_campaign_that_cannot_see_graph_events_is_never_accepted(tmp_path) -> None:
    """The campaign's green verdict must mean the graph ran, not the fixture.

    When ``_has_production_graph_events`` is False the evaluator substitutes
    fixture values (``case.passes``, ``critic_targets``,
    ``new_evidence_topics``, ``force_refinement``) that are true by
    construction, and the repetition still reported ``accepted``. The
    campaign's acceptance verdict is what the plan's checklist consumes, so an
    operator running the suite CLI without the test suite could be handed a
    fixture-based "accepted".
    """
    original_run = campaign_runner.run_research_graph

    async def blind_run(*args, **kwargs):
        run = await original_run(*args, **kwargs)
        return dataclasses.replace(
            run,
            state=run.state.model_copy(
                update={
                    "events": [
                        event
                        for event in run.state.events
                        if event.event_type != "graph.node.completed"
                    ]
                }
            ),
        )

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(campaign_runner, "run_research_graph", blind_run)
    try:
        result = campaign_runner.run_case(
            CONTROLLED_CASE_IDS[0],
            tier="controlled",
            repetitions=3,
            output_directory=tmp_path,
        )
    finally:
        monkeypatch.undo()

    assert not any(item.accepted for item in result.repetitions)
    assert not result.accepted
    assert "production_graph_unobserved" in result.hard_failures
    assert all(
        item.deterministic.graph_observed is False
        for item in result.repetitions
    )
    assert all(
        "production_graph_unobserved" in item.deterministic.integrity_failures
        for item in result.repetitions
    )


def test_the_publisher_writes_the_filename_the_finalizer_asked_for(
    tmp_path,
) -> None:
    """The campaign must be able to see which artifact was written where.

    ``ScriptedGraphPublisher.publish_document`` named its target by call
    ordinal — first call ``report.md``, second ``evidence-ledger.md`` — and
    never read its ``filename`` argument, so a finalizer regression publishing
    both artifacts under one name, or swapping them, still passed. Binding the
    target and the recorded operation to the real filename, and re-reading the
    published bytes off disk, is what makes the campaign's publication evidence
    about the finalizer rather than about the double.
    """
    result = campaign_runner.run_case(
        CONTROLLED_CASE_IDS[0],
        tier="controlled",
        repetitions=3,
        output_directory=tmp_path,
    )
    repetition = result.repetitions[0]
    reader = Path(repetition.state.report_path or "")
    ledger = Path(repetition.state.evidence_path or "")

    assert reader.name == "report-controlled-broad-constraints-r1-1.md"
    assert ledger.name == "report-controlled-broad-constraints-r1-1-evidence.md"
    assert reader.is_file() and ledger.is_file()
    assert reader.read_text(encoding="utf-8") == repetition.state.report
    assert ledger.read_text(encoding="utf-8") == repetition.state.report_evidence
    # The name decides the recorded operation, so a swap is recorded as one.
    assert repetition.publication_operations[:2] == [
        "reader_document",
        "evidence_document",
    ]


def test_critic_target_failures_are_reachable_from_the_graph_path(tmp_path) -> None:
    """Both Critic-target failures must be driven by real graph events.

    ``critic_targets_unresolved`` and ``critic_refinement_no_new_evidence``
    were reachable only through ``_terminal_state``, i.e. a hand-written
    state. Here the Critic's own ``agent.critic.targets.recorded`` event and
    the Fact Checker's complete-snapshot events carry the comparison: the plan
    has five topics, only three carry claims in the first pass, and the
    refinement pass republishes the same snapshot, so the two targets the
    Critic named are never closed and no new coverage arrives.
    """
    case = case_by_id("refinement-evidence-recovery")
    stale_pass = case.passes[0].model_copy(
        update={"iteration": 1, "force_refinement": True}
    )
    case = case.model_copy(
        update={"passes": [case.passes[0], stale_pass]}
    )
    dependencies = dependencies_for(case)
    publisher = ScriptedGraphPublisher(dependencies, tmp_path)
    agents = scripted_research_agents(case, dependencies, publisher)
    graph = compile_research_graph(agents)
    tracker = Tracker(
        LangSmithRuntimeConfig(
            tracing_enabled=False,
            project="controlled-critic-failure-test",
            api_key=None,
        )
    )
    run = asyncio.run(
        run_research_graph(
            graph=graph,
            tracker=tracker,
            session_id="controlled-critic-failures",
            question=case.question,
            max_iterations=2,
        )
    )
    cli_output = campaign_runner.render_summary(
        build_outcome(run, metrics=tracker.metrics), verbose=False
    )
    metrics = deterministic_evaluation(
        case, run.state, dependencies=dependencies, cli_output=cli_output
    )

    assert metrics.graph_observed is True
    assert metrics.critic_targets == 2
    assert metrics.closed_critic_targets == 0
    assert metrics.new_evidence_in_refinement == 0
    assert "critic_targets_unresolved" in metrics.integrity_failures
    assert "critic_refinement_no_new_evidence" in metrics.integrity_failures


def test_the_targetless_gate_forced_refinement_is_a_visible_observation(
    tmp_path,
) -> None:
    """The campaign's null result must be recorded, not silently accepted.

    The broad case deliberately empties its reader summary at iteration 0, so
    the quality gate forces a refinement pass the Critic names no target for.
    That pass spends budget and adds nothing, and no gate fails on it — the
    gate is the graph's own verdict and this case exists to exercise it. The
    earlier ruling deferred the underlying routing question on the grounds
    that this campaign is where it would surface; it reaches the artifact as a
    recorded count instead of being silently accepted.
    """
    result = campaign_runner.run_case(
        CONTROLLED_CASE_IDS[0],
        tier="controlled",
        repetitions=3,
        output_directory=tmp_path,
    )
    artifact = json.loads(
        tmp_path.joinpath("broad-constraints", "case.json").read_text()
    )
    recorded = artifact["repetitions"][0]["deterministic"]

    assert recorded.get("targetless_gate_forced_refinements") == 1
    assert recorded["gate_forced_refinement_passes"] == 1
    assert recorded["critic_targets"] == 0
    assert recorded["new_evidence_in_refinement"] == 0
    assert all(
        item.deterministic.targetless_gate_forced_refinements == 1
        for item in result.repetitions
    )


def test_agent_inputs_prove_ordered_upstream_handoffs(tmp_path) -> None:
    """Every real graph node consumes the preceding node's typed output."""
    case = case_by_id("refinement-evidence-recovery")
    dependencies = dependencies_for(case)
    publisher = ScriptedGraphPublisher(dependencies, tmp_path)
    agents = scripted_research_agents(case, dependencies, publisher)
    graph = compile_research_graph(agents)
    asyncio.run(
        run_research_graph(
            graph=graph,
            tracker=Tracker(
                LangSmithRuntimeConfig(
                    tracing_enabled=False,
                    project="controlled-handoff-test",
                    api_key=None,
                )
            ),
            session_id="controlled-handoff-order",
            question=case.question,
            max_iterations=2,
        )
    )

    assert dependencies.agent_call_order[:6] == [
        "planner",
        "researcher",
        "source_evaluator",
        "fact_checker",
        "synthesizer",
        "critic",
    ]
    assert dependencies.agent_call_order[6:11] == [
        "researcher",
        "source_evaluator",
        "fact_checker",
        "synthesizer",
        "critic",
    ]
    assert agents.researcher.input_states[0].sub_topics == case.sub_topics
    assert (
        agents.researcher.output_updates[0]["raw_findings"]
        == agents.source_evaluator.input_states[0].raw_findings
    )
    assert (
        agents.source_evaluator.output_updates[0]["evaluated_sources"]
        == agents.fact_checker.input_states[0].evaluated_sources
    )
    assert (
        agents.fact_checker.output_updates[0]["verified_claims"]
        == agents.synthesizer.input_states[0].verified_claims
    )
    assert (
        agents.synthesizer.output_updates[0]["composition"]
        == agents.critic.input_states[0].composition
    )
    assert agents.critic.input_states[0].quality is not None


def test_critic_targets_are_derived_from_uncovered_state_not_fixture_targets(
    tmp_path,
) -> None:
    case = case_by_id("refinement-evidence-recovery")
    case = case.model_copy(
        update={
            "passes": [
                case.passes[0].model_copy(update={"critic_targets": []}),
                case.passes[1],
            ]
        }
    )
    dependencies = dependencies_for(case)
    publisher = ScriptedGraphPublisher(dependencies, tmp_path)
    agents = scripted_research_agents(case, dependencies, publisher)
    graph = compile_research_graph(agents)
    asyncio.run(
        run_research_graph(
            graph=graph,
            tracker=Tracker(
                LangSmithRuntimeConfig(
                    tracing_enabled=False,
                    project="controlled-critic-target-test",
                    api_key=None,
                )
            ),
            session_id="controlled-critic-targets",
            question=case.question,
            max_iterations=2,
        )
    )

    critique = agents.critic.output_updates[0]["critique"]
    assert {gap.coverage_id for gap in critique.gaps} == {"topic-04", "topic-05"}


def test_campaign_uses_production_cli_formatter(tmp_path) -> None:
    assert hasattr(campaign_runner, "render_summary")
    result = campaign_runner.run_case(
        "comparative-conflict",
        tier="controlled",
        repetitions=3,
        output_directory=tmp_path,
    )
    repetition = result.repetitions[0]
    assert repetition.deterministic.cli_summary_matches
    assert repetition.cli_output
    assert any(line.startswith("Quality:") for line in repetition.cli_output)


def test_cli_agreement_gate_uses_formatter_output(tmp_path) -> None:
    original = campaign_runner.render_summary

    def tampered_summary(outcome, *, verbose):
        lines = original(outcome, verbose=verbose)
        return [
            line.replace(
                "Integrity: 0 duplicate claims", "Integrity: 1 duplicate claims"
            )
            for line in lines
        ]

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(campaign_runner, "render_summary", tampered_summary)
    try:
        result = campaign_runner.run_case(
            "comparative-conflict",
            tier="controlled",
            repetitions=3,
            output_directory=tmp_path,
        )
    finally:
        monkeypatch.undo()

    assert not result.accepted
    assert all(
        not item.deterministic.cli_summary_matches for item in result.repetitions
    )
    assert all(
        "cli_summary_mismatch" in item.deterministic.integrity_failures
        for item in result.repetitions
    )


def test_repetition_persists_exact_bounded_judge_input(tmp_path) -> None:
    result = campaign_runner.run_case(
        CONTROLLED_CASE_IDS[0],
        tier="controlled",
        repetitions=3,
        output_directory=tmp_path,
    )

    repetition = result.repetitions[0]
    assert repetition.judge_input.reader_report == repetition.report
    artifact = json.loads(
        tmp_path.joinpath("broad-constraints", "case.json").read_text()
    )
    assert set(artifact["repetitions"][0]["judge_input"]) == {
        "question",
        "scoped_plan",
        "reader_report",
        "deterministic_metrics",
        "evidence_ledger_summary",
    }


@pytest.mark.parametrize(
    ("scores", "accepted"),
    [
        ([0.69, 0.69, 0.69], False),
        ([0.70, 0.70, 0.70], False),
        ([0.70, 0.80, 0.90], True),
    ],
)
def test_controlled_acceptance_boundaries_are_seven_score_aware(
    tmp_path, scores, accepted
) -> None:
    values = iter(scores)

    def bounded_judge(_payload):
        from deep_research.e2e_evaluation.models import WholeReportJudgeScore

        return WholeReportJudgeScore(score=next(values))

    result = campaign_runner.run_case(
        "comparative-conflict",
        tier="controlled",
        repetitions=3,
        output_directory=tmp_path,
        judge=bounded_judge,
    )

    assert result.accepted is accepted
    assert result.mean_judge_score == pytest.approx(sum(scores) / 3)
    assert all(
        repetition.accepted is (score >= 0.70)
        for repetition, score in zip(result.repetitions, scores, strict=True)
    )


def test_the_graph_historical_suite_verdict_reads_each_rows_declaration(
    tmp_path,
) -> None:
    """A row that declares a partial result passes without being accepted.

    The suite's verdict used to be ``all(case.accepted)``, which demanded that
    every row be an accepted row — so a case whose declared result *is* a
    partial one could not be declared at all. The verdict now reads each row's
    own declaration: the five rows still pass, one of them by producing the
    partial result it declares rather than by being accepted, and the run's own
    output says which. Both facts stay visible, because "this suite passed" and
    "every row was accepted" are different claims.
    """
    result = run_suite(tier="controlled", repetitions=3, output_directory=tmp_path)

    assert result.accepted
    assert result.rows_accepted is False
    assert [case.case_id for case in result.cases] == list(CONTROLLED_CASE_IDS)
    assert all(len(case.repetitions) == 3 for case in result.cases)
    assert result.metadata["network"] == "zero"
    assert tmp_path.joinpath("suite.json").is_file()
    assert result.artifact_path == str(tmp_path / "suite.json")
    assert result.langsmith_metadata["quality_gate_version"] == 1

    by_id = {case.case_id: case for case in result.cases}
    open_obligation = by_id["claimed-coverage-open-obligation"]
    assert open_obligation.expected_result.accepted is False
    assert open_obligation.accepted is False
    assert open_obligation.met_expectation is True
    assert by_id["declared-obligations-answered"].accepted is True
    assert by_id["declared-obligations-answered"].met_expectation is True
    # The three legacy rows are untouched: accepted, and met by being accepted.
    for case_id in CONTROLLED_CASE_IDS[:3]:
        assert by_id[case_id].accepted is True, case_id
        assert by_id[case_id].expected_result.accepted is True, case_id
        assert by_id[case_id].met_expectation is True, case_id

    lines = graph_historical_suite_lines(result)
    assert any(
        line.startswith(f"{open_obligation.case_id}: declared partial")
        for line in lines
    ), lines
    assert lines[-3] == (
        f"Suite: accepted (3 repetitions per case; "
        f"{len(CONTROLLED_CASE_IDS) - 1}/{len(CONTROLLED_CASE_IDS)} "
        "rows accepted)"
    )


def test_a_row_that_does_not_produce_its_declared_result_fails_the_suite(
    tmp_path, monkeypatch
) -> None:
    """The verdict is a comparison, not a formality.

    A suite whose aggregation only ever agrees with the rows would report
    ``accepted`` for a run that produced something other than what its case
    declares. The three legacy rows declare an accepted result, so a repetition
    that *records* a leg its case does not declare must fail the row and the
    suite — which is the same comparison, run the other way, that lets the
    declared-partial row pass.
    """
    real_evaluation = campaign_runner.deterministic_evaluation
    undeclared_leg = "coverage_below_0.80"

    def add_an_undeclared_leg(case, state, **kwargs):
        metrics = real_evaluation(case, state, **kwargs)
        if case.case_id != CONTROLLED_CASE_IDS[0]:
            return metrics
        return metrics.model_copy(
            update={
                "integrity_failures": [
                    *metrics.integrity_failures,
                    undeclared_leg,
                ]
            }
        )

    monkeypatch.setattr(
        campaign_runner, "deterministic_evaluation", add_an_undeclared_leg
    )

    result = run_suite(tier="controlled", repetitions=3, output_directory=tmp_path)

    row = result.cases[0]
    assert undeclared_leg in row.hard_failures
    assert row.expected_result.required_failures == []
    assert row.met_expectation is False
    assert row.accepted is False
    assert result.accepted is False
    assert result.rows_accepted is False


def test_controlled_repetitions_are_bounded_to_exactly_three(tmp_path) -> None:
    with pytest.raises(ValueError, match="exactly 3"):
        run_suite(tier="controlled", repetitions=2, output_directory=tmp_path)


def test_the_replay_suite_runs_the_whole_manifest_not_the_three_legacy_rows(
    tmp_path,
) -> None:
    """The controlled suite's inventory is the manifest, not a hardcoded three.

    The suite used to be closed over exactly the three scripted-dependency
    cases, which left fifteen declared real-agent rows that no command ever
    ran. The inventory is the versioned manifest, so a row that is added,
    removed or re-versioned changes what the suite runs without editing the
    runner -- and the fifteen rows the suite never reached are now the
    evidence a release is read from.
    """
    result = run_replay_suite(
        tier="controlled", repetitions=3, output_directory=tmp_path
    )

    assert result.mode == "real-agent"
    assert [case.case_id for case in result.cases] == list(REPLAY_CASE_IDS)
    assert len(result.cases) == len(REPLAY_CASE_MANIFEST)
    assert result.repetitions == 3
    assert result.metadata["network"] == "zero"
    assert result.metadata["network_attempts"] == 0
    assert result.accepted
    assert all(case.passed for case in result.cases)
    assert all(case.deterministic for case in result.cases)
    for case in result.cases:
        assert len(case.repetitions) == 3
        assert len({item.session_id for item in case.repetitions}) == 3
        assert all(item.network_attempts == [] for item in case.repetitions)
    # The two harnesses write different artifacts, so neither can overwrite
    # the other's evidence in a shared output directory.
    assert tmp_path.joinpath("replay-suite.json").is_file()
    assert result.artifact_path == str(tmp_path / "replay-suite.json")
    assert not tmp_path.joinpath("suite.json").exists()
    restored = ReplaySuiteResult.model_validate(
        json.loads(tmp_path.joinpath("replay-suite.json").read_text())
    )
    assert [case.case_id for case in restored.cases] == list(REPLAY_CASE_IDS)
    assert restored.cases[0].repetitions[0].report_fingerprint


def _stub_replay_suite(
    *, cases: int, attempts: tuple[str, ...] = (), passed: bool = True
) -> ReplaySuiteResult:
    """A suite result to render, so a disclosure test need not run one."""
    rows = [
        ReplayCaseResult(
            case_id=f"row-{index}",
            version=1,
            expected_product_result="accepted / 0",
            decisive_assertion="stub",
            repetitions=[
                ReplayRepetitionResult(
                    case_id=f"row-{index}",
                    repetition=number,
                    session_id=f"replay-row-{index}-r{number}",
                    terminal_quality="accepted",
                    exit_code=0,
                    expectation_failures=[] if passed else ["stub failure"],
                    answered_target_ids=["topic-01-target-01"],
                    network_attempts=(
                        list(attempts) if (index, number) == (1, 1) else []
                    ),
                    report_fingerprint="0" * 64,
                )
                for number in range(1, 4)
            ],
            deterministic=True,
            passed=passed,
        )
        for index in range(1, cases + 1)
    ]
    return ReplaySuiteResult(
        campaign_id="controlled-replay-stub",
        tier="controlled",
        mode="real-agent",
        manifest_version=1,
        case_version=1,
        repetitions=3,
        cases=rows,
        accepted=passed and not attempts,
        artifact_path="output/evaluations/e2e/replay-suite.json",
    )


def test_the_real_agent_output_names_the_harness_before_the_results() -> None:
    """Which agents ran is the first thing a reader has to be told.

    Both whole-branch reviews on this task flagged prose-only disclosure: a
    ledger saying "real agents" is not disclosure if the command's own output
    does not say it. The line names the harness, the inventory it was read
    from, and where the evidence landed, in the output itself.
    """
    suite = _stub_replay_suite(cases=18)

    lines = real_agent_suite_lines(suite)

    assert lines[:2] == [
        "Mode: real-agent (18 cases from replay manifest v1, case semantics v1)",
        "Agents: production classes through the real graph",
    ]
    assert [
        f"row-{index}: passed (3 repetitions, deterministic)"
        for index in range(1, 19)
    ] == lines[2:-3]
    assert lines[-3:] == [
        "Suite: accepted (3 repetitions per case, 18/18 rows)",
        "Artifact: output/evaluations/e2e/replay-suite.json",
        "Network: zero (socket layer denied; 0 attempts recorded)",
    ]


def test_the_network_line_reports_attempts_rather_than_claiming_zero() -> None:
    """A suite that reached the network cannot print the zero line."""
    attempted = _stub_replay_suite(
        cases=1,
        attempts=("('agency.test', 443)", "('agency.test', 80)"),
    )

    assert network_line(attempted) == (
        "Network: NOT zero (socket layer denied; 2 attempts recorded)"
    )
    assert attempted.accepted is False


def test_the_suite_command_prints_the_real_agent_disclosure(
    monkeypatch, capsys
) -> None:
    """No --mode means the real agents, and the output says so."""
    stub = _stub_replay_suite(cases=2)
    monkeypatch.setattr(campaign_runner, "run_replay_suite", lambda **_: stub)

    code = campaign_runner.main(
        ["suite", "--tier", "controlled", "--repetitions", "3"]
    )
    printed = capsys.readouterr().out.splitlines()

    assert code == 0
    assert printed == real_agent_suite_lines(stub)
    assert printed[0].startswith("Mode: real-agent")
    assert "SCRIPTED" not in "\n".join(printed)


def test_the_graph_historical_mode_says_it_runs_scripted_doubles(
    monkeypatch, tmp_path, capsys
) -> None:
    """The historical harness runs, and its output refuses to be read as proof.

    The three legacy rows are still a real regression — they are the product
    result recorded when the only agents were scripted doubles — so the mode
    stays runnable. What it may never do is print a result that a reader could
    take for real-agent evidence, so the label names the doubles and says what
    the mode is not.
    """
    monkeypatch.setattr(campaign_runner, "DEFAULT_OUTPUT_DIRECTORY", tmp_path)

    code = campaign_runner.main(
        [
            "suite",
            "--tier",
            "controlled",
            "--mode",
            "graph-historical",
            "--repetitions",
            "3",
        ]
    )
    printed = capsys.readouterr().out.splitlines()

    assert code == 0
    assert printed[:3] == [
        f"Mode: graph-historical "
        f"({len(GRAPH_ONLY_HISTORICAL_MANIFEST)} scripted-double cases)",
        "Agents: SCRIPTED DOUBLES, not production classes — historical "
        "regression only.",
        "        This mode is not release evidence for the real agents.",
    ]
    assert printed[-2:] == [
        f"Artifact: {tmp_path / 'suite.json'}",
        "Network: zero (scripted dependencies only)",
    ]
    assert tmp_path.joinpath("suite.json").is_file()
    assert not tmp_path.joinpath("replay-suite.json").exists()


def test_the_list_command_shows_both_inventories_and_both_labels(capsys) -> None:
    code = campaign_runner.main(["list"])
    printed = capsys.readouterr().out.splitlines()

    assert code == 0
    text = "\n".join(printed)
    assert "Mode: real-agent" in text
    assert "Agents: production classes through the real graph" in text
    assert "Mode: graph-historical" in text
    assert "SCRIPTED DOUBLES, not production classes" in text
    for case_id in REPLAY_CASE_IDS:
        assert f"  {case_id}: " in text
    for entry in GRAPH_ONLY_HISTORICAL_MANIFEST:
        assert f"  {entry.case_id}: " in text
    for case_id in LIVE_CASE_IDS:
        assert f"  {case_id}" in text


def test_the_replay_suite_is_bounded_to_exactly_three_repetitions(tmp_path) -> None:
    with pytest.raises(ValueError, match="exactly 3"):
        run_replay_suite(tier="controlled", repetitions=2, output_directory=tmp_path)
    with pytest.raises(RuntimeError, match=LIVE_TIER_NOT_RUN):
        run_replay_suite(tier="live", repetitions=3, output_directory=tmp_path)
    assert not list(tmp_path.rglob("*.json"))


def test_the_suite_command_defaults_to_the_real_agent_matrix() -> None:
    """No --mode means the real agents, because that is what a release is."""
    options = build_parser().parse_args(
        ["suite", "--tier", "controlled", "--repetitions", "3"]
    )

    assert options.mode == "real-agent"
    historical = build_parser().parse_args(
        ["suite", "--tier", "controlled", "--mode", "graph-historical"]
    )
    assert historical.mode == "graph-historical"


def test_live_tier_is_authorization_ready_but_never_runs(tmp_path) -> None:
    with pytest.raises(RuntimeError, match=LIVE_TIER_NOT_RUN):
        run_suite(tier="live", repetitions=1, output_directory=tmp_path)
    assert not list(tmp_path.rglob("*.json"))


def test_langsmith_metadata_has_graph_prompts_schemas_models_and_requests(
    tmp_path,
) -> None:
    result = run_case(
        CONTROLLED_CASE_IDS[0],
        tier="controlled",
        repetitions=3,
        output_directory=tmp_path,
    )
    metadata = result.repetitions[0].langsmith_metadata

    assert metadata["graph_revision"]
    assert set(metadata["target_prompt_fingerprints"]) == {
        "planner",
        "researcher",
        "source_evaluator",
        "fact_checker",
        "synthesizer",
        "critic",
    }
    for key in (
        "report_schema_version",
        "quality_gate_version",
        "case_schema_version",
        "target_model",
        "judge_model",
        "request_counts",
    ):
        assert key in metadata
    assert metadata["request_counts"]["judge"] == 1


def test_a_repetition_records_the_semantic_review_its_run_made(tmp_path) -> None:
    """The harness's semantic record is fed, not merely defined.

    ``CampaignRepetition.semantic_review`` is where the evaluation harness
    reads the terminal review, and ``semantic_review_summary`` is the evaluator
    that reads it. A repetition whose runner left that field unset would make
    the evaluator's input permanently absent: every campaign would report "no
    judgement" whatever the run actually judged, which is the failure mode a
    metric that skips its inputs has. The scripted campaign wires no reviewer,
    so the honest record here is the incomplete review the graph recorded — a
    review present, not scored, and never an acceptance.
    """
    result = run_case(
        "refinement-evidence-recovery",
        tier="controlled",
        output_directory=tmp_path,
    )

    review = result.repetitions[0].state.report_review
    recorded = result.repetitions[0].semantic_review
    assert review is not None
    assert recorded is not None
    assert recorded.status == review.status
    assert recorded.score == review.mean_score
    assert recorded.missing is True
    assert recorded.accepted is False


def test_judge_metadata_builder_is_content_free() -> None:
    metadata = build_judge_metadata(
        graph_revision="abc123",
        case_id="broad-constraints",
        repetition=1,
        request_counts={"web_search": 1, "judge": 1},
    )
    assert "secret" not in repr(metadata).casefold()
    assert "provider_output" not in repr(metadata).casefold()


def test_cli_exposes_only_list_case_and_suite(capsys) -> None:
    actions = [
        action
        for action in build_parser()._subparsers._group_actions
        if hasattr(action, "choices")
    ]
    assert set(actions[0].choices) == {"list", "case", "suite"}


def test_the_case_command_accepts_the_id_form_list_prints(
    monkeypatch, tmp_path, capsys
) -> None:
    """``case`` and ``list`` name one inventory, and the run says which harness.

    ``list`` prints the graph-historical rows as ``<id>-graph`` — the suffix is
    what keeps one inventory's evidence from being read as the other's — while
    ``case`` accepted only the legacy ids, so the form a reader was shown was
    rejected by argparse. Both forms run the same scripted doubles, and the
    legacy ids are three of the rows ``list`` prints under "Mode: real-agent",
    which is why the output has to disclose the harness before it states a
    result: a per-case line reading "accepted" is otherwise a claim about the
    production agents that a scripted run did not make.
    """
    monkeypatch.setattr(campaign_runner, "DEFAULT_OUTPUT_DIRECTORY", tmp_path)
    legacy = CONTROLLED_CASE_IDS[0]

    for case_id in (f"{legacy}-graph", legacy):
        code = campaign_runner.main(
            ["case", case_id, "--tier", "controlled", "--repetitions", "3"]
        )
        printed = capsys.readouterr().out.splitlines()

        assert code == 0, case_id
        assert printed[:3] == [
            f"Mode: graph-historical "
            f"({len(GRAPH_ONLY_HISTORICAL_MANIFEST)} scripted-double cases)",
            "Agents: SCRIPTED DOUBLES, not production classes — historical "
            "regression only.",
            "        This mode is not release evidence for the real agents.",
        ], case_id
        assert printed[3].startswith(f"Case {legacy}: "), case_id
        assert printed[-2].startswith("Artifact: "), case_id
        assert printed[-1] == "Network: zero (scripted dependencies only)", case_id
        assert tmp_path.joinpath(legacy, "case.json").is_file(), case_id


def test_the_case_command_rejects_an_unknown_id(capsys) -> None:
    """The accepted list is the two inventories' ids and nothing else."""
    with pytest.raises(SystemExit) as error:
        campaign_runner.main(
            ["case", "not-a-controlled-case", "--repetitions", "3"]
        )

    assert error.value.code == 2


@pytest.mark.parametrize("tier", ("controlled", "live"))
def test_the_case_command_stamps_no_harness_on_a_live_id(tier, capsys) -> None:
    """The live tier has no runner, so nothing may say one produced this.

    ``case`` accepts the live ids because the tier is declared, and the
    disclosure it printed for them described a harness that never ran: the
    command is refused before ``run_case``. The disclosure is a claim about
    which agents produced a result, so it belongs only to a run that happens —
    and the refusal names the live tier rather than reporting the id as an
    unknown controlled case.
    """
    code = campaign_runner.main(
        ["case", LIVE_CASE_IDS[0], "--tier", tier, "--repetitions", "3"]
    )
    printed = capsys.readouterr().out.splitlines()

    assert code == 2
    assert printed == [f"error: {LIVE_TIER_NOT_RUN}"]


def test_cli_accepts_a_live_case_id_without_executing_it() -> None:
    options = build_parser().parse_args(
        ["case", LIVE_CASE_IDS[0], "--tier", "live"]
    )

    assert options.case_id == LIVE_CASE_IDS[0]
    assert options.tier == "live"


def test_a_partial_product_status_is_never_printed_as_a_product_acceptance(
    monkeypatch, tmp_path, capsys
) -> None:
    """The row line prints the product's own status, not the campaign's.

    Every scripted-double repetition records ``graph_quality_status`` partial:
    no reviewer scored its report, so the product itself accepted nothing. The
    row can still clear the campaign's own floor gates, and a line that printed
    those floors as "product accepted" claimed an acceptance the product never
    made. The line now labels the campaign's verdict as the campaign's and
    prints the product's recorded status beside it, so the two facts can never
    be read as one.
    """
    monkeypatch.setattr(campaign_runner, "DEFAULT_OUTPUT_DIRECTORY", tmp_path)
    case_id = CONTROLLED_CASE_IDS[0]
    result = run_suite(tier="controlled", repetitions=3, output_directory=tmp_path)

    assert result.rows_accepted is False
    row_lines = [
        line
        for line in graph_historical_suite_lines(result)
        if line.startswith(f"{case_id}: ")
    ]
    assert row_lines
    for line in row_lines:
        assert "product accepted" not in line, line
        assert "product graph_quality_status partial" in line, line
        assert "campaign accepted" in line, line

    code = campaign_runner.main(
        ["case", case_id, "--tier", "controlled", "--repetitions", "3"]
    )
    printed = capsys.readouterr().out.splitlines()
    case_line = next(
        line for line in printed if line.startswith(f"Case {case_id}: ")
    )

    assert code == 0
    assert "product accepted" not in case_line, case_line
    assert "product graph_quality_status partial" in case_line, case_line
    assert "campaign accepted" in case_line, case_line


def test_a_row_whose_repetitions_disagree_is_not_passed(
    monkeypatch, tmp_path
) -> None:
    """Determinism is required of a row, not merely reported beside it.

    Task 12's three repetitions exist for deterministic order and identity and
    for state-isolation, so a row whose repetitions each meet the declared
    result but disagree on the published report is not a row that passed — and
    a suite holding it is not accepted. Nothing here monkeypatches the suite:
    ``run_replay_suite`` runs its own aggregation, with only the
    per-repetition runner stubbed so the repetitions can be made to disagree.
    """
    entry = ReplayCaseEntry(
        case_id="non-deterministic-row",
        version=1,
        title="stub",
        expected_product_result="accepted / 0",
        decisive_assertion="stub",
        build=None,
        graph_only_historical=True,
    )

    def repetition(number: int) -> ReplayRepetitionResult:
        return ReplayRepetitionResult(
            case_id=entry.case_id,
            repetition=number,
            session_id=f"{entry.case_id}-r{number}",
            terminal_quality="accepted",
            exit_code=0,
            expectation_failures=[],
            answered_target_ids=["topic-01-target-01"],
            report_fingerprint=f"{number:064d}",
        )

    row = campaign_runner._replay_case_result(
        entry, [repetition(number) for number in range(1, 4)]
    )

    assert row.deterministic is False
    assert row.passed is False

    def _differing_repetition(entry, repetition_number, *, storage):
        return repetition(repetition_number)

    monkeypatch.setattr(
        campaign_runner, "_replay_repetition", _differing_repetition
    )
    suite = run_replay_suite(
        tier="controlled", repetitions=3, output_directory=tmp_path
    )

    assert all(case.deterministic is False for case in suite.cases)
    assert all(case.passed is False for case in suite.cases)
    assert all(
        item.expectation_failures == []
        for case in suite.cases
        for item in case.repetitions
    )
    assert suite.accepted is False


# --- the clock a row is stamped from -----------------------------------------


class _MachineClockAcrossMidnight(datetime):
    """The machine's clock, moved on by whole days.

    The agents a run does not pin read the wall clock through the name their
    own module resolves: the planner dates the answer contract from ``utc_now``
    and the reader stamps its ``Generated on`` line from the same call.
    Replacing that name in those modules is how a test reproduces the two
    repetitions the whole-branch review found — the one that ran before
    00:00 UTC and the one that ran after it — without a suite that waits for
    midnight.
    """

    days_on = 0

    @classmethod
    def now(cls, tz=None):
        return datetime.now(tz) + timedelta(days=cls.days_on)


def _machine_clock_moved_on(monkeypatch, *, days: int) -> None:
    """Move every wall clock the run's own agents would fall back to."""
    monkeypatch.setattr(
        planner_agents, "datetime", _MachineClockAcrossMidnight
    )
    monkeypatch.setattr(
        researcher_agents, "datetime", _MachineClockAcrossMidnight
    )
    _MachineClockAcrossMidnight.days_on = days


def test_a_row_that_straddles_midnight_is_still_one_result(
    monkeypatch, tmp_path
) -> None:
    """The wall clock crossing midnight is not a row's result.

    A row passes only if its repetitions published the same thing, and a
    published report says which day it was printed on. Three repetitions that
    straddle 00:00 UTC therefore used to publish three differently dated
    reports and be reported as a non-deterministic row — a verdict about the
    clock the machine happened to be at, not about the agents. The harness
    stamps every repetition from its own clock, so the date is a constant of
    the row; this test moves the machine's date instead of waiting for a suite
    that straddles midnight.
    """
    entry = manifest_entry(REPLAY_CASE_IDS[0])
    repetitions = []
    for repetition in range(1, 4):
        # Repetition 1 runs before midnight UTC, repetitions 2 and 3 after it.
        _machine_clock_moved_on(monkeypatch, days=repetition - 1)
        repetitions.append(
            campaign_runner._replay_repetition(
                entry,
                repetition,
                storage=tmp_path / f"repetition-{repetition}",
            )
        )

    row = campaign_runner._replay_case_result(entry, repetitions)

    assert len({item.report_fingerprint for item in repetitions}) == 1
    assert row.deterministic is True
    assert row.passed is True


def test_a_replay_report_is_dated_by_the_harness_clock(
    monkeypatch, tmp_path
) -> None:
    """A replay report states the day the harness printed it.

    Both date lines are the harness's: ``Generated on`` is the clock's date,
    and ``As of`` is the newest evidence timestamp, which the same frozen clock
    stamped when the run recorded its reads and findings. So the whole report —
    not merely the part the fingerprint is taken over — is a function of the
    fixture and the harness's declared instant, which is what lets a row
    require determinism of it.
    """
    _machine_clock_moved_on(monkeypatch, days=1)

    with network_denied() as attempts:
        run = run_replay_scenario(
            scenario_by_id(REPLAY_CASE_IDS[0]), root=tmp_path, repetition=1
        )

    lines = run.report.splitlines()
    assert attempts == []
    assert f"**Generated on:** {REPLAY_CLOCK_INSTANT.date().isoformat()}" in lines
    assert f"**As of:** {REPLAY_CLOCK_INSTANT.isoformat()}" in lines
