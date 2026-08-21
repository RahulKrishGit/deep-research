import pytest

from deep_research.providers import (
    ProviderConfigurationError,
    resolve_request_settings,
)
from deep_research.utils.config import EffectiveModelConfig


@pytest.mark.parametrize("model", ["deepseek-v4-flash", "deepseek-v4-pro"])
def test_deepseek_capabilities_enable_high_or_max(model: str) -> None:
    resolved = resolve_request_settings(
        "deepseek",
        EffectiveModelConfig(
            model=model, thinking_mode="enabled", reasoning_effort="max"
        ),
    )

    assert resolved.reasoning_effort == "max"
    assert resolved.include_temperature is False


def test_deepseek_disabled_mode_dormants_effort_and_accepts_temperature() -> None:
    resolved = resolve_request_settings(
        "deepseek",
        EffectiveModelConfig(
            model="deepseek-v4-flash",
            thinking_mode="disabled",
            reasoning_effort="high",
        ),
    )

    assert resolved.reasoning_effort is None
    assert resolved.include_temperature is True


@pytest.mark.parametrize(
    ("model", "setting", "value"),
    [
        # reasoning_effort must be a valid ReasoningEffort literal for the
        # model-match case; "high" keeps the focus on the unknown model.
        ("unknown", "model", "high"),
        ("deepseek-v4-flash", "reasoning_effort", "medium"),
    ],
)
def test_capabilities_fail_closed_with_safe_actionable_errors(
    model: str, setting: str, value: str
) -> None:
    effective = EffectiveModelConfig(
        model=model,
        thinking_mode="enabled",
        reasoning_effort=value,
    )

    with pytest.raises(ProviderConfigurationError) as caught:
        resolve_request_settings("deepseek", effective)

    message = str(caught.value)
    assert "deepseek" in message
    assert model in message
    assert setting in message
    assert "API_KEY" not in message


@pytest.mark.parametrize(
    ("model", "mode", "configured", "sent", "temperature"),
    [
        ("gpt-4o", "disabled", "none", None, True),
        ("gpt-4o-2024-11-20", "disabled", "none", None, True),
        ("gpt-4.1-mini", "disabled", "none", None, True),
        ("gpt-5.4", "disabled", "high", "none", True),
        ("gpt-5.4-2026-03-05", "enabled", "xhigh", "xhigh", False),
        ("gpt-5.4-pro", "enabled", "medium", "medium", False),
        ("gpt-5.5", "enabled", "low", "low", False),
        ("gpt-5.5-pro-2026-04-23", "enabled", "xhigh", "xhigh", False),
        ("gpt-5.6", "disabled", "high", "none", True),
        ("gpt-5.6-terra", "enabled", "max", "max", False),
        ("gpt-5.6-luna-2026-06-01", "enabled", "high", "high", False),
        ("gpt-5.3-codex", "enabled", "xhigh", "xhigh", False),
        ("gpt-5.2", "disabled", "high", "none", True),
        ("gpt-5.2-pro", "enabled", "medium", "medium", False),
        ("gpt-5.1", "disabled", "high", "none", True),
        ("gpt-5", "enabled", "minimal", "minimal", False),
        ("gpt-5-mini", "enabled", "high", "high", False),
        ("gpt-5-pro", "enabled", "high", "high", False),
        ("o1", "enabled", "low", "low", False),
        ("o1-2024-12-17", "enabled", "high", "high", False),
        ("o3", "enabled", "medium", "medium", False),
        ("o4-mini-2025-04-16", "enabled", "high", "high", False),
    ],
)
def test_openai_capability_families_and_snapshots(
    model: str,
    mode: str,
    configured: str,
    sent: str | None,
    temperature: bool,
) -> None:
    resolved = resolve_request_settings(
        "openai",
        EffectiveModelConfig(
            model=model,
            thinking_mode=mode,
            reasoning_effort=configured,
        ),
    )

    assert resolved.reasoning_effort == sent
    assert resolved.include_temperature is temperature


@pytest.mark.parametrize(
    ("model", "mode", "effort"),
    [
        ("gpt-4o", "enabled", "none"),
        ("o3", "disabled", "medium"),
        ("o1", "enabled", "max"),
        ("gpt-5.4", "enabled", "max"),
        ("gpt-5.5", "enabled", "minimal"),
        ("gpt-5.5-pro", "enabled", "low"),
        ("gpt-5.6", "enabled", "minimal"),
    ],
)
def test_openai_rejects_unsupported_mode_or_effort(
    model: str, mode: str, effort: str
) -> None:
    with pytest.raises(ProviderConfigurationError):
        resolve_request_settings(
            "openai",
            EffectiveModelConfig(
                model=model,
                thinking_mode=mode,
                reasoning_effort=effort,
            ),
        )


def test_unknown_snapshot_family_does_not_match_by_substring() -> None:
    with pytest.raises(ProviderConfigurationError, match="model"):
        resolve_request_settings(
            "openai",
            EffectiveModelConfig(
                model="vendor-gpt-5.6-proxy",
                thinking_mode="enabled",
                reasoning_effort="high",
            ),
        )
