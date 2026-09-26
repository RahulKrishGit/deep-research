"""Tests for the §7.3 run telemetry: the collector and its two renderings."""

from __future__ import annotations

import pytest

from deep_research.observability import (
    RunTelemetryCollector,
    render_telemetry_advice,
    render_telemetry_line,
)
from deep_research.observability.run_telemetry import cap_key_for
from deep_research.request_budget import (
    RequestBudgetEventKind,
    RequestBudgetSnapshot,
    RequestBudgetUpdate,
)
from deep_research.utils.types import (
    OperationTelemetry,
    RunTelemetry,
    StageTelemetry,
)


def _update(
    kind: RequestBudgetEventKind, *, provider: str = "deepseek", attempts: int = 1
) -> RequestBudgetUpdate:
    """One budget update, shaped exactly as ``RequestBudget`` publishes it."""
    return RequestBudgetUpdate(
        kind=kind,
        snapshot=RequestBudgetSnapshot(
            provider=provider,
            attempts=attempts,
            ceiling=None,
            effective_limit=None,
            input_tokens=0,
            output_tokens=0,
        ),
    )


def test_rate_limits_are_counted_and_recovered() -> None:
    """Three transient rate-limit failures, two retried to success, one that
    exhausted the ladder: 3 errors, 2 recovered."""
    collector = RunTelemetryCollector()

    for _ in range(2):
        # One call that hit a 429 and came back on the retry.
        collector.note_rate_limit()
        collector.note_rate_limit_recovered()
    # One call whose 429 ended it: counted, never marked recovered.
    collector.note_rate_limit()

    snapshot = collector.snapshot()
    assert snapshot.rate_limit_errors == 3
    assert snapshot.rate_limit_recovered == 2


def test_peak_in_flight_is_the_high_water_mark() -> None:
    """Reservations and completions interleaved: the peak is 8, not the final 0,
    and it names the agent whose calls set it."""
    collector = RunTelemetryCollector()

    for _ in range(3):
        collector.note_call_starting("researcher")
        collector.observe_budget(_update("attempt_reserved"))
    collector.observe_budget(_update("tokens_reported"))
    for _ in range(6):
        collector.note_call_starting("evidence_verifier")
        collector.observe_budget(_update("attempt_reserved"))

    peaked = collector.snapshot()
    assert peaked.peak_calls_in_flight == 8
    assert peaked.peak_agent == "evidence_verifier"

    # The eighth reservation is the high-water mark; draining the gauge to
    # zero (the state a "current calls" reading would report) leaves it alone.
    for _ in range(8):
        collector.observe_budget(_update("tokens_reported"))
    assert collector.snapshot().peak_calls_in_flight == 8


def test_a_refused_attempt_changes_nothing() -> None:
    """A refusal never reserved, so it must not enter the gauge."""
    collector = RunTelemetryCollector()
    collector.note_call_starting("researcher")
    collector.observe_budget(_update("attempt_reserved"))
    collector.observe_budget(_update("attempt_blocked"))
    collector.observe_budget(_update("attempt_reserved"))
    collector.observe_budget(_update("attempt_blocked"))

    assert collector.snapshot().peak_calls_in_flight == 2


def test_a_search_attempt_is_not_a_model_call_in_flight() -> None:
    """Tavily reserves against the run's budget but reports no tokens.

    That leaves a search reservation with nothing that could ever release it:
    counting it would hold the gauge one call high per search, and the peak —
    the figure an operator lowers a concurrency cap from — would read high by
    every search the run made.
    """
    collector = RunTelemetryCollector()
    collector.note_call_starting("researcher")
    for _ in range(4):
        collector.observe_budget(_update("attempt_reserved", provider="tavily"))
    for _ in range(4):
        collector.observe_budget(_update("tokens_reported", provider="tavily"))
    collector.observe_budget(_update("attempt_reserved", provider="deepseek"))

    peaked = collector.snapshot()
    assert peaked.peak_calls_in_flight == 1
    assert peaked.peak_agent == "researcher"


def test_a_failed_attempt_releases_its_slot() -> None:
    """Every reserved attempt is released, or a leak would stack up: three
    failed attempts that leaked would read as six calls in flight."""
    collector = RunTelemetryCollector()

    for _ in range(3):
        collector.note_call_starting("researcher")
        collector.observe_budget(_update("attempt_reserved"))
        collector.note_attempt_finished()
    for _ in range(3):
        collector.observe_budget(_update("attempt_reserved"))

    assert collector.snapshot().peak_calls_in_flight == 3


def test_a_stage_aggregates_calls_seconds_and_slowest() -> None:
    """Three calls of 2 s, 9 s and 4 s: calls=3, seconds=15.0, slowest=9.0."""
    collector = RunTelemetryCollector()
    for seconds in (2.0, 9.0, 4.0):
        collector.record_call(
            agent="report_writer",
            operation="structured_output",
            seconds=seconds,
            output_tokens=1_000,
            configured_cap=65_536,
            truncated=False,
        )

    [stage] = collector.snapshot().stages
    assert stage.agent == "report_writer"
    assert stage.calls == 3
    assert stage.seconds == 15.0
    assert stage.slowest_seconds == 9.0


def test_output_tokens_are_reported_against_the_cap() -> None:
    """The operation's maximum output tokens are compared with that call's
    `configured_max_tokens`, and `finish_reason_category == "length"` counts
    as a truncation."""
    collector = RunTelemetryCollector()
    for output_tokens, truncated in ((61_200, False), (65_536, True)):
        collector.record_call(
            agent="report_reviewer",
            operation="structured_output",
            seconds=1.0,
            output_tokens=output_tokens,
            configured_cap=65_536,
            truncated=truncated,
        )

    [stage] = collector.snapshot().stages
    [operation] = stage.operations
    assert operation.agent == "report_reviewer"
    assert operation.max_output_tokens == 65_536
    assert operation.configured_cap == 65_536
    assert operation.truncations == 1
    assert operation.cap_key == "agents.report_review_max_tokens"


@pytest.mark.parametrize(
    ("agent", "operation", "expected"),
    [
        ("planner", "structured_output", "agents.planner_final_max_tokens"),
        ("report_reviewer", "structured_output", "agents.report_review_max_tokens"),
        ("researcher", "react_tool_turn", "agents.react_decision_max_tokens"),
        ("report_writer", "structured_output", "llm.max_tokens"),
        ("evidence_verifier", "chat", "llm.max_tokens"),
        (None, "structured_output", "llm.max_tokens"),
    ],
)
def test_the_cap_key_names_the_key_that_bounds_the_call(
    agent: str | None, operation: str, expected: str
) -> None:
    assert cap_key_for(agent, operation) == expected


def test_the_line_renders_the_four_parts_of_the_spec() -> None:
    """The plan's own example line, part for part."""
    telemetry = RunTelemetry(
        rate_limit_errors=3,
        rate_limit_recovered=2,
        peak_calls_in_flight=8,
        peak_agent="evidence_verifier",
        stages=(
            StageTelemetry(
                agent="report_reviewer",
                calls=2,
                seconds=223.4,
                slowest_seconds=223.4,
            ),
            StageTelemetry(
                agent="report_writer",
                calls=2,
                seconds=30.0,
                slowest_seconds=20.0,
                operations=(
                    OperationTelemetry(
                        agent="report_writer",
                        max_output_tokens=61_200,
                        configured_cap=65_536,
                        cap_key="llm.max_tokens",
                    ),
                ),
            ),
        ),
    )

    assert render_telemetry_line(telemetry) == (
        "Telemetry: peak 8 provider calls in flight (evidence_verifier); "
        "3 rate limits (2 recovered); "
        "slowest call report_reviewer 223.4 s; "
        "report_writer output 61,200 of 65,536 tokens (93% of its cap); "
        "0 truncated"
    )


def test_the_advice_names_the_knob_and_the_cap() -> None:
    """N × 429 prints "rate limits hit N times; consider lowering
    agents.verifier_concurrency" (the knob of the agent at the peak); a call at
    93% of its cap prints "output within 93% of the report_writer cap
    (llm.max_tokens); consider raising it" — the key, because one agent can
    hold several caps and only the key says which one to raise; with neither,
    no advice line is added."""
    rate_limited = RunTelemetry(
        rate_limit_errors=3,
        rate_limit_recovered=1,
        peak_calls_in_flight=8,
        peak_agent="evidence_verifier",
    )
    assert render_telemetry_advice(rate_limited) == (
        "rate limits hit 3 times; consider lowering agents.verifier_concurrency",
    )

    near_cap = RunTelemetry(
        peak_calls_in_flight=2,
        peak_agent="evidence_verifier",
        stages=(
            StageTelemetry(
                agent="report_writer",
                calls=1,
                seconds=20.0,
                slowest_seconds=20.0,
                operations=(
                    OperationTelemetry(
                        agent="report_writer",
                        max_output_tokens=61_200,
                        configured_cap=65_536,
                        cap_key="llm.max_tokens",
                    ),
                ),
            ),
        ),
    )
    assert render_telemetry_advice(near_cap) == (
        "output within 93% of the report_writer cap (llm.max_tokens); "
        "consider raising it",
    )

    assert render_telemetry_advice(RunTelemetry()) == ()


def test_the_cap_advice_names_the_key_of_the_operation_that_is_full() -> None:
    """One agent can hold two caps, so the key is what makes the line usable.

    A researcher at 94 % of its ReAct decision cap needs
    ``agents.react_decision_max_tokens`` raised; raising that same agent's
    other cap (``llm.max_tokens``, which bounds its structured calls) would do
    nothing for the call that hit its ceiling.
    """
    collector = RunTelemetryCollector()
    collector.record_call(
        agent="researcher",
        operation="react_tool_turn",
        seconds=19.0,
        output_tokens=31_000,
        configured_cap=32_768,
        truncated=False,
    )
    collector.record_call(
        agent="researcher",
        operation="structured_output",
        seconds=4.0,
        output_tokens=100,
        configured_cap=32_768,
        truncated=False,
    )

    assert render_telemetry_advice(collector.snapshot()) == (
        "output within 94% of the researcher cap "
        "(agents.react_decision_max_tokens); consider raising it",
    )


@pytest.mark.parametrize(
    ("agent", "expected"),
    [
        ("evidence_verifier", "agents.verifier_concurrency"),
        ("researcher", "agents.sub_topic_concurrency"),
        ("source_evaluator", "agents.source_scoring_concurrency"),
        ("report_writer", "agents.writer_section_concurrency"),
    ],
)
def test_the_advised_knob_belongs_to_the_agent_at_the_peak(
    agent: str, expected: str
) -> None:
    """The four concurrency caps of spec §7.3 and §6.10; any other agent at
    the peak falls back to the researcher's, the cap that bounds the widest
    fan-out."""
    telemetry = RunTelemetry(
        rate_limit_errors=1, peak_calls_in_flight=4, peak_agent=agent
    )
    assert render_telemetry_advice(telemetry) == (
        f"rate limits hit 1 times; consider lowering {expected}",
    )


def test_the_snapshot_carries_the_four_groups_of_the_spec() -> None:
    """All four §7.3 groups reach the record, not just the arithmetic."""
    collector = RunTelemetryCollector()
    collector.note_call_starting("researcher")
    collector.observe_budget(_update("attempt_reserved"))
    collector.note_attempt_finished()
    collector.note_rate_limit()
    collector.observe_budget(_update("tokens_reported"))
    collector.record_call(
        agent="researcher",
        operation="react_tool_turn",
        seconds=18.5,
        output_tokens=31_000,
        configured_cap=32_768,
        truncated=False,
    )

    telemetry = collector.snapshot()
    assert telemetry.rate_limit_errors == 1
    assert telemetry.rate_limit_recovered == 0
    assert telemetry.peak_calls_in_flight == 1
    assert telemetry.peak_agent == "researcher"
    [stage] = telemetry.stages
    assert (stage.agent, stage.calls, stage.seconds, stage.slowest_seconds) == (
        "researcher",
        1,
        18.5,
        18.5,
    )
    [operation] = stage.operations
    assert operation.cap_key == "agents.react_decision_max_tokens"


def test_the_line_reports_cache_hits_when_input_tokens_were_reported() -> None:
    collector = RunTelemetryCollector()
    for input_tokens, cached in ((4_000, 3_000), (6_000, 0)):
        collector.record_call(
            agent="evidence_verifier", operation="structured_output", seconds=2.0,
            output_tokens=100, configured_cap=32_768, truncated=False,
            input_tokens=input_tokens, cached_input_tokens=cached,
        )
    telemetry = collector.snapshot()
    assert (telemetry.input_tokens, telemetry.cached_input_tokens) == (10_000, 3_000)
    assert render_telemetry_line(telemetry).endswith(
        "0 truncated; cache hits 3,000 of 10,000 input tokens (30%)"
    )
