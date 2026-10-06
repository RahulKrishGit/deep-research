"""The stage replay asks a capture again under one arm."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from deep_research.agents.evidence_verifier import (
    ContextCheckDraft,
    EvidenceVerifierAgent,
    FigureCheckDraft,
    StatementCheckDraft,
    StatementCheckItem,
    StatementVerdictDraft,
)
from deep_research.agents.prompts import STRUCTURED_REQUEST_END
from deep_research.experiments.stage_replay import (
    agreement,
    replay_capture,
    summarize,
)
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import (
    Tracker,
    bind_stage_capture,
    capture_node_input,
    capture_statement_check,
)
from deep_research.utils.config import AgentRuntimeConfig
from deep_research.utils.types import ResearchState
from tests.agent_fakes import ScriptedCompleter
from tests.evidence_fakes import figure, make_finding, make_read


class _ConfirmingCompleter(ScriptedCompleter):
    """Confirms every figure and every sentence it is asked about, and records
    how many it was asked about per call."""

    def __init__(self) -> None:
        super().__init__()
        self.asked: list[tuple[str, int]] = []

    async def complete_structured(self, messages: Any, schema: type, **_: Any) -> Any:
        body = messages[1].content
        if schema is ContextCheckDraft:
            section = body.split("# Figures to check\n", 1)[1]
            section = section.split("\n\n" + STRUCTURED_REQUEST_END, 1)[0]
            figures = []
            for block in re.split(r"\n\n(?=## )", section):
                figures.append(
                    FigureCheckDraft(
                        finding=re.match(r"## (F\d+)", block).group(1),
                        figure=1,
                        attribution="own",
                        kind="actual",
                        evidence_words=re.findall(r"snippet: (.*)", block)[-1],
                        verdict="confirm",
                        reason="As stated.",
                    )
                )
            self.asked.append(("context", len(figures)))
            return ContextCheckDraft(figures=figures)
        labels = re.findall(r"## (S\d+)", body)
        self.asked.append(("statement", len(labels)))
        return StatementCheckDraft(
            statements=[
                StatementVerdictDraft(label=label, verdict="consistent", reason="As cited.")
                for label in labels
            ]
        )


def _capture(directory: Path) -> None:
    sentences = [f"Site {index:03d} added {10 + index} GW of capacity in 2025." for index in range(4)]
    read = make_read(" ".join(sentences), url="https://example.test/sites", title="Site metrics")
    findings = [
        make_finding(
            read,
            sentence,
            figures=[figure(str(10 + index), "GW", "2025", "actual")],
            content=f"Site {index:03d} capacity finding",
        )
        for index, sentence in enumerate(sentences)
    ]
    state = ResearchState.model_validate(
        {
            "session_id": "session-1",
            "original_question": "How much capacity was added?",
            "raw_findings": [finding.model_dump(mode="json") for finding in findings],
            "read_records": {read.read_id: read.model_dump(mode="json")},
        }
    )
    items = [
        StatementCheckItem(
            label=f"S{index:02d}",
            text=sentence,
            findings=[findings[0]],
            labels=["F01"],
        )
        for index, sentence in enumerate(sentences[:3], 1)
    ]
    with bind_stage_capture(directory, nodes=["evidence_verifier"]):
        capture_node_input("evidence_verifier", state)
        capture_statement_check("How much capacity was added?", items)


def _verifier(name: str, settings: Any, *, tracker: Tracker, provider: Any, **_: Any) -> Any:
    assert name == "evidence_verifier"
    return EvidenceVerifierAgent(
        provider=provider,
        tracker=tracker,
        scratchpad=ScratchpadMemory(
            session_id="session-1", agent_name=name, max_entries=20
        ),
        config=settings.agents,
    )


@pytest.mark.asyncio
async def test_a_capture_is_asked_again_at_each_arms_batch_size(
    tmp_path: Path, tracker: Tracker
) -> None:
    _capture(tmp_path)
    arms = {}
    for size in (2, 5):
        completer = _ConfirmingCompleter()
        settings = SimpleNamespace(
            agents=AgentRuntimeConfig(verifier_batch_size=size, verifier_concurrency=64)
        )
        arms[size] = await replay_capture(
            tmp_path, settings=settings, provider=completer, tracker=tracker,
            build_agent=_verifier,
        )
        asked = sorted(completer.asked)
        if size == 2:
            assert asked == [("context", 2), ("context", 2), ("statement", 1), ("statement", 2)]
        else:
            assert asked == [("context", 4), ("statement", 3)]

    for results in arms.values():
        context, statement = results
        assert (context.stage, context.source) == ("context_check", "evidence_verifier-01.json")
        assert context.counts["figures_judged"] == 4
        assert context.counts["findings_verified"] == 4
        assert context.counts["figures_dropped"] == 0
        assert (statement.stage, statement.counts["consistent"]) == ("statement_check", 3)
    assert agreement(arms[2][0].verdicts, arms[5][0].verdicts) == 1.0
    assert agreement(arms[2][1].verdicts, arms[5][1].verdicts) == 1.0


def _write(out: Path, arm: str, repetition: int, *, kept: dict[str, Any], dropped: int,
           seconds: float, inconsistent: int = 0) -> None:
    results = [
        {"stage": "context_check", "source": "evidence_verifier-01.json", "seconds": seconds,
         "verdicts": kept,
         "counts": {"figures_judged": len(kept), "figures_dropped": dropped,
                    "figures_corrected": 0, "figures_unchecked": 0,
                    "findings_verified": len(kept) - dropped, "findings_corrected": 0,
                    "findings_dropped": dropped, "errors": 0}},
        {"stage": "statement_check", "source": "1 calls", "seconds": 1.0,
         "verdicts": {"statement_check-001.json#S01": "consistent"},
         "counts": {"statements": 1, "consistent": 1 - inconsistent, "corrected": 0,
                    "inconsistent": inconsistent, "unjudged": 0, "errors": 0}},
    ]
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{arm}-{repetition}.json").write_text(json.dumps({"results": results}), "utf-8")


def _verdicts(*dropped: str) -> dict[str, Any]:
    return {
        f"f{index}#1": ["context_rejected" if f"f{index}" in dropped else None, False, False,
                        "2025", None, None, "own", "actual"]
        for index in range(1, 11)
    }


def test_a_treatment_inside_the_controls_noise_passes(tmp_path: Path) -> None:
    _write(tmp_path, "control", 1, kept=_verdicts(), dropped=0, seconds=200.0)
    _write(tmp_path, "control", 2, kept=_verdicts("f1"), dropped=1, seconds=210.0)
    _write(tmp_path, "control", 3, kept=_verdicts(), dropped=0, seconds=190.0)
    for repetition, seconds in enumerate((120.0, 130.0, 110.0), 1):
        _write(tmp_path, "treatment", repetition, kept=_verdicts(), dropped=0, seconds=seconds)

    summary = summarize(tmp_path)

    assert summary["context_check"]["agreement_control_floor"] == 0.9
    assert summary["context_check"]["agreement_treatment"] == pytest.approx(0.9667, abs=1e-4)
    assert summary["passed"] is True


def test_a_treatment_that_drops_more_or_agrees_less_fails(tmp_path: Path) -> None:
    for repetition in (1, 2, 3):
        _write(tmp_path, "control", repetition, kept=_verdicts(), dropped=0, seconds=200.0)
        _write(tmp_path, "treatment", repetition, kept=_verdicts("f2", "f3"), dropped=2,
               seconds=100.0, inconsistent=1)

    summary = summarize(tmp_path)

    assert summary["checks"]["context_check.agreement"] is False
    assert summary["checks"]["context_check.figures_dropped"] is False
    assert summary["checks"]["context_check.findings_kept"] is False
    assert summary["checks"]["statement_check.inconsistent"] is False
    assert summary["checks"]["context_check.faster"] is True
    assert summary["passed"] is False


def test_a_summary_fails_closed_when_the_context_check_has_no_verdicts(tmp_path: Path) -> None:
    """A capture with nothing for the Context Check must not pass on the
    Statement Check alone, however much slower the treatment is."""
    for repetition in (1, 2, 3):
        _write(tmp_path, "control", repetition, kept={}, dropped=0, seconds=10.0)
        _write(tmp_path, "treatment", repetition, kept={}, dropped=0, seconds=50.0)

    summary = summarize(tmp_path)

    assert summary["context_check"] is None
    assert summary["checks"]["context_check.present"] is False
    assert summary["checks"]["statement_check.agreement"] is True
    assert summary["checks"]["statement_check.inconsistent"] is True
    assert summary["checks"]["statement_check.unjudged"] is True
    assert summary["passed"] is False


def test_a_summary_needs_two_controls_and_a_treatment(tmp_path: Path) -> None:
    _write(tmp_path, "control", 1, kept=_verdicts(), dropped=0, seconds=1.0)
    _write(tmp_path, "treatment", 1, kept=_verdicts(), dropped=0, seconds=1.0)

    with pytest.raises(ValueError, match="need at least two control"):
        summarize(tmp_path)


def test_the_run_command_writes_one_arm_file_through_the_production_wiring(
    tmp_path: Path, tracker: Tracker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real settings, the real ``build_agent``; only the provider and the
    tracker are stand-ins, so no request leaves the machine."""
    import deep_research.experiments.stage_replay as stage_replay
    import deep_research.providers as providers
    from deep_research.observability import Tracker as TrackerClass
    from deep_research.utils.config import load_config

    completer = _ConfirmingCompleter()
    monkeypatch.setattr(
        stage_replay, "_settings",
        lambda config, override: load_config(config, overrides=json.loads(override)),
    )
    monkeypatch.setattr(providers, "build_chat_provider", lambda *args, **kwargs: completer)
    monkeypatch.setattr(TrackerClass, "from_config", classmethod(lambda cls, config: tracker))
    _capture(tmp_path / "capture")

    code = stage_replay.main(
        [
            "run", "--capture", str(tmp_path / "capture"), "--arm", "treatment",
            "--repetition", "2", "--override", '{"agents": {"verifier_batch_size": 2}}',
            "--out", str(tmp_path / "out"),
        ],
        now=lambda: datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc),
    )

    assert code == 0
    written = json.loads((tmp_path / "out" / "treatment-2.json").read_text("utf-8"))
    assert written["override"] == {"agents": {"verifier_batch_size": 2}}
    assert [result["stage"] for result in written["results"]] == ["context_check", "statement_check"]
    assert sorted(completer.asked) == [("context", 2), ("context", 2), ("statement", 1), ("statement", 2)]


def test_the_summarize_command_exits_nonzero_when_the_treatment_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from deep_research.experiments.stage_replay import main

    for repetition in (1, 2):
        _write(tmp_path, "control", repetition, kept=_verdicts(), dropped=0, seconds=200.0)
    _write(tmp_path, "treatment", 1, kept=_verdicts("f1"), dropped=1, seconds=100.0)

    assert main(["summarize", "--out", str(tmp_path)]) == 1
    assert json.loads(capsys.readouterr().out)["passed"] is False


def test_a_replay_refuses_to_start_inside_deepseeks_peak_hours(tmp_path: Path) -> None:
    from deep_research.experiments.stage_replay import main

    code = main(
        ["run", "--capture", str(tmp_path), "--arm", "control", "--repetition", "1",
         "--override", "{}", "--out", str(tmp_path / "out")],
        now=lambda: datetime(2026, 10, 3, 0, 30, tzinfo=timezone.utc),
    )

    assert code == 2
    assert not (tmp_path / "out").exists()
