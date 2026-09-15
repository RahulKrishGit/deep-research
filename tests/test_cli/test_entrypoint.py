"""Tests for the CLI entry point: wiring, prompts, and exit codes."""

from __future__ import annotations

import io

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
)
from deep_research.graph.events import (
    node_completed_event,
    node_started_event,
    session_completed_event,
    session_started_event,
)
from deep_research.main import run_research_sync
from deep_research.observability import TokenUsage
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
    assert call == {
        "question": QUESTION,
        "resume_session_id": None,
        "config_path": "custom.yaml",
        "max_iterations": 5,
        "output_format": "markdown",
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
