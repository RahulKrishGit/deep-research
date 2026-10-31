"""The stage replay asks a capture again under one arm."""

from __future__ import annotations

import asyncio
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
from deep_research.agents.researcher import FindingDraft, SubTopicFindingsDraft
from deep_research.experiments.stage_replay import (
    PAGE_EXTRACTION,
    STATEMENT_CHECK,
    agreement,
    replay_capture,
    summarize,
)
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import (
    Tracker,
    bind_stage_capture,
    capture_node_input,
    capture_page_extraction,
    capture_statement_check,
)
from deep_research.providers import ChatMessage, ProviderError
from deep_research.utils.config import AgentRuntimeConfig
from deep_research.utils.types import ResearchState, SubTopic
from tests.agent_fakes import LabelRecordingCompleter, ScriptedCompleter
from tests.evidence_fakes import figure, make_finding, make_read


class _ConfirmingCompleter(ScriptedCompleter):
    """Confirms every figure and every sentence it is asked about, and records
    how many it was asked about per call and the effort each call asked for."""

    def __init__(self) -> None:
        super().__init__()
        self.asked: list[tuple[str, int]] = []
        self.stage_efforts: list[tuple[str, str | None, bool]] = []
        """Per call: the schema's name, the effort passed, and whether the
        ``reasoning_effort`` keyword was passed at all."""

    async def complete_structured(self, messages: Any, schema: type, **kwargs: Any) -> Any:
        self.stage_efforts.append(
            (schema.__name__, kwargs.get("reasoning_effort"), "reasoning_effort" in kwargs)
        )
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


@pytest.mark.asyncio
async def test_a_replayed_statement_check_uses_the_arm_effort(
    tmp_path: Path, tracker: Tracker
) -> None:
    """The arm's own ``statement_check`` effort reaches every replayed
    Statement Check call, and its ``context_check`` effort the replayed
    Context Check's: each stage runs at its own, not at the other's."""
    _capture(tmp_path)
    completer = _ConfirmingCompleter()
    settings = SimpleNamespace(
        agents=AgentRuntimeConfig(
            verifier_batch_size=2,
            verifier_concurrency=64,
            operation_efforts={"statement_check": "low", "context_check": "high"},
        )
    )

    await replay_capture(
        tmp_path, settings=settings, provider=completer, tracker=tracker,
        build_agent=_verifier,
    )

    assert sorted(completer.stage_efforts) == [
        ("ContextCheckDraft", "high", True),
        ("ContextCheckDraft", "high", True),
        ("StatementCheckDraft", "low", True),
        ("StatementCheckDraft", "low", True),
    ]


@pytest.mark.asyncio
async def test_a_replay_sends_no_effort_when_the_arm_sets_none(
    tmp_path: Path, tracker: Tracker
) -> None:
    """The shipped empty map: no replayed call carries the keyword, so a replay
    is the request it was before per-operation efforts existed."""
    _capture(tmp_path)
    completer = _ConfirmingCompleter()
    settings = SimpleNamespace(
        agents=AgentRuntimeConfig(verifier_batch_size=2, verifier_concurrency=64)
    )

    await replay_capture(
        tmp_path, settings=settings, provider=completer, tracker=tracker,
        build_agent=_verifier,
    )

    assert completer.stage_efforts
    assert [(effort, sent) for _, effort, sent in completer.stage_efforts] == [
        (None, False)
    ] * len(completer.stage_efforts)


def _write(out: Path, arm: str, repetition: int, *, kept: dict[str, Any], dropped: int,
           seconds: float, inconsistent: int = 0,
           stages: tuple[str, ...] | None = None) -> None:
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
    payload: dict[str, Any] = {"results": results}
    if stages is not None:
        payload["stages"] = list(stages)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{arm}-{repetition}.json").write_text(json.dumps(payload), "utf-8")


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
    assert written["stages"] == ["context_check", "statement_check", "page_extraction"]
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


_PAGE_SENTENCES = (
    "Wood Mackenzie added 18.9 GW of storage in 2025.",
    "Lazard priced storage at 120 dollars per MWh in 2025.",
)


def _page_request() -> list[ChatMessage]:
    return [
        ChatMessage(role="developer", content="Extract findings."),
        ChatMessage(role="user", content=f"Retrieved evidence: {' '.join(_PAGE_SENTENCES)}"),
    ]


def _page_capture(directory: Path, *, pages: int = 1) -> Any:
    """``pages`` page-extraction requests, as one read's extraction asks them."""
    read = make_read(
        " ".join(_PAGE_SENTENCES), url="https://example.test/storage", title="Storage additions"
    )
    sub_topic = SubTopic(
        coverage_id="topic-01",
        title="Battery storage",
        rationale="How much storage was added.",
        search_queries=["battery storage additions 2025"],
        success_criteria=["Names the capacity added."],
        priority=1,
    )
    with bind_stage_capture(directory, nodes=()):
        for _ in range(pages):
            capture_page_extraction(
                _page_request(),
                sub_topic=sub_topic,
                known_reads={read.read_id: read},
                valid_target_ids=["t1", "t2"],
            )
    return read


def _draft(read: Any, sentence: str, *, read_id: str | None = None,
           target_ids: tuple[str, ...] = ()) -> FindingDraft:
    return FindingDraft(
        content=sentence,
        source_url=read.resolved_url,
        source_title=read.title,
        confidence=0.9,
        read_id=read_id or read.read_id,
        locator="page-1-chunk-0",
        snippet=sentence,
        target_ids=list(target_ids),
    )


def _page_settings(**operation_efforts: Any) -> Any:
    return SimpleNamespace(agents=AgentRuntimeConfig(operation_efforts=operation_efforts))


@pytest.mark.asyncio
async def test_a_replayed_page_extraction_reports_bound_targets_and_counts(
    tmp_path: Path, tracker: Tracker
) -> None:
    read = _page_capture(tmp_path)
    fake = LabelRecordingCompleter(
        outputs=[
            SubTopicFindingsDraft(
                findings=[
                    _draft(read, _PAGE_SENTENCES[0], target_ids=("t1",)),
                    _draft(read, _PAGE_SENTENCES[1], read_id="r-never-shown", target_ids=("t2",)),
                ]
            )
        ]
    )

    [result] = await replay_capture(
        tmp_path, settings=_page_settings(page_extraction="low"), provider=fake,
        tracker=tracker, build_agent=None, stages=(PAGE_EXTRACTION,),
    )

    assert (result.stage, result.source) == ("page_extraction", "1 calls")
    assert result.verdicts == {"page_extraction-001.json#t1": True}
    assert result.counts == {
        "findings_kept": 1, "findings_rejected": 1, "targets_bound": 1, "provider_errors": 0,
    }
    assert fake.efforts == ["low"]
    assert fake.labels == ["page_extraction"]
    [(schema, agent_name, messages)] = fake.calls
    assert (schema, agent_name) == ("SubTopicFindingsDraft", "researcher")
    assert messages == _page_request()


@pytest.mark.asyncio
async def test_a_replayed_page_extraction_sends_no_effort_when_the_arm_sets_none(
    tmp_path: Path, tracker: Tracker
) -> None:
    """The shipped empty map: the replayed request carries no effort keyword,
    so it is the request the page extraction made before efforts existed."""
    _page_capture(tmp_path)
    fake = LabelRecordingCompleter(outputs=[SubTopicFindingsDraft(findings=[])])

    await replay_capture(
        tmp_path, settings=_page_settings(), provider=fake, tracker=tracker,
        build_agent=None, stages=(PAGE_EXTRACTION,),
    )

    assert fake.effort_kwarg_sent == [False]


@pytest.mark.asyncio
async def test_a_provider_failure_counts_and_leaves_that_page_without_verdicts(
    tmp_path: Path, tracker: Tracker
) -> None:
    read = _page_capture(tmp_path, pages=2)
    fake = ScriptedCompleter(
        outputs=[
            ProviderError("provider is down"),
            SubTopicFindingsDraft(findings=[_draft(read, _PAGE_SENTENCES[0], target_ids=("t1",))]),
        ]
    )

    [result] = await replay_capture(
        tmp_path, settings=_page_settings(), provider=fake, tracker=tracker,
        build_agent=None, stages=(PAGE_EXTRACTION,),
    )

    assert result.source == "2 calls"
    assert result.verdicts == {"page_extraction-002.json#t1": True}
    assert result.counts == {
        "findings_kept": 1, "findings_rejected": 0, "targets_bound": 1, "provider_errors": 1,
    }


class _InFlightCompleter(ScriptedCompleter):
    """Records how many structured calls were in flight at once."""

    def __init__(self, outputs: list[Any]) -> None:
        super().__init__(outputs=outputs)
        self.running = 0
        self.peak = 0

    async def complete_structured(self, messages: Any, schema: type, **kwargs: Any) -> Any:
        self.running += 1
        self.peak = max(self.peak, self.running)
        try:
            await asyncio.sleep(0)
            return await super().complete_structured(messages, schema, **kwargs)
        finally:
            self.running -= 1


@pytest.mark.asyncio
async def test_page_extraction_files_share_one_extraction_concurrency_gate(
    tmp_path: Path, tracker: Tracker
) -> None:
    _page_capture(tmp_path, pages=5)
    fake = _InFlightCompleter([SubTopicFindingsDraft(findings=[]) for _ in range(5)])
    settings = SimpleNamespace(agents=AgentRuntimeConfig(extraction_concurrency=2))

    [result] = await replay_capture(
        tmp_path, settings=settings, provider=fake, tracker=tracker, build_agent=None,
        stages=(PAGE_EXTRACTION,),
    )

    assert result.source == "5 calls"
    assert fake.peak == 2


@pytest.mark.asyncio
async def test_a_replay_asks_only_the_stages_it_is_given(
    tmp_path: Path, tracker: Tracker
) -> None:
    """``build_agent=None`` and the page capture on disk: neither the Context
    Check nor the page extraction is touched when only the Statement Check is asked."""
    _capture(tmp_path)
    _page_capture(tmp_path)
    completer = _ConfirmingCompleter()
    settings = SimpleNamespace(
        agents=AgentRuntimeConfig(verifier_batch_size=5, verifier_concurrency=64)
    )

    results = await replay_capture(
        tmp_path, settings=settings, provider=completer, tracker=tracker,
        build_agent=None, stages=(STATEMENT_CHECK,),
    )

    assert [result.stage for result in results] == ["statement_check"]
    assert completer.asked == [("statement", 3)]


@pytest.mark.asyncio
async def test_a_replay_with_no_stages_given_asks_the_page_extraction_too(
    tmp_path: Path, tracker: Tracker
) -> None:
    _page_capture(tmp_path)
    fake = LabelRecordingCompleter(outputs=[SubTopicFindingsDraft(findings=[])])

    results = await replay_capture(
        tmp_path, settings=_page_settings(), provider=fake, tracker=tracker, build_agent=None
    )

    assert [result.stage for result in results] == ["page_extraction"]


def _write_extraction(
    out: Path, arm: str, repetition: int, *, bound: tuple[str, ...], rejected: int = 0,
    errors: int = 0, reasoning_tokens: int | None = None,
    stages: tuple[str, ...] = ("page_extraction",),
) -> None:
    results = [
        {"stage": "page_extraction", "source": "1 calls", "seconds": 10.0,
         "verdicts": {f"page_extraction-001.json#{target}": True for target in bound},
         "counts": {"findings_kept": len(bound), "findings_rejected": rejected,
                    "targets_bound": len(bound), "provider_errors": errors}},
    ]
    payload: dict[str, Any] = {"stages": list(stages), "results": results}
    if reasoning_tokens is not None:
        payload["telemetry"] = {"reasoning_tokens": reasoning_tokens}
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{arm}-{repetition}.json").write_text(json.dumps(payload), "utf-8")


def test_summarize_fails_a_treatment_that_binds_fewer_targets(tmp_path: Path) -> None:
    for repetition in (1, 2):
        _write_extraction(tmp_path, "control", repetition, bound=("t1", "t2"))
    _write_extraction(tmp_path, "treatment", 1, bound=("t1",))

    summary = summarize(tmp_path)

    assert summary["checks"]["page_extraction.targets_bound"] is False
    assert summary["checks"]["page_extraction.findings_kept"] is False
    assert summary["checks"]["page_extraction.agreement"] is False
    assert summary["passed"] is False


def test_summarize_passes_a_page_extraction_that_matches_the_control_and_reports_reasoning(
    tmp_path: Path,
) -> None:
    for repetition, tokens in ((1, 900), (2, 1100)):
        _write_extraction(tmp_path, "control", repetition, bound=("t1", "t2"), reasoning_tokens=tokens)
    _write_extraction(tmp_path, "treatment", 1, bound=("t1", "t2"), reasoning_tokens=300)

    summary = summarize(tmp_path)

    assert summary["checks"] == {
        "page_extraction.agreement": True,
        "page_extraction.findings_kept": True,
        "page_extraction.targets_bound": True,
        "page_extraction.provider_errors": True,
    }
    assert summary["passed"] is True
    assert summary["page_extraction"]["control_reasoning_tokens"] == [900, 1100]
    assert summary["page_extraction"]["treatment_reasoning_tokens"] == [300]


def test_summarize_fails_a_treatment_with_more_provider_errors_than_the_control(
    tmp_path: Path,
) -> None:
    for repetition in (1, 2):
        _write_extraction(tmp_path, "control", repetition, bound=("t1",))
    _write_extraction(tmp_path, "treatment", 1, bound=("t1",), errors=1)

    summary = summarize(tmp_path)

    assert summary["checks"]["page_extraction.provider_errors"] is False
    assert summary["checks"]["page_extraction.targets_bound"] is True
    assert summary["passed"] is False


def test_summarize_judges_only_the_stages_a_replay_ran(tmp_path: Path) -> None:
    for repetition in (1, 2):
        _write_extraction(tmp_path, "control", repetition, bound=("t1",))
    _write_extraction(tmp_path, "treatment", 1, bound=("t1",))

    summary = summarize(tmp_path)

    assert not any(check.startswith(("context_check", "statement_check")) for check in summary["checks"])
    assert "context_check" not in summary and "statement_check" not in summary


def test_summarize_judges_the_context_and_statement_checks_of_a_replay_that_lists_no_stages(
    tmp_path: Path,
) -> None:
    """Outputs written before ``--stages`` existed carry no ``stages`` key and
    were judged on those two stages; they still are, and never on the page extraction."""
    for repetition in (1, 2):
        _write(tmp_path, "control", repetition, kept=_verdicts(), dropped=0, seconds=200.0)
    _write(tmp_path, "treatment", 1, kept=_verdicts(), dropped=0, seconds=100.0)

    summary = summarize(tmp_path)

    assert summary["checks"]["context_check.agreement"] is True
    assert summary["checks"]["statement_check.agreement"] is True
    assert not any(check.startswith("page_extraction") for check in summary["checks"])
    assert "page_extraction" not in summary
    assert summary["passed"] is True


def test_a_listed_stage_the_control_has_no_verdicts_for_fails_closed(tmp_path: Path) -> None:
    """A replay that asked for the page extraction but whose capture holds none
    must not pass on the other stages alone."""
    stages = ("context_check", "statement_check", "page_extraction")
    for repetition in (1, 2):
        _write(tmp_path, "control", repetition, kept=_verdicts(), dropped=0, seconds=200.0,
               stages=stages)
    _write(tmp_path, "treatment", 1, kept=_verdicts(), dropped=0, seconds=100.0, stages=stages)

    summary = summarize(tmp_path)

    assert summary["checks"]["page_extraction.present"] is False
    assert summary["page_extraction"] is None
    assert summary["checks"]["context_check.agreement"] is True
    assert summary["passed"] is False


def test_summarize_refuses_control_repetitions_that_list_different_stages(tmp_path: Path) -> None:
    """A control that skipped a stage contributes no verdicts for it, which drops the
    agreement floor and every minimum count to 0: a treatment that bound no target
    would pass on that stage, so the mixed listing is refused instead."""
    _write_extraction(tmp_path, "control", 1, bound=("t1", "t2"))
    _write(tmp_path, "control", 2, kept=_verdicts(), dropped=0, seconds=200.0,
           stages=("context_check", "statement_check", "page_extraction"))
    _write_extraction(tmp_path, "treatment", 1, bound=())

    with pytest.raises(ValueError, match="same stages") as caught:
        summarize(tmp_path)

    assert "control: page_extraction" in str(caught.value)
    assert "control: context_check,statement_check,page_extraction" in str(caught.value)


def test_summarize_refuses_a_treatment_that_lists_other_stages_than_the_control(
    tmp_path: Path,
) -> None:
    for repetition in (1, 2):
        _write_extraction(tmp_path, "control", repetition, bound=("t1",))
    _write(tmp_path, "treatment", 1, kept=_verdicts(), dropped=0, seconds=100.0)

    with pytest.raises(ValueError, match="same stages") as caught:
        summarize(tmp_path)

    assert "control: page_extraction" in str(caught.value)
    assert "treatment: context_check,statement_check" in str(caught.value)


def test_summarize_counts_an_output_with_no_stages_as_the_legacy_stages(tmp_path: Path) -> None:
    """An output written before ``--stages`` existed lists the Context Check and the
    Statement Check, so it sits beside one that lists exactly those two."""
    for repetition in (1, 2):
        _write(tmp_path, "control", repetition, kept=_verdicts(), dropped=0, seconds=200.0)
    _write(tmp_path, "treatment", 1, kept=_verdicts(), dropped=0, seconds=100.0,
           stages=("statement_check", "context_check"))

    assert summarize(tmp_path)["passed"] is True


def test_the_run_command_writes_the_stages_it_was_asked_for(
    tmp_path: Path, tracker: Tracker, monkeypatch: pytest.MonkeyPatch
) -> None:
    import deep_research.experiments.stage_replay as stage_replay
    import deep_research.providers as providers
    from deep_research.observability import Tracker as TrackerClass
    from deep_research.utils.config import load_config

    read = _page_capture(tmp_path / "capture")
    completer = LabelRecordingCompleter(
        outputs=[
            SubTopicFindingsDraft(findings=[_draft(read, _PAGE_SENTENCES[0], target_ids=("t1",))])
        ]
    )
    monkeypatch.setattr(
        stage_replay, "_settings",
        lambda config, override: load_config(config, overrides=json.loads(override)),
    )
    monkeypatch.setattr(providers, "build_chat_provider", lambda *args, **kwargs: completer)
    monkeypatch.setattr(TrackerClass, "from_config", classmethod(lambda cls, config: tracker))

    code = stage_replay.main(
        [
            "run", "--capture", str(tmp_path / "capture"), "--arm", "control",
            "--repetition", "1", "--override", "{}", "--stages", "page_extraction",
            "--out", str(tmp_path / "out"),
        ],
        now=lambda: datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc),
    )

    assert code == 0
    written = json.loads((tmp_path / "out" / "control-1.json").read_text("utf-8"))
    assert written["stages"] == ["page_extraction"]
    assert [result["stage"] for result in written["results"]] == ["page_extraction"]
    assert written["results"][0]["verdicts"] == {"page_extraction-001.json#t1": True}


def test_an_unknown_stage_is_refused_before_anything_runs(tmp_path: Path) -> None:
    from deep_research.experiments.stage_replay import main

    with pytest.raises(SystemExit) as refused:
        main(
            ["run", "--capture", str(tmp_path), "--arm", "control", "--repetition", "1",
             "--override", "{}", "--stages", "context_check,page_extractions",
             "--out", str(tmp_path / "out")],
            now=lambda: datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc),
        )

    assert refused.value.code == 2
    assert not (tmp_path / "out").exists()
