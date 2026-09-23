"""Anchored, fail-closed provider model capability registry.

Every supported model family is registered here as frozen metadata. The
resolver validates, in order: model match, thinking mode, enabled effort.
Nothing falls through to a provider request unvalidated.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from re import Pattern

from deep_research.providers.contracts import ProviderConfigurationError
from deep_research.utils.config import (
    EffectiveModelConfig,
    ProviderName,
    ReasoningEffort,
    ThinkingMode,
)


@dataclass(frozen=True)
class ModelCapability:
    """Frozen metadata for one anchored model family."""

    pattern: Pattern[str]
    thinking_modes: frozenset[ThinkingMode]
    enabled_efforts: frozenset[ReasoningEffort]
    disabled_effort: ReasoningEffort | None
    temperature_modes: frozenset[ThinkingMode]


@dataclass(frozen=True)
class ResolvedRequestSettings:
    """Validated request settings for one effective model configuration."""

    effective: EffectiveModelConfig
    reasoning_effort: ReasoningEffort | None
    include_temperature: bool


def _capability(
    pattern: str,
    modes: str,
    efforts: str,
    disabled_effort: str | None,
    temperature: str,
) -> ModelCapability:
    """Build one registry entry from comma-separated metadata.

    ``disabled_effort`` is the effort sent when thinking is disabled, or
    ``None`` to omit the parameter entirely. ``temperature`` uses ``"none"``
    for families that never send a temperature parameter.
    """
    return ModelCapability(
        pattern=re.compile(pattern),
        thinking_modes=frozenset(modes.split(",")),
        enabled_efforts=frozenset(efforts.split(",")),
        disabled_effort=disabled_effort,
        temperature_modes=(
            frozenset()
            if temperature == "none"
            else frozenset(temperature.split(","))
        ),
    )


# Entries are ordered from most specific to most general so -pro/-codex
# snapshots are matched before their base families.
_CAPABILITIES: dict[ProviderName, tuple[ModelCapability, ...]] = {
    "deepseek": (
        _capability(
            r"^deepseek-v4-(flash|pro)$",
            modes="enabled,disabled",
            efforts="high,max",
            disabled_effort=None,
            temperature="disabled",
        ),
    ),
    "openai": (
        _capability(
            r"^gpt-4o(?:-mini)?(?:-\d{4}-\d{2}-\d{2})?$",
            modes="disabled",
            efforts="none",
            disabled_effort=None,
            temperature="disabled",
        ),
        _capability(
            r"^gpt-4\.1(?:-(?:mini|nano))?(?:-\d{4}-\d{2}-\d{2})?$",
            modes="disabled",
            efforts="none",
            disabled_effort=None,
            temperature="disabled",
        ),
        _capability(
            r"^gpt-5\.4-pro(?:-\d{4}-\d{2}-\d{2})?$",
            modes="enabled",
            efforts="medium,high,xhigh",
            disabled_effort=None,
            temperature="none",
        ),
        _capability(
            r"^gpt-5\.4(?:-(?:mini|nano))?(?:-\d{4}-\d{2}-\d{2})?$",
            modes="enabled,disabled",
            efforts="low,medium,high,xhigh",
            disabled_effort="none",
            temperature="disabled",
        ),
        _capability(
            r"^gpt-5\.5-pro(?:-\d{4}-\d{2}-\d{2})?$",
            modes="enabled",
            efforts="medium,high,xhigh",
            disabled_effort=None,
            temperature="none",
        ),
        _capability(
            r"^gpt-5\.5(?:-\d{4}-\d{2}-\d{2})?$",
            modes="enabled,disabled",
            efforts="low,medium,high,xhigh",
            disabled_effort="none",
            temperature="disabled",
        ),
        _capability(
            r"^gpt-5\.6(?:-(?:sol|terra|luna))?(?:-\d{4}-\d{2}-\d{2})?$",
            modes="enabled,disabled",
            efforts="low,medium,high,xhigh,max",
            disabled_effort="none",
            temperature="disabled",
        ),
        _capability(
            r"^gpt-5\.3-codex(?:-\d{4}-\d{2}-\d{2})?$",
            modes="enabled",
            efforts="low,medium,high,xhigh",
            disabled_effort=None,
            temperature="none",
        ),
        _capability(
            r"^gpt-5\.2-pro(?:-\d{4}-\d{2}-\d{2})?$",
            modes="enabled",
            efforts="medium,high,xhigh",
            disabled_effort=None,
            temperature="none",
        ),
        _capability(
            r"^gpt-5\.2(?:-\d{4}-\d{2}-\d{2})?$",
            modes="enabled,disabled",
            efforts="low,medium,high,xhigh",
            disabled_effort="none",
            temperature="disabled",
        ),
        _capability(
            r"^gpt-5\.1(?:-\d{4}-\d{2}-\d{2})?$",
            modes="enabled,disabled",
            efforts="low,medium,high",
            disabled_effort="none",
            temperature="disabled",
        ),
        _capability(
            r"^gpt-5-pro(?:-\d{4}-\d{2}-\d{2})?$",
            modes="enabled",
            efforts="high",
            disabled_effort=None,
            temperature="none",
        ),
        _capability(
            r"^gpt-5(?:-(?:mini|nano))?(?:-\d{4}-\d{2}-\d{2})?$",
            modes="enabled",
            efforts="minimal,low,medium,high",
            disabled_effort=None,
            temperature="none",
        ),
        _capability(
            r"^o1(?:-\d{4}-\d{2}-\d{2})?$",
            modes="enabled",
            efforts="low,medium,high",
            disabled_effort=None,
            temperature="none",
        ),
        _capability(
            r"^o3(?:-\d{4}-\d{2}-\d{2})?$",
            modes="enabled",
            efforts="low,medium,high",
            disabled_effort=None,
            temperature="none",
        ),
        _capability(
            r"^o4-mini(?:-\d{4}-\d{2}-\d{2})?$",
            modes="enabled",
            efforts="low,medium,high",
            disabled_effort=None,
            temperature="none",
        ),
    ),
}


def _accepted_text(values: frozenset[str]) -> str:
    """Render accepted metadata as sorted, comma-separated text."""
    return ", ".join(sorted(values))


def with_reasoning_effort(
    effective: EffectiveModelConfig, override: str | None
) -> EffectiveModelConfig:
    """Return ``effective`` with a per-call reasoning-effort override applied.

    A request-level setting, not a profile edit: it changes what *this one*
    request resolves to and nothing the agent's own configuration carries, so
    a retry can reason at another effort while every later call from the same
    agent keeps the configured one. ``None`` returns the configuration
    unchanged, which is what every ordinary call sends.

    The result is validated by :func:`resolve_request_settings` like any other
    effective configuration, so an unsupported level is refused before the SDK
    is touched, and a model whose thinking is disabled keeps sending no effort
    at all.

    ``model_copy`` is what applies the override: it is a copy rather than a
    mutation, which is what a frozen configuration requires, and the value is
    validated by the registry immediately below rather than by the copy.
    """
    if override is None:
        return effective
    return effective.model_copy(update={"reasoning_effort": override})


def capability_for(provider: ProviderName, model: str) -> ModelCapability:
    """Return the capability whose anchored pattern matches ``model``.

    Raises:
        ProviderConfigurationError: If the provider or model is not
            registered, so an unknown or typo'd model never reaches a
            provider request.
    """
    entries = _CAPABILITIES.get(provider)
    if entries is None:
        supported = _accepted_text(frozenset(_CAPABILITIES))
        raise ProviderConfigurationError(
            f"Provider '{provider}' is not supported; "
            f"supported providers: {supported}"
        )
    for capability in entries:
        if capability.pattern.fullmatch(model):
            return capability
    supported = _accepted_text(frozenset(e.pattern.pattern for e in entries))
    raise ProviderConfigurationError(
        f"Provider '{provider}' does not support model '{model}'; "
        f"supported models: {supported}"
    )


def resolve_request_settings(
    provider: ProviderName, effective: EffectiveModelConfig
) -> ResolvedRequestSettings:
    """Validate ``effective`` against the capability registry.

    Checks model match, then thinking mode, then enabled effort. A disabled
    thinking mode never sends the configured dormant effort; the family's
    disabled behavior applies instead.

    Raises:
        ProviderConfigurationError: If any checked setting is unsupported.
    """
    capability = capability_for(provider, effective.model)
    mode = effective.thinking_mode
    if mode not in capability.thinking_modes:
        supported = _accepted_text(capability.thinking_modes)
        raise ProviderConfigurationError(
            f"Provider '{provider}' model '{effective.model}' does not "
            f"support thinking_mode '{mode}'; supported thinking modes: "
            f"{supported}"
        )
    if mode == "enabled":
        effort = effective.reasoning_effort
        if effort not in capability.enabled_efforts:
            supported = _accepted_text(capability.enabled_efforts)
            raise ProviderConfigurationError(
                f"Provider '{provider}' model '{effective.model}' does not "
                f"support reasoning_effort '{effort}'; supported "
                f"reasoning_effort values: {supported}"
            )
        return ResolvedRequestSettings(
            effective=effective,
            reasoning_effort=effort,
            include_temperature=mode in capability.temperature_modes,
        )
    return ResolvedRequestSettings(
        effective=effective,
        reasoning_effort=capability.disabled_effort,
        include_temperature=mode in capability.temperature_modes,
    )
