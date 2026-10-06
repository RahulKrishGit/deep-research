"""Real-agent replay matrix runner and command-contract tests."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

import pytest

import deep_research.agents.planner as planner_agents
import deep_research.agents.researcher as researcher_agents
import deep_research.e2e_evaluation.runner as campaign_runner
from deep_research.e2e_evaluation.models import (
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
    REPLAY_CASE_IDS,
    REPLAY_CASE_MANIFEST,
    ReplayCaseEntry,
    manifest_entry,
    scenario_by_id,
)
from deep_research.e2e_evaluation.runner import (
    LIVE_TIER_NOT_RUN,
    build_parser,
    canonical_report_fingerprint,
    network_line,
    real_agent_suite_lines,
    run_replay_suite,
)


def test_the_replay_suite_runs_the_whole_manifest_not_the_three_legacy_rows(
    tmp_path,
) -> None:
    """The controlled suite's inventory is the manifest, not a hardcoded three.

    The inventory is the versioned manifest, so a row that is added, removed
    or re-versioned changes what the suite runs without editing the runner --
    and every declared row is evidence a release is read from.
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
    assert tmp_path.joinpath("replay-suite.json").is_file()
    assert result.artifact_path == str(tmp_path / "replay-suite.json")
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
                    extra_passes=0,
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

    A ledger saying "real agents" is not disclosure if the command's own
    output does not say it. The line names the harness, the inventory it was
    read from, and where the evidence landed, in the output itself.
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
    """The suite command's output says which agents ran."""
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


def test_the_list_command_shows_the_real_agent_inventory(capsys) -> None:
    """The list command shows one inventory, one label."""
    code = campaign_runner.main(["list"])
    printed = capsys.readouterr().out.splitlines()

    assert code == 0
    text = "\n".join(printed)
    assert "Mode: real-agent" in text
    assert "Agents: production classes through the real graph" in text
    assert "SCRIPTED" not in text
    assert "scripted-double" not in text
    for case_id in REPLAY_CASE_IDS:
        assert f"  {case_id}: " in text


def test_the_replay_suite_is_bounded_to_exactly_three_repetitions(tmp_path) -> None:
    with pytest.raises(ValueError, match="exactly 3"):
        run_replay_suite(tier="controlled", repetitions=2, output_directory=tmp_path)
    with pytest.raises(RuntimeError, match=LIVE_TIER_NOT_RUN):
        run_replay_suite(tier="live", repetitions=3, output_directory=tmp_path)
    assert not list(tmp_path.rglob("*.json"))


def test_cli_exposes_only_list_and_suite(capsys) -> None:
    """The CLI exposes only list and suite commands."""
    actions = [
        action
        for action in build_parser()._subparsers._group_actions
        if hasattr(action, "choices")
    ]
    assert len(actions) == 1
    assert set(actions[0].choices) == {"list", "suite"}


def _never_built() -> object:
    raise AssertionError("a stub row must never be asked for its scenario")


def test_a_row_whose_repetitions_disagree_is_not_passed(
    monkeypatch, tmp_path
) -> None:
    """Determinism is required of a row, not merely reported beside it.

    Three repetitions exist for deterministic order and identity and for
    state-isolation, so a row whose repetitions each meet the declared result
    but disagree on the published report is not a row that passed -- and a
    suite holding it is not accepted. Nothing here monkeypatches the suite:
    ``run_replay_suite`` runs its own aggregation, with only the
    per-repetition runner stubbed so the repetitions can be made to disagree.
    """
    entry = ReplayCaseEntry(
        case_id="non-deterministic-row",
        version=1,
        title="stub",
        expected_product_result="accepted / 0",
        decisive_assertion="stub",
        build=_never_built,  # type: ignore[arg-type]
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
            extra_passes=0,
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
    Replacing that name in those modules is how a test reproduces two
    repetitions run on different sides of midnight UTC, without a suite that
    waits for midnight.
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
    straddle 00:00 UTC would therefore publish three differently dated
    reports and be reported as a non-deterministic row -- a verdict about the
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
    """A replay report states the harness's pinned clock, not the machine's.

    The evidence line's date is the newest evidence timestamp, which the
    frozen clock stamped when the run recorded its reads and findings. So the
    whole report -- not merely the part the fingerprint is taken over -- is a
    function of the fixture and the harness's declared instant, which is what
    lets a row require determinism of it, whatever the machine's own clock
    reads.
    """
    _machine_clock_moved_on(monkeypatch, days=1)

    with network_denied() as attempts:
        run = run_replay_scenario(
            scenario_by_id(REPLAY_CASE_IDS[0]), root=tmp_path, repetition=1
        )

    lines = run.report.splitlines()
    assert attempts == []
    assert any(
        line.startswith(f"Evidence as of {REPLAY_CLOCK_INSTANT:%Y-%m-%d} ")
        for line in lines
    )


# --- what the report fingerprint is taken over --------------------------------


def _published_report(*, as_of: str, sources: int) -> str:
    """A report whose evidence line is the only thing that moves.

    The evidence line is the reader's own line (``agents.report.render_written_report``):
    the evidence date and the source count. Everything
    below it is fixed, so two of these differ only in what the reader was
    told.
    """
    return (
        "# Was the grid modernised in 2024?\n"
        "\n"
        f"Evidence as of {as_of} · {sources} source(s)\n"
        "\n"
        "## Bottom line\n"
        "\n"
        "The grid was modernised [1].\n"
        "\n"
        "## Sources\n"
        "\n"
        "1. Example Agency — [Grid report](https://example.test/grid)\n"
    )


def test_the_byline_the_reader_was_shown_is_hashed() -> None:
    """The evidence line's date and source count are part of a repetition's
    own outcome.

    The evidence line is one line: the evidence date and how many sources the
    reader was told were cited. The harness clock is pinned, so the date is a
    constant of the row and needs no stripping; a fingerprint that skipped
    the whole line could not tell two repetitions apart when only the date or
    the count moved.
    """
    baseline = canonical_report_fingerprint(
        _published_report(as_of="2026-01-02", sources=3)
    )

    assert canonical_report_fingerprint(
        _published_report(as_of="2026-01-03", sources=3)
    ) != baseline
    assert canonical_report_fingerprint(
        _published_report(as_of="2026-01-02", sources=4)
    ) != baseline
    # The same report is one fingerprint, so the two differences above are the
    # evidence line rather than the fixture.
    assert canonical_report_fingerprint(
        _published_report(as_of="2026-01-02", sources=3)
    ) == baseline


# --- the counts a recorded repetition carries ---------------------------------


def test_a_recorded_repetition_carries_the_seven_counts_of_its_final_state(
    monkeypatch, tmp_path
) -> None:
    """A row's recorded result states the product's own seven counts.

    ``verified_findings``, ``dropped_findings``, ``context_unchecked_findings``,
    ``duplicate_fact_rows``, ``unjudged_sentences``, ``missing_required_targets``
    and ``extra_passes`` are read from the state the run finished in, never
    re-derived here: a harness that counted for itself could only disagree with
    the run it is judging. The row is the one that spends a real extra pass, so
    its iteration count is asserted against the harness's own cap as well.
    """
    entry = manifest_entry("extra-pass-recovers-missing-target")
    states: list[Any] = []
    real_run = campaign_runner.run_replay_scenario

    def _capturing_run(*args, **kwargs):
        run = real_run(*args, **kwargs)
        states.append(run.state)
        return run

    monkeypatch.setattr(campaign_runner, "run_replay_scenario", _capturing_run)

    recorded = campaign_runner._replay_repetition(entry, 1, storage=tmp_path)

    state = states[0]
    quality = state.quality
    assert quality is not None
    assert recorded.verified_findings == quality.verified_findings
    assert recorded.dropped_findings == quality.dropped_findings
    assert recorded.context_unchecked_findings == quality.context_unchecked_findings
    assert recorded.duplicate_fact_rows == quality.duplicate_fact_rows
    assert recorded.unjudged_sentences == len(quality.unjudged_sentences)
    assert recorded.missing_required_targets == len(
        quality.missing_required_target_ids
    )
    assert recorded.extra_passes == state.iteration == 1
