"""Does the provider's reasoning effort change how much the model reasons?

The probe asks one fixed multi-step arithmetic problem at each reasoning effort,
several times, as the evidence verifier with thinking forced on, and writes what
each call cost to ``<out>/effort-probe-<YYYYMMDDTHHMMSSZ>.json``:

    python -m deep_research.experiments.effort_probe --out output/effort-probe \\
        --efforts high,low --repetitions 3

Every call is paid, and the calls run one after another. The command refuses to
start when any minute of the next 90 falls inside DeepSeek's peak hours,
01:00-04:00 or 06:00-10:00 UTC, and then writes nothing.

Reading the result: every row is one call, with the ``reasoning_tokens`` and
``output_tokens`` the provider reported for it and the wall ``seconds`` it took.
Compare ``reasoning_tokens`` across efforts. When ``low`` reasons about as much
as ``high``, asking for ``low`` saves nothing on this model and an
``agents.operation_efforts`` entry would only change the request. A row with
``accepted: false`` is a call that failed, with the provider's message in
``error``; ``rejected: true`` marks the failures that say the request itself was
refused (the effort or thinking mode is not supported, as a configuration error
or an HTTP 400 or 422), which a retry would not change, and ``rejected: false``
marks a transient failure (a timeout, a rate limit, a 5xx, a bad key). The exit
code is 0 when every call was accepted, 1 when any effort was rejected, 3 when
nothing was rejected but some call failed, and 2 when the command declined to
start.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any

from deep_research.agents.base import StructuredCompleter
from deep_research.agents.prompts import STRUCTURED_REPLY_FORMAT
from deep_research.experiments.live_runs import peak_ahead
from deep_research.observability import RunTelemetryCollector, Tracker, call_label
from deep_research.providers import (
    ChatMessage,
    ProviderConfigurationError,
    ProviderError,
    ProviderResponseError,
    build_chat_provider,
)
from deep_research.utils.config import LLMConfig, load_config
from deep_research.utils.types import ContractModel

#: A probe is a few dozen sequential calls, so it looks further ahead than a
#: single run does.
PEAK_LOOKAHEAD_MINUTES = 90

#: The agent whose model, timeout and retry settings the probe's calls resolve.
PROBE_AGENT = "evidence_verifier"

#: The trace session every probe request is made inside.
PROBE_SESSION = "effort-probe"

#: The statuses that say the request itself was refused: a bad request, or one
#: the server could not process. Other 4xx statuses (a bad key, a rate limit)
#: say nothing about the effort.
_REJECTION_STATUSES = frozenset({400, 422})

_PROBLEM = (
    "A warehouse receives 14 pallets on Monday, each holding 36 crates. On Tuesday "
    "it receives 9 pallets of 48 crates. On Wednesday it ships out 211 crates, and "
    "then returns 40 percent of the crates that remain to the supplier. On Thursday "
    "it receives 3 more pallets of 25 crates. How many crates are in the warehouse "
    "at the end of Thursday? Work it out step by step."
)

PROBE_MESSAGES: tuple[ChatMessage, ...] = (
    ChatMessage(
        role="system",
        content="You solve arithmetic word problems. Check each step before you answer.",
    ),
    ChatMessage(
        role="user",
        content=f"{_PROBLEM}\n\n{STRUCTURED_REPLY_FORMAT} Put the number of crates in `value`.",
    ),
)


class ProbeAnswer(ContractModel):
    """The one integer the probe's problem asks for."""

    value: int


BuildProvider = Callable[[LLMConfig, RunTelemetryCollector], StructuredCompleter]


def _chat_provider_builder(tracker: Tracker) -> BuildProvider:
    def build(llm: LLMConfig, telemetry: RunTelemetryCollector) -> StructuredCompleter:
        return build_chat_provider(llm, tracker, telemetry=telemetry)

    return build


def _is_rejection(failure: ProviderError) -> bool:
    """Whether the provider refused the request itself, not just this attempt."""
    if isinstance(failure, ProviderConfigurationError):
        return True
    return (
        isinstance(failure, ProviderResponseError)
        and failure.http_status_code in _REJECTION_STATUSES
    )


def _reported_tokens(
    telemetry: RunTelemetryCollector, label: str
) -> tuple[int, int] | None:
    """Output and reasoning tokens of the calls recorded under ``label``, if any."""
    records = [
        record
        for stage in telemetry.snapshot().stages
        for record in stage.call_records
        if record.label == label
    ]
    if not records:
        return None
    return (
        sum(record.output_tokens for record in records),
        sum(record.reasoning_tokens for record in records),
    )


async def _probe(
    tracker: Tracker,
    provider: StructuredCompleter,
    telemetry: RunTelemetryCollector,
    efforts: Sequence[str],
    repetitions: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    # The provider opens an LLM span per request, and the tracker refuses a
    # child span with no session span around it.
    async with tracker.session_span(PROBE_SESSION, "effort probe"):
        for effort in efforts:
            for repetition in range(1, repetitions + 1):
                label = f"probe_{effort}_{repetition}"
                error: str | None = None
                rejected = False
                started = perf_counter()
                try:
                    with call_label(label):
                        await provider.complete_structured(
                            PROBE_MESSAGES,
                            ProbeAnswer,
                            agent_name=PROBE_AGENT,
                            reasoning_effort=effort,
                        )
                except ProviderError as failure:
                    error = str(failure)
                    rejected = _is_rejection(failure)
                seconds = perf_counter() - started
                tokens = _reported_tokens(telemetry, label)
                rows.append(
                    {
                        "effort": effort,
                        "repetition": repetition,
                        "accepted": error is None,
                        "rejected": rejected,
                        "error": error,
                        "reasoning_tokens": None if tokens is None else tokens[1],
                        "output_tokens": None if tokens is None else tokens[0],
                        "seconds": round(seconds, 3),
                    }
                )
    return rows


def main(
    argv: Sequence[str] | None = None,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    build_provider: BuildProvider | None = None,
) -> int:
    parser = argparse.ArgumentParser(prog="python -m deep_research.experiments.effort_probe")
    parser.add_argument("--out", required=True)
    parser.add_argument("--efforts", default="high,low", help="comma-separated reasoning efforts")
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--config", default="config.yaml")
    arguments = parser.parse_args(argv)

    started_at = now()
    if peak_ahead(started_at, minutes=PEAK_LOOKAHEAD_MINUTES):
        print(
            f"REFUSE: DeepSeek peak hours start within {PEAK_LOOKAHEAD_MINUTES} minutes; "
            "nothing was run."
        )
        return 2

    # Not strict: the probe needs the model settings only. A real provider names
    # the key it is missing when it is built.
    settings = load_config(arguments.config)
    llm = settings.llm.model_copy(update={"thinking_mode": "enabled"})
    tracker = Tracker.from_config(settings.langsmith)
    telemetry = RunTelemetryCollector()
    provider = (build_provider or _chat_provider_builder(tracker))(llm, telemetry)
    efforts = [effort.strip() for effort in arguments.efforts.split(",") if effort.strip()]
    rows = asyncio.run(_probe(tracker, provider, telemetry, efforts, arguments.repetitions))

    out = Path(arguments.out)
    out.mkdir(parents=True, exist_ok=True)
    result = out / f"effort-probe-{started_at.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    result.write_text(
        json.dumps({"model": llm.model, "rows": rows}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    print(result)
    if any(row["rejected"] for row in rows):
        return 1
    return 0 if all(row["accepted"] for row in rows) else 3


if __name__ == "__main__":
    sys.exit(main())
