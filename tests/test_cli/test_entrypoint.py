"""Tests for the CLI entry point: wiring, prompts, and exit codes."""

from __future__ import annotations

import io
import threading

import pytest
import yaml

from deep_research.cli import (
    EXIT_CONFIGURATION_ERROR,
    EXIT_GRAPH_FAILED,
    EXIT_INTERRUPTED,
    EXIT_OK,
    EXIT_QUALITY_UNACCEPTED,
    ProgressStream,
    build_parser,
    main,
    render_progress,
)
from deep_research.graph.events import (
    node_completed_event,
    node_started_event,
    session_completed_event,
    session_started_event,
)
from deep_research.main import run_research_sync
from deep_research.observability import TokenUsage
from deep_research.request_budget import (
    RequestBudgetSnapshot,
    RequestBudgetUpdate,
)
from deep_research.runtime.errors import configuration_error
from deep_research.runtime.outcome import ResearchOutcome
from deep_research.utils.types import (
    Critique,
    ReportQualitySnapshot,
    ResearchError,
    ResearchEvent,
    ResearchState,
)

QUESTION = "How mature is quantum error correction?"


def outcome(status: str = "completed", **overrides) -> ResearchOutcome:
    state = overrides.pop("state", None) or ResearchState(
        session_id="session-1", original_question=QUESTION
    )
    defaults = {
        "session_id": "session-1",
        "question": QUESTION,
        "status": status,
        "state": state,
        "trace_url": None,
        "report_path": "report-session-1-0.md",
        "token_usage": TokenUsage(),
        "tool_calls": (),
    }
    defaults.update(overrides)
    return ResearchOutcome(**defaults)


def accepted_state() -> ResearchState:
    """A pass the gates cleared and the Critic accepted."""
    return ResearchState(
        session_id="session-1",
        original_question=QUESTION,
        quality=ReportQualitySnapshot(
            coverage_ratio=1.0,
            planned_topics=2,
            covered_topics=2,
            unique_findings=1,
            unique_sources=2,
            cited_sources=2,
            scored_cited_source_ratio=1.0,
            verified_claims=1,
            contradicted_claims=0,
            duplicate_claims=0,
            duplicate_source_rows=0,
            uncited_settled_points=0,
        ),
        critique=Critique(
            score=8,
            gaps=[],
            unsupported_claims=[],
            recommended_queries=[],
            should_continue=False,
            rationale="Recorded for entry-point tests.",
        ),
    )


class RecordingRunner:
    """Capture the keyword arguments the CLI hands to run_research_sync."""

    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.result = result if result is not None else outcome()
        self.error = error
        self.calls: list[dict[str, object]] = []

    def __call__(self, **kwargs: object) -> ResearchOutcome:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.result


class StreamingRunner:
    """Stream events through the CLI's own handler, then return the outcome.

    A real run delivers each event to the handler while the graph produces it,
    and returns a state that carries the same records. Handing back that state
    is what proves the summary does not print them a second time.
    """

    def __init__(
        self, events: list[ResearchEvent], result: ResearchOutcome | None = None
    ) -> None:
        self.events = events
        self.result = result if result is not None else outcome(
            state=ResearchState(
                session_id="session-1",
                original_question=QUESTION,
                events=list(events),
            )
        )
        self.handlers: list[object] = []

    def __call__(self, **kwargs: object) -> ResearchOutcome:
        handler = kwargs["event_handler"]
        self.handlers.append(handler)
        for event in self.events:
            handler(event)
        return self.result


def test_a_successful_run_exits_zero_and_prints_the_report_path() -> None:
    runner = RecordingRunner()
    stream = io.StringIO()

    code = main([QUESTION], runner=runner, stream=stream)

    assert code == EXIT_OK
    printed = stream.getvalue()
    assert "Session ID: session-1" in printed
    assert "Report: report-session-1-0.md" in printed


def test_the_cli_passes_every_option_through_to_run_research() -> None:
    runner = RecordingRunner()

    main(
        [
            QUESTION,
            "--max-iterations",
            "5",
            "--output-format",
            "markdown",
            "--config",
            "custom.yaml",
            "--verbose",
        ],
        runner=runner,
        stream=io.StringIO(),
    )

    call = dict(runner.calls[0])
    assert isinstance(call.pop("event_handler"), ProgressStream)
    assert call.pop("request_budget_handler", None) is not None
    assert call == {
        "question": QUESTION,
        "resume_session_id": None,
        "config_path": "custom.yaml",
        "max_iterations": 5,
        "output_format": "markdown",
        "config_overrides": None,
    }


def test_progress_streams_while_the_run_happens_and_is_never_reprinted() -> None:
    """Step 6: the handler runs before the runner returns, and once only."""
    events = [
        session_started_event(
            session_id="session-1", max_iterations=2, checkpointing=False
        ),
        node_started_event("planner", iteration=0),
        session_completed_event(
            status="completed", iteration=0, error_count=0, has_report=True
        ),
    ]
    runner = StreamingRunner(events)
    stream = io.StringIO()

    code = main([QUESTION], runner=runner, stream=stream)

    printed = stream.getvalue()
    assert code == EXIT_OK
    assert len(runner.handlers) == 1
    for event in events:
        assert printed.count(event.message) == 1
    streamed_at = [printed.index(event.message) for event in events]
    assert streamed_at == sorted(streamed_at)
    assert printed.index("Research session started.") < printed.index(
        "Session ID: session-1"
    )
    assert printed.index("Node planner started.") < printed.index(
        "Session ID: session-1"
    )
    assert "Progress log:" not in printed


def test_every_streamed_progress_line_is_flushed_immediately() -> None:
    """Step 6: "live" progress must survive a redirected, block-buffered stream.

    ``main`` hands ``ProgressStream`` ``sys.stdout``, which is line-buffered
    only on a TTY. Redirected to a file or a pipe it is block-buffered at
    8 KiB, and a whole run emits far less than that, so without an explicit
    flush every "live" line appears at process exit — exactly the
    non-interactive case (``> run.log``, ``| tee``) where the user has no
    other signal the run is alive. The existing tests cannot see this because
    they inject an unbuffered ``io.StringIO``.
    """
    events = [
        session_started_event(
            session_id="session-1", max_iterations=2, checkpointing=False
        ),
        node_started_event("planner", iteration=0),
        session_completed_event(
            status="completed", iteration=0, error_count=0, has_report=True
        ),
    ]
    runner = StreamingRunner(events)
    stream = FlushCountingStream()
    assert len(events) == 3

    code = main([QUESTION], runner=runner, stream=stream)

    assert code == EXIT_OK
    assert stream.flushes >= len(events)
    for event in events:
        line = render_progress(event, verbose=False)
        assert line is not None
        assert line in stream.flushed_lines


class FlushCountingStream(io.StringIO):
    """A ``StringIO`` that counts flushes and records which lines they covered.

    ``flushed_lines`` holds only the lines written *before* a ``flush`` call,
    so the assertion cannot be satisfied by one flush at the end of the run:
    each streamed record has to have been flushed by the time the run moves on.
    Lines the summary writes without a flush stay out of the list.
    """

    def __init__(self) -> None:
        super().__init__()
        self.flushes = 0
        self.flushed_lines: list[str] = []
        self._pending: list[str] = []

    def write(self, value: str) -> int:
        written = super().write(value)
        if value.strip():
            self._pending.append(value.rstrip("\n"))
        return written

    def flush(self) -> None:
        self.flushes += 1
        self.flushed_lines.extend(self._pending)
        self._pending.clear()
        super().flush()


def test_verbose_streams_agent_completions_live() -> None:
    events = [
        node_started_event("planner", iteration=0),
        node_completed_event(
            "planner", iteration=0, event_count=2, error_count=0
        ),
    ]
    runner = StreamingRunner(events)
    stream = io.StringIO()

    main([QUESTION, "--verbose"], runner=runner, stream=stream)

    printed = stream.getvalue()
    assert printed.count("Node planner completed.") == 1
    assert printed.index("Node planner completed.") < printed.index(
        "Session ID: session-1"
    )


def test_plain_progress_does_not_stream_agent_completions() -> None:
    events = [
        node_completed_event(
            "planner", iteration=0, event_count=2, error_count=0
        )
    ]
    runner = StreamingRunner(events)
    stream = io.StringIO()

    main([QUESTION], runner=runner, stream=stream)

    assert "Node planner completed." not in stream.getvalue()


def test_the_numeric_exit_code_contract_is_pinned() -> None:
    """The numbers automation and the docs depend on, asserted literally.

    Every other test in this file compares against the constants themselves,
    and the help test compares the help text to itself, so swapping two
    constants — or renumbering one — kept the whole suite green while breaking
    every caller that reads an exit status. Only ``2`` (argparse's usage error)
    was pinned literally anywhere.
    """
    assert (
        EXIT_OK,
        EXIT_CONFIGURATION_ERROR,
        EXIT_GRAPH_FAILED,
        EXIT_QUALITY_UNACCEPTED,
        EXIT_INTERRUPTED,
    ) == (0, 1, 3, 4, 130)


def test_the_help_documents_every_exit_code() -> None:
    """Step 5: the codes live in the CLI's own help, not only in a test."""
    help_text = build_parser().format_help()

    assert "0  the run finished" in help_text
    assert "1  configuration error" in help_text
    assert "2  usage error" in help_text
    assert "3  the graph failed" in help_text
    assert (
        "4  --require-quality was set and the report was not accepted"
        in help_text
    )
    assert "130  interrupted" in help_text


def test_interactive_mode_prompts_once_and_runs_the_answer() -> None:
    runner = RecordingRunner()
    prompts: list[str] = []

    def prompt(message: str) -> str:
        prompts.append(message)
        return f"  {QUESTION}  "

    code = main(
        ["--interactive"], runner=runner, prompt=prompt, stream=io.StringIO()
    )

    assert code == EXIT_OK
    assert len(prompts) == 1
    assert runner.calls[0]["question"] == QUESTION


def test_an_empty_interactive_answer_is_a_configuration_failure() -> None:
    runner = RecordingRunner()
    stream = io.StringIO()

    code = main(
        ["--interactive"],
        runner=runner,
        prompt=lambda _message: "   ",
        stream=stream,
    )

    assert code == EXIT_CONFIGURATION_ERROR
    assert runner.calls == []
    assert "error:" in stream.getvalue()


def test_a_whitespace_only_question_is_a_configuration_failure() -> None:
    """A blank positional question never claims a run started."""
    runner = RecordingRunner()
    stream = io.StringIO()

    code = main(["   "], runner=runner, stream=stream)

    assert code == EXIT_CONFIGURATION_ERROR
    assert runner.calls == []
    printed = stream.getvalue()
    assert "error:" in printed
    assert "Preparing" not in printed


def test_positional_question_whitespace_is_normalized() -> None:
    runner = RecordingRunner()

    main(["  AI in healthcare  "], runner=runner, stream=io.StringIO())

    assert runner.calls[0]["question"] == "AI in healthcare"


def test_interactive_eof_is_a_configuration_failure() -> None:
    runner = RecordingRunner()

    def prompt(_message: str) -> str:
        raise EOFError

    code = main(
        ["--interactive"], runner=runner, prompt=prompt, stream=io.StringIO()
    )

    assert code == EXIT_CONFIGURATION_ERROR
    assert runner.calls == []


def test_a_blank_resume_session_id_is_rejected_end_to_end(
    tmp_path, monkeypatch
) -> None:
    """The CLI hands --resume to run_research, which rejects a blank id."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek-key")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setenv("TAVILY_API_KEY", "test-tavily-key")
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump({}), encoding="utf-8")

    async def builder(settings, *, session_id, **_ignored):
        raise AssertionError("a blank resume must fail before runtime setup")

    def runner(**kwargs):
        return run_research_sync(runtime_builder=builder, **kwargs)

    stream = io.StringIO()
    code = main(["--resume", "   "], runner=runner, stream=stream)

    assert code == EXIT_CONFIGURATION_ERROR
    printed = stream.getvalue()
    assert "error:" in printed
    assert "session id" in printed


def test_resume_passes_the_session_id_and_no_question() -> None:
    runner = RecordingRunner()

    main(["--resume", "session-1"], runner=runner, stream=io.StringIO())

    assert runner.calls[0]["question"] is None
    assert runner.calls[0]["resume_session_id"] == "session-1"


def test_the_entrypoint_starts_the_session_with_planning_recall(
    tmp_path, monkeypatch
) -> None:
    """Startup recall runs with ``purpose="planning"``, through the real
    entry point.

    That recall is the planner's single procedural lookup, and it must not
    read long-term findings: remembered prose handed to the planner becomes a
    settled premise in the plan. The call is inspected where ``run_research``
    actually makes it, not only where ``recall_memory_context`` is defined.
    """
    import asyncio

    from deep_research import main as main_module
    from deep_research.graph.orchestrator import compile_research_graph
    from deep_research.main import run_research
    from deep_research.observability import LangSmithRuntimeConfig, Tracker
    from deep_research.request_budget import RequestBudget
    from deep_research.runtime.assembly import ResearchRuntime
    from deep_research.utils.types import MemorySnapshot
    from tests.graph_fakes import fake_research_agents

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek-key")
    monkeypatch.setenv("TAVILY_API_KEY", "test-tavily-key")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    config = tmp_path / "config.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "graph": {"max_iterations": 1},
                "output": {"directory": str(tmp_path / "output")},
                "memory": {
                    "long_term": {"persist_directory": str(tmp_path / "memory")},
                    "procedural": {
                        "strategies_path": str(tmp_path / "strategies.json")
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    recalled: list[dict] = []

    async def recording_recall(**kwargs):
        recalled.append(kwargs)
        return MemorySnapshot()

    monkeypatch.setattr(main_module, "recall_memory_context", recording_recall)

    async def builder(settings, *, session_id, **_ignored):
        return ResearchRuntime(
            session_id=session_id,
            settings=settings,
            tracker=Tracker(
                LangSmithRuntimeConfig(
                    tracing_enabled=False,
                    project="entrypoint-tests",
                    api_key=None,
                )
            ),
            request_budget=RequestBudget(),
            graph=compile_research_graph(fake_research_agents()),
            long_term=None,
            procedural=None,
        )

    asyncio.run(
        run_research(
            QUESTION,
            config_path=str(config),
            runtime_builder=builder,
        )
    )

    assert len(recalled) == 1
    assert recalled[0]["purpose"] == "planning"
    assert recalled[0]["question"] == QUESTION


def test_a_configuration_failure_prints_its_hint_and_exits_one() -> None:
    runner = RecordingRunner(
        error=configuration_error(
            reason="missing_secrets",
            message="Missing required environment variables: OPENAI_API_KEY",
        )
    )
    stream = io.StringIO()

    code = main([QUESTION], runner=runner, stream=stream)

    assert code == EXIT_CONFIGURATION_ERROR
    printed = stream.getvalue()
    assert "error: Missing required environment variables" in printed
    assert "hint: Set the selected chat provider's API key" in printed
    assert "Traceback" not in printed


def test_an_unresumable_session_exits_one_with_the_known_limitation() -> None:
    runner = RecordingRunner(
        error=configuration_error(
            reason="no_checkpoint",
            message="Session session-1 cannot be resumed: no checkpoint",
        )
    )
    stream = io.StringIO()

    code = main(["--resume", "session-1"], runner=runner, stream=stream)

    assert code == EXIT_CONFIGURATION_ERROR
    assert "in-memory checkpoints do not survive" in stream.getvalue()


def test_a_failed_graph_run_exits_three() -> None:
    runner = RecordingRunner(result=outcome(status="failed"))
    stream = io.StringIO()

    code = main([QUESTION], runner=runner, stream=stream)

    assert code == EXIT_GRAPH_FAILED
    assert "Status: failed" in stream.getvalue()


def test_a_limited_run_still_exits_zero() -> None:
    runner = RecordingRunner(result=outcome(status="max_iterations"))

    code = main([QUESTION], runner=runner, stream=io.StringIO())

    assert code == EXIT_OK


def test_require_quality_exits_four_for_a_partial_report() -> None:
    """Step 5: the non-zero quality exit is opt-in."""
    runner = RecordingRunner(result=outcome(status="max_iterations"))
    stream = io.StringIO()

    code = main([QUESTION, "--require-quality"], runner=runner, stream=stream)

    assert code == EXIT_QUALITY_UNACCEPTED
    assert "--require-quality" in stream.getvalue()


def test_require_quality_exits_four_without_any_quality_pass() -> None:
    """Nothing judged cannot be accepted."""
    runner = RecordingRunner(result=outcome())

    code = main(
        [QUESTION, "--require-quality"], runner=runner, stream=io.StringIO()
    )

    assert code == EXIT_QUALITY_UNACCEPTED


def test_require_quality_exits_zero_for_an_accepted_report() -> None:
    runner = RecordingRunner(result=outcome(state=accepted_state()))
    stream = io.StringIO()

    code = main([QUESTION, "--require-quality"], runner=runner, stream=stream)

    assert code == EXIT_OK
    assert "Quality: accepted" in stream.getvalue()


def test_a_failed_graph_run_outranks_the_quality_flag() -> None:
    runner = RecordingRunner(result=outcome(status="failed"))

    code = main(
        [QUESTION, "--require-quality"], runner=runner, stream=io.StringIO()
    )

    assert code == EXIT_GRAPH_FAILED


def test_an_incomplete_run_still_exits_zero_and_says_why() -> None:
    runner = RecordingRunner(result=outcome(status="incomplete"))
    stream = io.StringIO()

    code = main([QUESTION], runner=runner, stream=stream)

    assert code == EXIT_OK
    assert "ended without an accepted critique" in stream.getvalue()


def test_recoverable_errors_are_grouped_as_warnings_not_failures() -> None:
    state = ResearchState(
        session_id="session-1",
        original_question=QUESTION,
        errors=[
            ResearchError(
                error_type="web_search_failed",
                source="tools.web_search",
                message="The search provider timed out.",
                details={"coverage_id": "topic-03"},
            )
        ],
    )
    runner = RecordingRunner(result=outcome(state=state))
    stream = io.StringIO()

    code = main([QUESTION], runner=runner, stream=stream)

    printed = stream.getvalue()
    assert code == EXIT_OK
    assert "Warnings:" in printed
    assert "tools.web_search: 1 error (coverage topic-03)" in printed


def test_verbose_progress_prints_the_typed_error_message() -> None:
    state = ResearchState(
        session_id="session-1",
        original_question=QUESTION,
        errors=[
            ResearchError(
                error_type="web_search_failed",
                source="tools.web_search",
                message="The search provider timed out.",
            )
        ],
    )
    runner = RecordingRunner(result=outcome(state=state))
    stream = io.StringIO()

    main([QUESTION, "--verbose"], runner=runner, stream=stream)

    assert "warning: [web_search_failed] The search provider timed out." in (
        stream.getvalue()
    )


def test_a_keyboard_interrupt_exits_one_hundred_thirty() -> None:
    runner = RecordingRunner(error=KeyboardInterrupt())
    stream = io.StringIO()

    code = main([QUESTION], runner=runner, stream=stream)

    assert code == EXIT_INTERRUPTED
    assert "cancelled" in stream.getvalue()


def test_an_unexpected_exception_is_not_swallowed() -> None:
    runner = RecordingRunner(error=RuntimeError("a defect"))

    with pytest.raises(RuntimeError, match="a defect"):
        main([QUESTION], runner=runner, stream=io.StringIO())


def test_the_module_entry_point_exposes_main() -> None:
    from deep_research.__main__ import main as module_main

    assert module_main is main


# --- the request budget ----------------------------------------------------


def budget_snapshot(
    provider: str,
    *,
    attempts: int,
    ceiling: int | None = None,
    effective_limit: int | None = None,
) -> RequestBudgetSnapshot:
    return RequestBudgetSnapshot(
        provider=provider,  # type: ignore[arg-type]
        attempts=attempts,
        ceiling=ceiling,
        effective_limit=effective_limit,
        input_tokens=0,
        output_tokens=0,
    )


def reserved_update() -> RequestBudgetUpdate:
    return RequestBudgetUpdate(
        kind="attempt_reserved",
        snapshot=budget_snapshot(
            "tavily", attempts=1, ceiling=11, effective_limit=11
        ),
    )


class BudgetReportingRunner:
    """Hand one budget update to the CLI's own handler, then return."""

    def __init__(self, result: ResearchOutcome | None = None) -> None:
        self.result = result if result is not None else outcome()
        self.handlers: list[object] = []

    def __call__(self, **kwargs: object) -> ResearchOutcome:
        handler = kwargs.get("request_budget_handler")
        self.handlers.append(handler)
        assert callable(handler)
        handler(reserved_update())
        return self.result


def test_main_builds_the_nested_request_budget_overrides() -> None:
    runner = RecordingRunner()

    main(
        [
            QUESTION,
            "--request-deepseek-attempt-ceiling",
            "5",
            "--request-openai-attempt-ceiling",
            "7",
            "--request-tavily-attempt-ceiling",
            "11",
            "--request-stop-fraction",
            "0.5",
        ],
        runner=runner,
        stream=io.StringIO(),
    )

    assert runner.calls[0].get("config_overrides") == {
        "request_budget": {
            "deepseek_attempt_ceiling": 5,
            "openai_attempt_ceiling": 7,
            "tavily_attempt_ceiling": 11,
            "stop_fraction": 0.5,
        }
    }


def test_only_the_supplied_request_budget_keys_reach_the_config() -> None:
    runner = RecordingRunner()

    main(
        [QUESTION, "--request-tavily-attempt-ceiling", "11"],
        runner=runner,
        stream=io.StringIO(),
    )

    assert runner.calls[0].get("config_overrides") == {
        "request_budget": {"tavily_attempt_ceiling": 11}
    }


def test_no_request_budget_flag_means_no_overrides_at_all() -> None:
    runner = RecordingRunner()

    main([QUESTION], runner=runner, stream=io.StringIO())

    assert runner.calls[0].get("config_overrides") is None


def test_main_passes_a_request_budget_stream_to_its_runner() -> None:
    from deep_research.cli import RequestBudgetStream

    runner = RecordingRunner()

    main([QUESTION], runner=runner, stream=io.StringIO())

    assert isinstance(
        runner.calls[0].get("request_budget_handler"), RequestBudgetStream
    )


def test_request_budget_updates_stream_live_only_when_verbose() -> None:
    """A worker thread's update is flushed in verbose mode, silent otherwise."""
    verbose_stream = FlushCountingStream()

    main(
        [QUESTION, "--verbose"],
        runner=BudgetReportingRunner(),
        stream=verbose_stream,
    )

    assert any("tavily" in line for line in verbose_stream.flushed_lines)

    quiet_stream = io.StringIO()
    main([QUESTION], runner=BudgetReportingRunner(), stream=quiet_stream)

    assert "tavily" not in quiet_stream.getvalue()
    assert "Request budget" not in quiet_stream.getvalue()


def test_the_request_budget_stream_serializes_worker_thread_writes() -> None:
    """Provider work really is concurrent; the stream must not interleave."""
    from deep_research.cli import RequestBudgetStream

    stream = io.StringIO()
    handler = RequestBudgetStream(stream, verbose=True)

    def worker() -> None:
        for _ in range(20):
            handler(reserved_update())

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    lines = [line for line in stream.getvalue().splitlines() if line]
    assert len(lines) == 160
    assert all(line.startswith("  request budget: tavily") for line in lines)
    assert all(line.endswith(")") for line in lines)


def test_the_request_budget_stream_renders_every_update_kind() -> None:
    """Every kind the budget publishes has its own line, refusal included.

    ``RequestBudget._notify`` catches ``Exception`` from an observer by design,
    so a label that went missing would raise ``KeyError`` inside the handler
    and be swallowed — the run would continue with that line silently absent
    rather than failing. The line most worth protecting is the refusal, which
    is the only place a ceiling is visible at the moment it bites. Asserting
    all three kinds here means ``BUDGET_UPDATE_LABELS`` cannot lose an entry
    without the suite noticing.
    """
    from deep_research.cli import BUDGET_UPDATE_LABELS, RequestBudgetStream

    stream = io.StringIO()
    handler = RequestBudgetStream(stream, verbose=True)
    snapshot = budget_snapshot(
        "tavily", attempts=11, ceiling=11, effective_limit=11
    )

    for kind in ("attempt_reserved", "tokens_reported", "attempt_blocked"):
        handler(RequestBudgetUpdate(kind=kind, snapshot=snapshot))  # type: ignore[arg-type]

    lines = [line for line in stream.getvalue().splitlines() if line]

    assert set(BUDGET_UPDATE_LABELS) == {
        "attempt_reserved",
        "tokens_reported",
        "attempt_blocked",
    }
    assert len(lines) == 3
    assert all(line.startswith("  request budget: tavily") for line in lines)
    assert "tokens reported post-response" in lines[1]
    assert "attempt refused at the declared ceiling" in lines[2]


def test_a_request_limit_graph_failure_exits_three_before_require_quality() -> None:
    """Exit 3 outranks the opt-in quality exit: the run did not finish."""
    runner = RecordingRunner(
        result=outcome(
            status="failed",
            request_budget_snapshots=(
                budget_snapshot(
                    "tavily", attempts=11, ceiling=11, effective_limit=11
                ),
            ),
        )
    )
    stream = io.StringIO()

    code = main(
        [QUESTION, "--require-quality", "--verbose"],
        runner=runner,
        stream=stream,
    )

    printed = stream.getvalue()
    assert code == EXIT_GRAPH_FAILED
    assert "Status: failed" in printed
    assert "Request budget:" in printed
    assert "--require-quality" not in printed
