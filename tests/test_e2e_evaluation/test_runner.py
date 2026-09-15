"""Controlled campaign runner and command-contract tests."""

from __future__ import annotations

import json

import pytest

from deep_research.e2e_evaluation.cases import CONTROLLED_CASE_IDS, LIVE_CASE_IDS
from deep_research.e2e_evaluation.models import CaseCampaignResult
from deep_research.e2e_evaluation.runner import (
    LIVE_TIER_NOT_RUN,
    build_judge_metadata,
    build_parser,
    run_case,
    run_suite,
)


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
    assert tmp_path.joinpath(
        "broad-constraints", "repetition-1", "report.md"
    ).is_file()
    assert tmp_path.joinpath(
        "broad-constraints", "repetition-1", "evidence-ledger.md"
    ).is_file()
    assert result.repetitions[0].metadata.request_counts["query_memory"] == 1
    assert result.repetitions[0].metadata.request_counts["write_document"] == 2
    restored = json.loads(
        tmp_path.joinpath("broad-constraints", "case.json").read_text()
    )
    assert CaseCampaignResult.model_validate(restored) is not None


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
