"""The effort probe asks one fixed question at each reasoning effort."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from deep_research.experiments.effort_probe import ProbeAnswer, main
from deep_research.observability import RunTelemetryCollector, current_trace_context
from deep_research.providers import (
    ProviderConfigurationError,
    ProviderError,
    ProviderResponseError,
)
from deep_research.providers import deepseek_provider as deepseek_module
from deep_research.utils.config import LLMConfig, load_config
from tests.test_deepseek_provider import (
    FakeDeepSeekClient,
    RecordingResponses,
    responses_response,
)


@pytest.fixture(autouse=True)
def _no_langsmith_tracing(monkeypatch: pytest.MonkeyPatch) -> None:
    """The probe builds a tracker from the config; a test never traces."""
    monkeypatch.setenv("LANGSMITH_TRACING", "false")


def _offpeak() -> datetime:
    return datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)


def _never_called(llm: LLMConfig, telemetry: RunTelemetryCollector) -> Any:
    raise AssertionError("no provider may be built inside the peak look-ahead")


class _FakeProvider:
    """Answers like a provider that reports reasoning tokens through telemetry."""

    def __init__(
        self,
        llm: LLMConfig,
        telemetry: RunTelemetryCollector,
        *,
        tokens: dict[str, int],
        reject: dict[str, ProviderError],
    ) -> None:
        self.llm = llm
        self.requests: list[dict[str, Any]] = []
        self._telemetry = telemetry
        self._tokens = tokens
        self._reject = reject

    async def complete_structured(
        self,
        messages: Any,
        schema: type,
        *,
        agent_name: str | None = None,
        max_tokens: int | None = None,
        reasoning_effort: str | None = None,
    ) -> Any:
        # The real provider opens an LLM span, which needs a session span around it.
        if current_trace_context() is None:
            raise RuntimeError("child spans require an active session span")
        self.requests.append(
            {"schema": schema, "agent_name": agent_name, "reasoning_effort": reasoning_effort}
        )
        if reasoning_effort in self._reject:
            raise self._reject[reasoning_effort]
        reasoning = self._tokens.get(reasoning_effort or "", 0)
        self._telemetry.record_call(
            agent=agent_name,
            operation="structured_output",
            seconds=0.2,
            output_tokens=reasoning + 30,
            configured_cap=393216,
            truncated=False,
            reasoning_tokens=reasoning,
        )
        return schema(value=510)


def _fake(
    *,
    tokens: dict[str, int] | None = None,
    reject: set[str] | None = None,
    error: Callable[[str], ProviderError] = lambda effort: ProviderConfigurationError(
        f"reasoning_effort '{effort}' is not supported"
    ),
    built: list[_FakeProvider] | None = None,
) -> Callable[[LLMConfig, RunTelemetryCollector], _FakeProvider]:
    def build(llm: LLMConfig, telemetry: RunTelemetryCollector) -> _FakeProvider:
        provider = _FakeProvider(
            llm,
            telemetry,
            tokens=tokens or {},
            reject={effort: error(effort) for effort in reject or ()},
        )
        if built is not None:
            built.append(provider)
        return provider

    return build


def _only_json(directory: Path) -> dict[str, Any]:
    (path,) = directory.glob("effort-probe-*.json")
    return json.loads(path.read_text(encoding="utf-8"))


def test_the_probe_refuses_inside_the_peak_lookahead_and_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(
        ["--out", str(tmp_path / "p")],
        now=lambda: datetime(2026, 11, 2, 0, 0, tzinfo=timezone.utc),
        build_provider=_never_called,
    )

    assert code == 2 and not (tmp_path / "p").exists()
    assert "90 minutes" in capsys.readouterr().out


def test_the_probe_records_each_effort_and_repetition_with_its_reasoning_tokens(
    tmp_path: Path,
) -> None:
    code = main(
        ["--out", str(tmp_path), "--repetitions", "2"],
        now=_offpeak,
        build_provider=_fake(tokens={"high": 400, "low": 150}),
    )

    rows = _only_json(tmp_path)["rows"]
    assert code == 0
    assert [(r["effort"], r["repetition"], r["reasoning_tokens"]) for r in rows] == [
        ("high", 1, 400), ("high", 2, 400), ("low", 1, 150), ("low", 2, 150)]
    assert all(r["accepted"] is True and r["rejected"] is False and r["error"] is None for r in rows)
    assert [r["output_tokens"] for r in rows] == [430, 430, 180, 180]
    assert all(isinstance(r["seconds"], float) and r["seconds"] >= 0 for r in rows)


def test_a_rejected_effort_is_recorded_not_raised(tmp_path: Path) -> None:
    code = main(
        ["--out", str(tmp_path), "--repetitions", "1"],
        now=_offpeak,
        build_provider=_fake(reject={"low"}),
    )

    rows = {r["effort"]: r for r in _only_json(tmp_path)["rows"]}
    low = rows["low"]
    assert code == 1 and low["accepted"] is False and low["rejected"] is True
    assert "low" in low["error"]
    assert low["reasoning_tokens"] is None and low["output_tokens"] is None
    assert rows["high"]["accepted"] is True and rows["high"]["rejected"] is False


def test_a_failure_that_is_not_a_rejection_is_recorded_and_exits_with_its_own_code(
    tmp_path: Path,
) -> None:
    code = main(
        ["--out", str(tmp_path), "--repetitions", "1", "--efforts", "high"],
        now=_offpeak,
        build_provider=_fake(
            reject={"high"}, error=lambda effort: ProviderError(f"{effort}: upstream timed out")
        ),
    )

    (row,) = _only_json(tmp_path)["rows"]
    assert row["accepted"] is False and row["rejected"] is False
    assert row["error"] == "high: upstream timed out"
    assert code == 3


@pytest.mark.parametrize(
    ("status", "retryable", "rejected", "code"),
    [(400, False, True, 1), (422, False, True, 1), (503, True, False, 3), (401, False, False, 3)],
)
def test_an_http_status_rejects_the_effort_only_when_the_request_itself_was_refused(
    tmp_path: Path, status: int, retryable: bool, rejected: bool, code: int
) -> None:
    def failure(effort: str) -> ProviderError:
        return ProviderResponseError(
            f"DeepSeek request failed with status {status}",
            failure_origin="sdk",
            retryable=retryable,
            failure_category="http",
            http_status_code=status,
        )

    exit_code = main(
        ["--out", str(tmp_path), "--repetitions", "1", "--efforts", "low"],
        now=_offpeak,
        build_provider=_fake(reject={"low"}, error=failure),
    )

    (row,) = _only_json(tmp_path)["rows"]
    assert row["accepted"] is False and row["rejected"] is rejected
    assert exit_code == code


def test_a_rejection_wins_over_another_failure_in_the_exit_code(tmp_path: Path) -> None:
    def failure(effort: str) -> ProviderError:
        if effort == "low":
            return ProviderConfigurationError("reasoning_effort 'low' is not supported")
        return ProviderError("upstream timed out")

    code = main(
        ["--out", str(tmp_path), "--repetitions", "1"],
        now=_offpeak,
        build_provider=_fake(reject={"high", "low"}, error=failure),
    )

    assert [(r["effort"], r["rejected"]) for r in _only_json(tmp_path)["rows"]] == [
        ("high", False), ("low", True)]
    assert code == 1


def test_the_probe_asks_the_fixed_question_as_the_evidence_verifier_at_each_effort(
    tmp_path: Path,
) -> None:
    built: list[_FakeProvider] = []

    main(
        ["--out", str(tmp_path), "--repetitions", "1", "--efforts", "max,high"],
        now=_offpeak,
        build_provider=_fake(built=built),
    )

    (provider,) = built
    assert provider.requests == [
        {"schema": ProbeAnswer, "agent_name": "evidence_verifier", "reasoning_effort": "max"},
        {"schema": ProbeAnswer, "agent_name": "evidence_verifier", "reasoning_effort": "high"},
    ]


def test_the_probe_forces_thinking_on_whatever_the_config_says(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text("llm:\n  model: deepseek-flash\n  thinking_mode: disabled\n", encoding="utf-8")
    assert load_config(str(config)).llm.thinking_mode == "disabled"
    built: list[_FakeProvider] = []

    main(
        ["--out", str(tmp_path / "out"), "--repetitions", "1", "--config", str(config)],
        now=_offpeak,
        build_provider=_fake(built=built),
    )

    assert built[0].llm.thinking_mode == "enabled"


def test_the_result_is_stamped_with_the_start_time_and_names_the_model(tmp_path: Path) -> None:
    main(
        ["--out", str(tmp_path), "--repetitions", "1"],
        now=_offpeak,
        build_provider=_fake(),
    )

    (path,) = tmp_path.glob("effort-probe-*.json")
    assert path.name == "effort-probe-20261102T120000Z.json"
    assert json.loads(path.read_text(encoding="utf-8"))["model"] == load_config("config.yaml").llm.model


def test_the_configured_provider_is_driven_inside_a_tracker_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default path: the real DeepSeek adapter over a stubbed SDK client."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "not-a-real-key")
    reply = responses_response(output_text=json.dumps({"value": 510}), output_tokens=430)
    reply.usage.output_tokens_details = SimpleNamespace(reasoning_tokens=400)
    responses = RecordingResponses(reply, reply)
    monkeypatch.setattr(
        deepseek_module,
        "_build_client",
        lambda config, *, api_key, client: FakeDeepSeekClient(responses=responses),
    )

    code = main(["--out", str(tmp_path), "--repetitions", "1"], now=_offpeak)

    rows = _only_json(tmp_path)["rows"]
    assert code == 0
    assert [(r["effort"], r["reasoning_tokens"], r["output_tokens"]) for r in rows] == [
        ("high", 400, 430), ("low", 400, 430)]
    assert [call["reasoning"] for call in responses.calls] == [
        {"effort": "high"}, {"effort": "low"}]
