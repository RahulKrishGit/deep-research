"""Controlled campaign runner and command-contract tests."""

from __future__ import annotations

import asyncio
import dataclasses
import json
from pathlib import Path

import pytest

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
from deep_research.e2e_evaluation.models import CaseCampaignResult, ReplaySuiteResult
from deep_research.e2e_evaluation.replay_matrix import (
    REPLAY_CASE_IDS,
    REPLAY_CASE_MANIFEST,
)
from deep_research.e2e_evaluation.runner import (
    LIVE_TIER_NOT_RUN,
    build_judge_metadata,
    build_parser,
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


def test_controlled_suite_has_three_cases_and_no_report_body_on_summary(
    tmp_path,
) -> None:
    result = run_suite(tier="controlled", repetitions=3, output_directory=tmp_path)

    assert result.accepted
    assert [case.case_id for case in result.cases] == list(CONTROLLED_CASE_IDS)
    assert all(len(case.repetitions) == 3 for case in result.cases)
    assert result.metadata["network"] == "zero"
    assert tmp_path.joinpath("suite.json").is_file()
    assert result.artifact_path == str(tmp_path / "suite.json")
    assert result.langsmith_metadata["quality_gate_version"] == 1


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


def test_cli_accepts_a_live_case_id_without_executing_it() -> None:
    options = build_parser().parse_args(
        ["case", LIVE_CASE_IDS[0], "--tier", "live"]
    )

    assert options.case_id == LIVE_CASE_IDS[0]
    assert options.tier == "live"
