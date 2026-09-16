"""Tests for the CLI's argument surface."""

from __future__ import annotations

import pytest

from deep_research.cli import CliOptions, build_parser, parse_arguments
from deep_research.main import DEFAULT_CONFIG_PATH

QUESTION = "What are the security implications of quantum computing?"


def test_a_bare_question_is_the_whole_command() -> None:
    options = parse_arguments([QUESTION])

    assert options == CliOptions(
        question=QUESTION,
        interactive=False,
        resume=None,
        max_iterations=None,
        output_format=None,
        config=DEFAULT_CONFIG_PATH,
        verbose=False,
        require_quality=False,
    )


def test_every_documented_option_parses() -> None:
    options = parse_arguments(
        [
            "AI in healthcare",
            "--max-iterations",
            "5",
            "--output-format",
            "markdown",
            "--config",
            "custom.yaml",
            "--verbose",
            "--require-quality",
        ]
    )

    assert options.question == "AI in healthcare"
    assert options.max_iterations == 5
    assert options.output_format == "markdown"
    assert options.config == "custom.yaml"
    assert options.verbose is True
    assert options.require_quality is True


def test_interactive_takes_no_question() -> None:
    options = parse_arguments(["--interactive"])

    assert options.interactive is True
    assert options.question is None


def test_resume_takes_a_session_id() -> None:
    options = parse_arguments(["--resume", "session-1"])

    assert options.resume == "session-1"
    assert options.question is None


def test_a_question_and_interactive_together_are_a_usage_error() -> None:
    with pytest.raises(SystemExit) as caught:
        parse_arguments([QUESTION, "--interactive"])

    assert caught.value.code == 2


def test_a_question_and_resume_together_are_a_usage_error() -> None:
    with pytest.raises(SystemExit) as caught:
        parse_arguments([QUESTION, "--resume", "session-1"])

    assert caught.value.code == 2


def test_interactive_and_resume_together_are_a_usage_error() -> None:
    with pytest.raises(SystemExit) as caught:
        parse_arguments(["--interactive", "--resume", "session-1"])

    assert caught.value.code == 2


def test_no_arguments_at_all_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as caught:
        parse_arguments([])

    assert caught.value.code == 2


def test_a_non_positive_iteration_budget_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as caught:
        parse_arguments([QUESTION, "--max-iterations", "0"])

    assert caught.value.code == 2


def test_the_help_text_names_every_documented_option(capsys) -> None:
    build_parser().print_help()

    help_text = capsys.readouterr().out
    for flag in (
        "--interactive",
        "--resume",
        "--max-iterations",
        "--output-format",
        "--config",
        "--verbose",
        "--require-quality",
        "--request-deepseek-attempt-ceiling",
        "--request-openai-attempt-ceiling",
        "--request-tavily-attempt-ceiling",
        "--request-stop-fraction",
    ):
        assert flag in help_text


REQUEST_CEILING_FLAGS = (
    "--request-deepseek-attempt-ceiling",
    "--request-openai-attempt-ceiling",
    "--request-tavily-attempt-ceiling",
)


def test_the_request_budget_options_are_absent_by_default() -> None:
    """An omitted request option must leave no active limit behind."""
    options = parse_arguments([QUESTION])

    assert options.request_deepseek_attempt_ceiling is None
    assert options.request_openai_attempt_ceiling is None
    assert options.request_tavily_attempt_ceiling is None
    assert options.request_stop_fraction is None


def test_the_request_attempt_ceilings_parse_as_positive_ints() -> None:
    options = parse_arguments(
        [
            QUESTION,
            "--request-deepseek-attempt-ceiling",
            "5",
            "--request-openai-attempt-ceiling",
            "7",
            "--request-tavily-attempt-ceiling",
            "11",
        ]
    )

    assert options.request_deepseek_attempt_ceiling == 5
    assert options.request_openai_attempt_ceiling == 7
    assert options.request_tavily_attempt_ceiling == 11


@pytest.mark.parametrize("flag", REQUEST_CEILING_FLAGS)
@pytest.mark.parametrize("value", ["0", "-1", "-40"])
def test_a_non_positive_request_attempt_ceiling_is_a_usage_error(
    flag: str, value: str
) -> None:
    """A ceiling of zero is a caller mistake, not a declaration."""
    with pytest.raises(SystemExit) as caught:
        parse_arguments([QUESTION, flag, value])

    assert caught.value.code == 2


def test_the_request_stop_fraction_accepts_the_whole_unit_interval() -> None:
    whole = parse_arguments([QUESTION, "--request-stop-fraction", "1.0"])
    partial = parse_arguments([QUESTION, "--request-stop-fraction", "0.25"])

    assert whole.request_stop_fraction == 1.0
    assert partial.request_stop_fraction == 0.25


@pytest.mark.parametrize(
    "value", ["0", "0.0", "-0.5", "1.5", "2", "nan", "inf", "half"]
)
def test_a_request_stop_fraction_outside_the_unit_interval_is_a_usage_error(
    value: str,
) -> None:
    """``RequestBudgetConfig`` declares ``0 < F <= 1``, so the CLI does too."""
    with pytest.raises(SystemExit) as caught:
        parse_arguments([QUESTION, "--request-stop-fraction", value])

    assert caught.value.code == 2
