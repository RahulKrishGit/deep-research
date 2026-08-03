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
        ]
    )

    assert options.question == "AI in healthcare"
    assert options.max_iterations == 5
    assert options.output_format == "markdown"
    assert options.config == "custom.yaml"
    assert options.verbose is True


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
    ):
        assert flag in help_text
