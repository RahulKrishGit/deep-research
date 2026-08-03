"""Tests for the enumerated runtime configuration failure type."""

from __future__ import annotations

import pytest

from deep_research.runtime.errors import (
    CONFIGURATION_HINTS,
    ResearchConfigurationError,
    configuration_error,
)


def test_configuration_error_carries_its_enumerated_hint() -> None:
    error = configuration_error(
        reason="missing_secrets",
        message="Missing required environment variables: OPENAI_API_KEY",
    )

    assert isinstance(error, ResearchConfigurationError)
    assert error.reason == "missing_secrets"
    assert error.hint == CONFIGURATION_HINTS["missing_secrets"]
    assert "OPENAI_API_KEY" in str(error)


def test_an_unenumerated_reason_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown configuration reason"):
        configuration_error(reason="something_new", message="nope")


def test_every_hint_is_a_non_blank_sentence() -> None:
    assert CONFIGURATION_HINTS
    for reason, hint in CONFIGURATION_HINTS.items():
        assert reason.strip() == reason
        assert hint.strip()
        assert hint.endswith(".")


def test_blank_inputs_and_conflicts_have_enumerated_hints() -> None:
    for reason in ("blank_session_id", "question_and_resume"):
        assert reason in CONFIGURATION_HINTS
        assert CONFIGURATION_HINTS[reason].endswith(".")
