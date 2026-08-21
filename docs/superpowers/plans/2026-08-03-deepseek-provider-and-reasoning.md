# DeepSeek Provider and Model Reasoning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make DeepSeek V4 Flash the default chat provider, preserve OpenAI chat and embeddings, and resolve and validate global or per-agent thinking settings before any model request.

**Architecture:** Move chat contracts and errors into a provider-neutral module, resolve immutable effective model settings in configuration, and validate those settings against a fail-closed provider capability registry. Dedicated OpenAI Responses and DeepSeek Chat Completions adapters consume the same resolved settings; a small factory selects one chat adapter while runtime assembly always constructs OpenAI embeddings separately.

**Tech Stack:** Python 3.11+, Pydantic v2, `openai>=2` (`AsyncOpenAI`, Responses API, and OpenAI-compatible Chat Completions), PyYAML/python-dotenv, existing `Tracker`, pytest/pytest-asyncio, Ruff.

## Global Constraints

- Preserve `requires-python = ">=3.11"` and the existing `openai>=2` dependency; add no runtime dependency.
- DeepSeek is the default chat provider, `deepseek-v4-flash` is the default chat model, thinking is `enabled`, and reasoning effort is `high`.
- OpenAI remains selectable for chat and remains the only embedding provider in every chat-provider mode.
- Provider/model/thinking selection continues through YAML, existing environment overrides, and request-scoped configuration overrides; add no CLI or HTTP request fields.
- `thinking_mode` accepts only `enabled` or `disabled`; `reasoning_effort` accepts only `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, or `max`.
- A string `model_overrides` value changes only the model; a structured value may set any subset of `model`, `thinking_mode`, and `reasoning_effort`; provider overrides are forbidden.
- Resolve and validate all six agents (`planner`, `researcher`, `source_evaluator`, `fact_checker`, `synthesizer`, `critic`) before graph construction or any model request.
- Capability lookup is code-owned and fail-closed. Unknown providers/models and unsupported thinking modes/efforts raise safe `ProviderConfigurationError` messages naming provider, model, setting, and accepted values, never secret values.
- Capability metadata validates project support only; it does not discover live account entitlements, and an otherwise valid model may still be rejected by the remote API.
- Do not infer a provider from a model name or API key and do not add automatic failover, load balancing, or cost-based routing across providers.
- DeepSeek uses the code-owned base URL `https://api.deepseek.com`; do not add a configurable gateway, proxy, or base URL.
- Do not add DeepSeek function calling, SDK tool execution, streaming output, or provider reasoning-content persistence/exposure; agents continue to execute tools only in the project-owned ReAct loop.
- Keep SDK imports lazy and clients injectable so every normal test remains offline.
- DeepSeek structured output uses JSON mode plus local `schema.model_validate_json(...)`, with at most one repair request; transport, authentication, rate-limit, filtering, truncation, and provider-resource failures are never repaired as schema failures.
- When thinking is disabled, retain configured effort as a dormant default but do not send it to DeepSeek. For OpenAI, send the documented no-reasoning effort only when the matched capability entry defines one.
- Send temperature only when the matched provider/model/mode capability accepts it; never send a known-ignored setting.
- Keep provider prompts, raw outputs, raw reasoning content, schema-validation payloads, repair prompts, and API keys out of errors, serialized settings, tracker inputs/outputs, spans, metrics, logs, snapshots, and committed files.
- Preserve existing ReAct/agent/graph partial-progress behavior. Provider failures remain typed research errors and never expose provider text to users.
- The normal pytest command must deselect the live DeepSeek test. The live test runs only with the `live` marker, `RUN_DEEPSEEK_LIVE_TESTS=1`, and a non-blank `DEEPSEEK_API_KEY`.
- Do not change persisted research state, checkpoints, memory vectors, CLI arguments, or HTTP request/response schemas.

---

## Concrete File Map

| File | Change | Responsibility |
|---|---|---|
| `src/deep_research/providers/contracts.py` | Create | Own `ChatMessage`, `ChatResult`, `ProviderError`, the shared error subclasses, and the `OpenAIProviderError` compatibility alias. |
| `src/deep_research/providers/capabilities.py` | Create | Own fail-closed model-family metadata, matching, effective-mode validation, and safe request-setting resolution. |
| `src/deep_research/providers/deepseek_provider.py` | Create | Implement injected/lazy DeepSeek client construction, Chat Completions translation, plain output, JSON validation/repair, usage mapping, safe errors, and spans. |
| `src/deep_research/providers/factory.py` | Create | Select exactly one chat adapter from the closed provider setting. |
| `src/deep_research/providers/openai_provider.py` | Modify | Import neutral contracts/errors and translate effective thinking, effort, temperature, and span metadata into Responses calls without regressing native parsed output. |
| `src/deep_research/providers/__init__.py` | Modify | Export both adapters, factory, neutral contracts/errors, capability interfaces, and compatibility alias; remove OpenAI-only description. |
| `src/deep_research/utils/config.py` | Modify | Add closed provider/thinking/effort types, structured per-agent overrides, immutable effective resolution, defaults, environment overrides, and provider-conditional strict secrets. |
| `src/deep_research/runtime/assembly.py` | Modify | Validate all six effective configurations, call the chat factory, retain independent OpenAI embeddings, and report selected provider safely. |
| `src/deep_research/runtime/errors.py` | Modify | Make missing-secret/provider setup hints provider-neutral. |
| `src/deep_research/agents/base.py` | Modify | Describe `StructuredCompleter` as provider-neutral. |
| `src/deep_research/agents/react.py` | Modify | Catch `ProviderError` instead of the OpenAI alias. |
| `src/deep_research/agents/planner.py` | Modify | Catch `ProviderError` instead of the OpenAI alias. |
| `src/deep_research/agents/researcher.py` | Modify | Catch `ProviderError` instead of the OpenAI alias. |
| `src/deep_research/agents/source_evaluator.py` | Modify | Catch `ProviderError` instead of the OpenAI alias. |
| `src/deep_research/agents/fact_checker.py` | Modify | Catch `ProviderError` instead of the OpenAI alias. |
| `src/deep_research/agents/synthesizer.py` | Modify | Catch `ProviderError` instead of the OpenAI alias. |
| `src/deep_research/agents/critic.py` | Modify | Catch `ProviderError` instead of the OpenAI alias. |
| `config.yaml` | Modify | Commit DeepSeek/V4 Flash/enabled/high defaults. |
| `.env.example` | Modify | Add blank DeepSeek key and provider/reasoning examples; keep every secret blank. |
| `pyproject.toml` | Modify | Register `live` marker and deselect it from normal pytest runs. |
| `README.md` | Modify | Document both chat providers, embeddings, overrides, secret matrix, migration, safety, and exact live-test commands. |
| `tests/test_provider_contracts.py` | Create | Pin neutral types, hierarchy, compatibility alias, and agent/provider failure behavior. |
| `tests/test_provider_capabilities.py` | Create | Pin exact model-family matching, snapshots, modes, effort subsets, temperature rules, and safe fail-closed errors. |
| `tests/test_deepseek_provider.py` | Create | Provide injected Chat Completions fakes and comprehensive offline DeepSeek request/error/telemetry tests. |
| `tests/test_provider_factory.py` | Create | Pin exact adapter selection and defensive unknown-provider handling. |
| `tests/live/test_deepseek_live.py` | Create | Perform one explicitly opted-in bounded DeepSeek structured request. |
| `tests/test_config.py` | Modify | Test defaults, environment/request precedence, override resolution/inheritance/immutability, closed values, conditional secrets, and secret absence. |
| `tests/test_openai_provider.py` | Modify | Make OpenAI fixtures explicit and test reasoning/no-reasoning/temperature translation and safe span metadata. |
| `tests/test_runtime/test_assembly.py` | Modify | Test all-six prevalidation, factory integration, provider-neutral setup errors, no fallback, and OpenAI embeddings in DeepSeek mode. |
| `tests/test_runtime/test_run_research.py` | Modify | Supply the new default DeepSeek secret in strict-mode fixtures and test safe fail-fast provider/model errors. |
| `tests/test_runtime/test_errors.py` | Modify | Pin provider-neutral hints. |
| `tests/test_cli/test_entrypoint.py` | Modify | Keep strict default configuration fixtures valid and provider-neutral user guidance. |
| `tests/test_imports.py` | Modify | Pin new public exports and old alias compatibility. |

---

### Task 1: Provider-neutral chat contracts and errors

**Files:**
- Create: `src/deep_research/providers/contracts.py`
- Create: `tests/test_provider_contracts.py`
- Modify: `src/deep_research/providers/openai_provider.py`
- Modify: `src/deep_research/providers/__init__.py`
- Modify: `src/deep_research/agents/base.py`
- Modify: `src/deep_research/agents/react.py`
- Modify: `src/deep_research/agents/planner.py`
- Modify: `src/deep_research/agents/researcher.py`
- Modify: `src/deep_research/agents/source_evaluator.py`
- Modify: `src/deep_research/agents/fact_checker.py`
- Modify: `src/deep_research/agents/synthesizer.py`
- Modify: `src/deep_research/agents/critic.py`
- Modify: `tests/test_imports.py`
- Modify: focused agent tests that explicitly instantiate `OpenAIProviderError`

**Interfaces:**
- Preserves: `MessageRole = Literal["developer", "system", "user", "assistant"]`.
- Produces: `ChatMessage(role: MessageRole, content: str)` and `ChatResult(text: str, model: str, usage: TokenUsage)`.
- Produces: `ProviderError(RuntimeError)`, `ProviderConfigurationError`, `ProviderTimeoutError`, `ProviderRateLimitError`, `ProviderResponseError`, and `StructuredOutputError`.
- Produces: `OpenAIProviderError = ProviderError` as an identity alias, not a second base class.
- Consumed by: both adapters, all agent catches, graph/runtime boundaries, and public imports.

- [ ] **Step 1: Write the failing neutral-contract tests**

Create `tests/test_provider_contracts.py`:

```python
from deep_research.providers import (
    ChatMessage,
    OpenAIProviderError,
    ProviderConfigurationError,
    ProviderError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
    StructuredOutputError,
)


def test_provider_errors_share_one_neutral_base() -> None:
    assert OpenAIProviderError is ProviderError
    for error_type in (
        ProviderConfigurationError,
        ProviderTimeoutError,
        ProviderRateLimitError,
        ProviderResponseError,
        StructuredOutputError,
    ):
        assert issubclass(error_type, ProviderError)


def test_chat_message_accepts_provider_neutral_roles() -> None:
    assert ChatMessage(role="developer", content="policy").role == "developer"
    assert ChatMessage(role="system", content="policy").role == "system"
```

Update `tests/test_imports.py::test_provider_public_api_imports` to import `ProviderError` and assert `OpenAIProviderError is ProviderError`.

- [ ] **Step 2: Run the neutral-contract tests to verify RED**

Run: `python -m pytest tests/test_provider_contracts.py tests/test_imports.py::test_provider_public_api_imports -q`

Expected: FAIL because `contracts.py` and `ProviderError` do not exist.

- [ ] **Step 3: Create the neutral contracts and alias**

Create `src/deep_research/providers/contracts.py` with this exact public shape:

```python
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from deep_research.observability import TokenUsage

MessageRole = Literal["developer", "system", "user", "assistant"]


class ProviderContract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ChatMessage(ProviderContract):
    role: MessageRole
    content: str = Field(min_length=1)


class ChatResult(ProviderContract):
    text: str = Field(min_length=1)
    model: str = Field(min_length=1)
    usage: TokenUsage


class ProviderError(RuntimeError):
    """Base caller-facing error for every chat provider boundary."""


class ProviderConfigurationError(ProviderError):
    """The selected provider or effective model configuration is invalid."""


class ProviderTimeoutError(ProviderError):
    """A provider request exceeded its timeout."""


class ProviderRateLimitError(ProviderError):
    """A provider rejected a request due to rate limiting."""


class ProviderResponseError(ProviderError):
    """A provider returned an unusable response or status error."""


class StructuredOutputError(ProviderError):
    """Structured output remained invalid after one repair request."""


OpenAIProviderError = ProviderError
```

Remove the duplicate declarations from `openai_provider.py`, import these names from `contracts.py`, and re-export them from `providers/__init__.py`.

- [ ] **Step 4: Migrate runtime catches to the neutral base**

In each agent module listed in this task, replace:

```python
from deep_research.providers import OpenAIProviderError
```

with:

```python
from deep_research.providers import ProviderError
```

and replace every `except OpenAIProviderError as error:` with `except ProviderError as error:`. Update `StructuredCompleter`'s docstring to name `OpenAIChatProvider` and `DeepSeekChatProvider` as implementations without changing its method signature.

- [ ] **Step 5: Update compatibility-focused tests**

Where focused agent tests create the old name solely to exercise provider failure handling, import and construct `ProviderError("down")`, and assert recorded details use `{"exception_type": "ProviderError"}`. Retain one import assertion in `tests/test_imports.py` proving `OpenAIProviderError` still resolves as an alias.

- [ ] **Step 6: Run focused and regression tests to verify GREEN**

Run: `python -m pytest tests/test_provider_contracts.py tests/test_imports.py::test_provider_public_api_imports tests/test_agents/test_react.py tests/test_agents/test_planner.py tests/test_agents/test_researcher.py tests/test_agents/test_source_evaluator.py tests/test_agents/test_fact_checker.py tests/test_agents/test_synthesizer.py tests/test_agents/test_critic.py -q`

Expected: PASS; provider failures still follow existing partial-progress paths and the compatibility import remains valid.

- [ ] **Step 7: Commit the neutral boundary**

```bash
git add src/deep_research/providers/contracts.py src/deep_research/providers/openai_provider.py src/deep_research/providers/__init__.py src/deep_research/agents tests/test_provider_contracts.py tests/test_imports.py tests/test_agents
git commit -m "refactor: make chat provider contracts neutral"
```

---

### Task 2: Effective model configuration, defaults, and conditional secrets

**Files:**
- Modify: `src/deep_research/utils/config.py`
- Modify: `config.yaml`
- Modify: `tests/test_config.py`
- Modify: `tests/test_openai_provider.py`
- Modify: `tests/test_runtime/test_run_research.py`
- Modify: `tests/test_cli/test_entrypoint.py`

**Interfaces:**
- Produces: `ProviderName = Literal["deepseek", "openai"]`.
- Produces: `ThinkingMode = Literal["enabled", "disabled"]`.
- Produces: `ReasoningEffort = Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"]`.
- Produces: frozen `AgentModelOverride(model: str | None, thinking_mode: ThinkingMode | None, reasoning_effort: ReasoningEffort | None)`.
- Produces: frozen `EffectiveModelConfig(model: str, thinking_mode: ThinkingMode, reasoning_effort: ReasoningEffort)`.
- Produces: `LLMConfig.resolve_for(agent_name: str | None) -> EffectiveModelConfig`; retains `model_for(...) -> str` as a compatibility projection.
- Changes: `LLMConfig.model_overrides: dict[str, str | AgentModelOverride]`.
- Changes: `_validate_runtime_secrets(*, provider: ProviderName, tracing_enabled: bool) -> None`.

- [ ] **Step 1: Write failing default and resolver tests**

Add these tests to `tests/test_config.py`:

```python
from pydantic import ValidationError

from deep_research.utils.config import EffectiveModelConfig


def test_llm_defaults_select_deepseek_reasoning() -> None:
    llm = LLMConfig()

    assert llm.provider == "deepseek"
    assert llm.resolve_for(None) == EffectiveModelConfig(
        model="deepseek-v4-flash",
        thinking_mode="enabled",
        reasoning_effort="high",
    )


def test_agent_model_overrides_support_string_and_structured_forms() -> None:
    llm = LLMConfig(
        model_overrides={
            "planner": "deepseek-v4-pro",
            "critic": {
                "thinking_mode": "enabled",
                "reasoning_effort": "max",
            },
        }
    )

    assert llm.resolve_for("planner") == EffectiveModelConfig(
        model="deepseek-v4-pro",
        thinking_mode="enabled",
        reasoning_effort="high",
    )
    assert llm.resolve_for("critic") == EffectiveModelConfig(
        model="deepseek-v4-flash",
        thinking_mode="enabled",
        reasoning_effort="max",
    )
    assert llm.resolve_for("researcher") == llm.resolve_for(None)


def test_effective_model_config_is_immutable() -> None:
    effective = LLMConfig().resolve_for("planner")

    with pytest.raises(ValidationError):
        effective.model = "changed"  # type: ignore[misc]


@pytest.mark.parametrize("provider", ["anthropic", "", "DEEPSEEK"])
def test_provider_is_a_closed_lowercase_configuration_value(provider: str) -> None:
    with pytest.raises(ValidationError):
        LLMConfig(provider=provider)


def test_structured_agent_override_rejects_provider_field() -> None:
    with pytest.raises(ValidationError, match="provider"):
        LLMConfig(model_overrides={"critic": {"provider": "openai"}})
```

- [ ] **Step 2: Run resolver tests to verify RED**

Run: `python -m pytest tests/test_config.py -k "llm_defaults or agent_model_overrides or effective_model_config or closed_lowercase or rejects_provider" -q`

Expected: FAIL because the new types/defaults and resolver are absent.

- [ ] **Step 3: Implement closed types and immutable resolution**

Add the exact models before `LLMConfig` in `utils/config.py`:

```python
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ProviderName = Literal["deepseek", "openai"]
ThinkingMode = Literal["enabled", "disabled"]
ReasoningEffort = Literal[
    "none", "minimal", "low", "medium", "high", "xhigh", "max"
]


class AgentModelOverride(BaseModel):
    model_config = ConfigDict(
        extra="forbid", str_strip_whitespace=True, frozen=True
    )
    model: str | None = Field(default=None, min_length=1)
    thinking_mode: ThinkingMode | None = None
    reasoning_effort: ReasoningEffort | None = None


class EffectiveModelConfig(BaseModel):
    model_config = ConfigDict(
        extra="forbid", str_strip_whitespace=True, frozen=True
    )
    model: str = Field(min_length=1)
    thinking_mode: ThinkingMode
    reasoning_effort: ReasoningEffort
```

Change `LLMConfig` fields and resolver to:

```python
provider: ProviderName = "deepseek"
model: str = Field(default="deepseek-v4-flash", min_length=1)
thinking_mode: ThinkingMode = "enabled"
reasoning_effort: ReasoningEffort = "high"
model_overrides: dict[str, str | AgentModelOverride] = Field(default_factory=dict)

def resolve_for(self, agent_name: str | None) -> EffectiveModelConfig:
    override = None if agent_name is None else self.model_overrides.get(agent_name)
    if isinstance(override, str):
        return EffectiveModelConfig(
            model=override,
            thinking_mode=self.thinking_mode,
            reasoning_effort=self.reasoning_effort,
        )
    return EffectiveModelConfig(
        model=self.model if override is None or override.model is None else override.model,
        thinking_mode=(
            self.thinking_mode
            if override is None or override.thinking_mode is None
            else override.thinking_mode
        ),
        reasoning_effort=(
            self.reasoning_effort
            if override is None or override.reasoning_effort is None
            else override.reasoning_effort
        ),
    )

def model_for(self, agent_name: str | None) -> str:
    return self.resolve_for(agent_name).model
```

- [ ] **Step 4: Add environment overrides and provider-conditional secrets**

Add:

```python
"LLM_THINKING_MODE": ("llm", "thinking_mode"),
"LLM_REASONING_EFFORT": ("llm", "reasoning_effort"),
```

to `_ENVIRONMENT_OVERRIDES`. Replace the fixed provider-secret tuple with:

```python
_COMMON_REQUIRED_ENVIRONMENT_VARIABLES = ("OPENAI_API_KEY", "TAVILY_API_KEY")
_CHAT_PROVIDER_ENVIRONMENT_VARIABLES = {"deepseek": ("DEEPSEEK_API_KEY",), "openai": ()}


def _validate_runtime_secrets(
    *, provider: ProviderName, tracing_enabled: bool
) -> None:
    required = [
        *_CHAT_PROVIDER_ENVIRONMENT_VARIABLES[provider],
        *_COMMON_REQUIRED_ENVIRONMENT_VARIABLES,
    ]
```

Retain the existing conditional LangSmith extension and blank-value check, and pass `settings.llm.provider` from `load_config`.

- [ ] **Step 5: Test environment precedence and conditional secret rules**

Extend `tests/test_config.py` with:

```python
@pytest.mark.parametrize(
    ("name", "value", "attribute"),
    [
        ("LLM_PROVIDER", "openai", "provider"),
        ("LLM_MODEL", "gpt-5.6", "model"),
        ("LLM_THINKING_MODE", "disabled", "thinking_mode"),
        ("LLM_REASONING_EFFORT", "max", "reasoning_effort"),
    ],
)
def test_chat_environment_overrides(
    config_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
    attribute: str,
) -> None:
    monkeypatch.setenv(name, value)
    assert getattr(load_config(str(config_path)).llm, attribute) == value


def test_strict_deepseek_requires_both_chat_and_embedding_keys(
    config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "tavily")
    monkeypatch.setenv("OPENAI_API_KEY", "embeddings")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    config_path.write_text(yaml.safe_dump({}), encoding="utf-8")

    with pytest.raises(MissingSecretsError, match="DEEPSEEK_API_KEY"):
        load_config(str(config_path), strict=True)


def test_strict_openai_does_not_require_deepseek_key(
    config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "openai")
    monkeypatch.setenv("TAVILY_API_KEY", "tavily")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "openai")

    assert load_config(str(config_path), strict=True).llm.provider == "openai"
```

Also add `DEEPSEEK_API_KEY` to the serialization leak test's secret values and assert it is absent from `settings.model_dump()`.

- [ ] **Step 6: Make existing OpenAI-specific fixtures explicit**

In `tests/test_openai_provider.py`, add and use this helper for every chat-provider construction that previously relied on `LLMConfig()` defaults:

```python
def openai_config(**updates: object) -> LLMConfig:
    return LLMConfig.model_validate(
        {
            "provider": "openai",
            "model": "gpt-4o",
            "thinking_mode": "disabled",
            "reasoning_effort": "none",
            **updates,
        }
    )
```

In strict-mode fixtures whose YAML is `{}`, set `DEEPSEEK_API_KEY` alongside `OPENAI_API_KEY` and `TAVILY_API_KEY`. Where a test specifically asserts OpenAI-only strict behavior, put `llm: {provider: openai, model: gpt-4o, thinking_mode: disabled, reasoning_effort: none}` in its YAML instead.

In `tests/test_config.py`'s explicit OpenAI `config_path` fixture, add `thinking_mode: disabled` and `reasoning_effort: none`. Change the existing environment-leaf case `LLM_PROVIDER=anthropic` to `LLM_PROVIDER=deepseek`; the new closed-provider rejection test now owns unsupported-value coverage.

- [ ] **Step 7: Commit the new YAML defaults**

Change only the `llm` block in `config.yaml` to:

```yaml
llm:
  provider: deepseek
  model: deepseek-v4-flash
  embedding_model: text-embedding-3-small
  thinking_mode: enabled
  reasoning_effort: high
  model_overrides: {}
  timeout: 60.0
  retry_count: 2
  temperature: 0.7
  max_tokens: 4096
```

- [ ] **Step 8: Run configuration and strict-startup tests to verify GREEN**

Run: `python -m pytest tests/test_config.py tests/test_runtime/test_run_research.py tests/test_cli/test_entrypoint.py tests/test_openai_provider.py -q`

Expected: PASS; defaults resolve to DeepSeek while explicitly configured OpenAI tests retain their existing behavior.

- [ ] **Step 9: Commit effective configuration**

```bash
git add src/deep_research/utils/config.py config.yaml tests/test_config.py tests/test_openai_provider.py tests/test_runtime/test_run_research.py tests/test_cli/test_entrypoint.py
git commit -m "feat: resolve provider reasoning configuration"
```

---

### Task 3: Fail-closed provider capability registry

**Files:**
- Create: `src/deep_research/providers/capabilities.py`
- Create: `tests/test_provider_capabilities.py`
- Modify: `src/deep_research/providers/__init__.py`

**Interfaces:**
- Consumes: `ProviderName`, `ThinkingMode`, `ReasoningEffort`, and `EffectiveModelConfig` from Task 2.
- Produces: frozen dataclass `ModelCapability(pattern: Pattern[str], thinking_modes: frozenset[ThinkingMode], enabled_efforts: frozenset[ReasoningEffort], disabled_effort: ReasoningEffort | None, temperature_modes: frozenset[ThinkingMode])`.
- Produces: frozen dataclass `ResolvedRequestSettings(effective: EffectiveModelConfig, reasoning_effort: ReasoningEffort | None, include_temperature: bool)`.
- Produces: `capability_for(provider: ProviderName, model: str) -> ModelCapability`.
- Produces: `resolve_request_settings(provider: ProviderName, effective: EffectiveModelConfig) -> ResolvedRequestSettings`.

- [ ] **Step 1: Write failing DeepSeek and fail-closed tests**

Create `tests/test_provider_capabilities.py`:

```python
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
        ("unknown", "model", "unknown"),
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
```

- [ ] **Step 2: Write failing OpenAI family/snapshot tests**

Add a parameterized table that pins the implementation-date registry:

```python
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
```

Add rejection cases for `gpt-4o/enabled`, `o3/disabled`, `o1/max`, `gpt-5.4/max`, `gpt-5.5/minimal`, `gpt-5.5-pro/low`, and `gpt-5.6/minimal`.

- [ ] **Step 3: Run capability tests to verify RED**

Run: `python -m pytest tests/test_provider_capabilities.py -q`

Expected: FAIL because the capability module and functions do not exist.

- [ ] **Step 4: Implement exact capability metadata and matching**

Create `capabilities.py` with anchored regex entries ordered from specific to general. Use these exact behavior groups:

| Provider/model regex | Modes | Enabled efforts | Disabled sent effort | Temperature modes |
|---|---|---|---|---|
| DeepSeek `^deepseek-v4-(flash|pro)$` | enabled, disabled | high, max | omit | disabled |
| OpenAI `^gpt-4o(?:-mini)?(?:-\d{4}-\d{2}-\d{2})?$` | disabled | none | omit | disabled |
| OpenAI `^gpt-4\.1(?:-(?:mini|nano))?(?:-\d{4}-\d{2}-\d{2})?$` | disabled | none | omit | disabled |
| OpenAI `^gpt-5\.4-pro(?:-\d{4}-\d{2}-\d{2})?$` | enabled | medium, high, xhigh | omit | none |
| OpenAI `^gpt-5\.4(?:-(?:mini|nano))?(?:-\d{4}-\d{2}-\d{2})?$` | enabled, disabled | low, medium, high, xhigh | none | disabled |
| OpenAI `^gpt-5\.5-pro(?:-\d{4}-\d{2}-\d{2})?$` | enabled | medium, high, xhigh | omit | none |
| OpenAI `^gpt-5\.5(?:-\d{4}-\d{2}-\d{2})?$` | enabled, disabled | low, medium, high, xhigh | none | disabled |
| OpenAI `^gpt-5\.6(?:-(?:sol|terra|luna))?(?:-\d{4}-\d{2}-\d{2})?$` | enabled, disabled | low, medium, high, xhigh, max | none | disabled |
| OpenAI `^gpt-5\.3-codex(?:-\d{4}-\d{2}-\d{2})?$` | enabled | low, medium, high, xhigh | omit | none |
| OpenAI `^gpt-5\.2-pro(?:-\d{4}-\d{2}-\d{2})?$` | enabled | medium, high, xhigh | omit | none |
| OpenAI `^gpt-5\.2(?:-\d{4}-\d{2}-\d{2})?$` | enabled, disabled | low, medium, high, xhigh | none | disabled |
| OpenAI `^gpt-5\.1(?:-\d{4}-\d{2}-\d{2})?$` | enabled, disabled | low, medium, high | none | disabled |
| OpenAI `^gpt-5-pro(?:-\d{4}-\d{2}-\d{2})?$` | enabled | high | omit | none |
| OpenAI `^gpt-5(?:-(?:mini|nano))?(?:-\d{4}-\d{2}-\d{2})?$` | enabled | minimal, low, medium, high | omit | none |
| OpenAI `^o1(?:-\d{4}-\d{2}-\d{2})?$` | enabled | low, medium, high | omit | none |
| OpenAI `^o3(?:-\d{4}-\d{2}-\d{2})?$` | enabled | low, medium, high | omit | none |
| OpenAI `^o4-mini(?:-\d{4}-\d{2}-\d{2})?$` | enabled | low, medium, high | omit | none |

The resolver must validate in this order: model match, thinking mode, enabled effort. For disabled mode it ignores the configured dormant effort and returns the entry's `disabled_effort`. Build accepted-value text from sorted metadata; never include exception reprs or environment values. These entries cover every reasoning family in the official catalog on 2026-08-03 plus the required non-reasoning families; more-specific Pro/Codex regexes must be checked before their base-family regexes.

- [ ] **Step 5: Add a registry-maintenance guard**

Add this focused test so a future model addition remains metadata-only:

```python
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
```

- [ ] **Step 6: Run capability tests to verify GREEN**

Run: `python -m pytest tests/test_provider_capabilities.py -q`

Expected: PASS, including snapshot inheritance, exact effort subsets, temperature rules, and fail-closed error text.

- [ ] **Step 7: Commit the capability registry**

```bash
git add src/deep_research/providers/capabilities.py src/deep_research/providers/__init__.py tests/test_provider_capabilities.py
git commit -m "feat: validate provider model capabilities"
```

---

### Task 4: OpenAI Responses reasoning translation

**Files:**
- Modify: `src/deep_research/providers/openai_provider.py`
- Modify: `tests/test_openai_provider.py`

**Interfaces:**
- Consumes: `LLMConfig.resolve_for(...)` and `resolve_request_settings("openai", effective)`.
- Preserves: `OpenAIChatProvider.complete(...) -> ChatResult` and `complete_structured(...) -> schema` with native `responses.create` / `responses.parse` and exactly one existing repair.
- Adds: a private `_request_options(agent_name: str | None) -> tuple[EffectiveModelConfig, dict[str, object], dict[str, JsonValue]]` used identically by plain and structured calls.
- Changes: OpenAI model spans include provider, operation, agent name when supplied, message count, effective thinking mode, requested effort, effective sent effort when present, and structured attempt number.

- [ ] **Step 1: Write failing enabled-reasoning request tests**

Add to `tests/test_openai_provider.py`:

```python
@pytest.mark.asyncio
async def test_openai_reasoning_model_sends_resolved_effort_without_temperature() -> None:
    responses = RecordingResponses(response(text="Answer"))
    tracker = local_tracker()
    provider = OpenAIChatProvider(
        openai_config(
            model="gpt-5.6",
            thinking_mode="enabled",
            reasoning_effort="high",
        ),
        tracker,
        client=FakeOpenAIClient(responses=responses),
    )

    async with tracker.session_span("session-1", "question"):
        await provider.complete(
            [ChatMessage(role="user", content="Answer")],
            agent_name="planner",
        )

    call = responses.create_calls[0]
    assert call["reasoning"] == {"effort": "high"}
    assert "temperature" not in call


@pytest.mark.asyncio
async def test_openai_structured_call_uses_structured_agent_override() -> None:
    parsed = Outline(title="Answer", points=[])
    responses = RecordingResponses(response(parsed=parsed))
    tracker = local_tracker()
    provider = OpenAIChatProvider(
        openai_config(
            model="gpt-5.6",
            thinking_mode="enabled",
            reasoning_effort="low",
            model_overrides={"critic": {"reasoning_effort": "max"}},
        ),
        tracker,
        client=FakeOpenAIClient(responses=responses),
    )

    async with tracker.session_span("session-1", "question"):
        await provider.complete_structured(
            [ChatMessage(role="user", content="Review")],
            Outline,
            agent_name="critic",
        )

    assert responses.parse_calls[0]["model"] == "gpt-5.6"
    assert responses.parse_calls[0]["reasoning"] == {"effort": "max"}
    assert "temperature" not in responses.parse_calls[0]
```

- [ ] **Step 2: Write failing disabled/non-reasoning translation tests**

Add:

```python
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model", "expected_reasoning"),
    [("gpt-5.6", {"effort": "none"}), ("gpt-4o", None)],
)
async def test_openai_disabled_mode_sends_only_supported_controls(
    model: str, expected_reasoning: dict[str, str] | None
) -> None:
    responses = RecordingResponses(response(text="Answer"))
    tracker = local_tracker()
    provider = OpenAIChatProvider(
        openai_config(
            model=model,
            thinking_mode="disabled",
            reasoning_effort="high",
            temperature=0.25,
        ),
        tracker,
        client=FakeOpenAIClient(responses=responses),
    )

    async with tracker.session_span("session-1", "question"):
        await provider.complete([ChatMessage(role="user", content="Answer")])

    call = responses.create_calls[0]
    assert call["temperature"] == 0.25
    if expected_reasoning is None:
        assert "reasoning" not in call
    else:
        assert call["reasoning"] == expected_reasoning
```

- [ ] **Step 3: Run OpenAI translation tests to verify RED**

Run: `python -m pytest tests/test_openai_provider.py -k "reasoning_model or structured_agent_override or disabled_mode" -q`

Expected: FAIL because OpenAI always sends temperature and never sends `reasoning`.

- [ ] **Step 4: Build request options from effective capabilities**

In `OpenAIChatProvider`, add a private helper with this behavior:

```python
def _request_options(
    self, agent_name: str | None
) -> tuple[EffectiveModelConfig, dict[str, object], dict[str, JsonValue]]:
    effective = self._config.resolve_for(agent_name)
    resolved = resolve_request_settings("openai", effective)
    request: dict[str, object] = {
        "model": effective.model,
        "max_output_tokens": self._config.max_tokens,
    }
    if resolved.reasoning_effort is not None:
        request["reasoning"] = {"effort": resolved.reasoning_effort}
    if resolved.include_temperature:
        request["temperature"] = self._config.temperature
    metadata: dict[str, JsonValue] = {
        "provider": "openai",
        "thinking_mode": effective.thinking_mode,
        "requested_reasoning_effort": effective.reasoning_effort,
    }
    if agent_name is not None:
        metadata["agent_name"] = agent_name
    if resolved.reasoning_effort is not None:
        metadata["effective_reasoning_effort"] = resolved.reasoning_effort
    return effective, request, metadata
```

Call it before opening either model span; merge `input`, `operation`, `message_count`, and `attempt` into the appropriate call/span without logging message content, schema name, or schema JSON. Remove the current `schema` span-input field.

- [ ] **Step 5: Pin safe model-span metadata**

Add a test subclass that records the `inputs` mapping passed to `llm_span(...)` before delegating to the real tracker:

```python
class CapturingTracker(Tracker):
    def __init__(self) -> None:
        super().__init__(LangSmithRuntimeConfig(tracing_enabled=False))
        self.llm_inputs: list[dict[str, object]] = []

    def llm_span(self, model, inputs):
        self.llm_inputs.append(dict(inputs))
        return super().llm_span(model, inputs)
```

Use `CapturingTracker()` for a successful enabled-reasoning call and assert:

```python
serialized = json.dumps(tracker.llm_inputs[0], sort_keys=True)
assert '"provider": "openai"' in serialized
assert '"thinking_mode": "enabled"' in serialized
assert '"requested_reasoning_effort": "high"' in serialized
assert "Answer" not in serialized
```

Also assert the structured attempt metadata is `1` then `2` on the repair test and that native `text_format=schema` remains present on both calls.

- [ ] **Step 6: Verify validation happens before an OpenAI client call**

Add:

```python
@pytest.mark.asyncio
async def test_openai_rejects_unsupported_effort_before_request() -> None:
    responses = RecordingResponses(response(text="must not be consumed"))
    tracker = local_tracker()
    provider = OpenAIChatProvider(
        openai_config(
            model="gpt-5.5",
            thinking_mode="enabled",
            reasoning_effort="max",
        ),
        tracker,
        client=FakeOpenAIClient(responses=responses),
    )

    async with tracker.session_span("session-1", "question"):
        with pytest.raises(ProviderConfigurationError, match="gpt-5.5"):
            await provider.complete([ChatMessage(role="user", content="Answer")])

    assert responses.create_calls == []
```

- [ ] **Step 7: Run all OpenAI provider tests to verify GREEN**

Run: `python -m pytest tests/test_openai_provider.py -q`

Expected: PASS; native Responses structured parsing/repair and SDK error translation remain unchanged, and reasoning controls are capability-driven.

- [ ] **Step 8: Commit OpenAI reasoning support**

```bash
git add src/deep_research/providers/openai_provider.py tests/test_openai_provider.py
git commit -m "feat: translate OpenAI reasoning settings"
```

---

### Task 5: DeepSeek client and plain completion adapter

**Files:**
- Create: `src/deep_research/providers/deepseek_provider.py`
- Create: `tests/test_deepseek_provider.py`
- Modify: `src/deep_research/providers/__init__.py`

**Interfaces:**
- Consumes: neutral contracts/errors, `LLMConfig.resolve_for(...)`, `resolve_request_settings("deepseek", ...)`, `Tracker`, and lazy `openai` SDK errors.
- Produces: `DEEPSEEK_BASE_URL = "https://api.deepseek.com"`.
- Produces: `DeepSeekChatProvider(config: LLMConfig, tracker: Tracker, *, api_key: str | None = None, client: Any | None = None)`.
- Produces: `complete(messages: Sequence[ChatMessage], *, agent_name: str | None = None) -> ChatResult`.
- Produces: private `_translated_messages(...)`, `_request_options(...)`, `_usage_from_response(...)`, `_choice_text(response, *, allow_empty: bool = False)`, and `_raise_deepseek_error(...)` helpers used by Task 6.

- [ ] **Step 1: Create offline Chat Completions fakes and client-construction tests**

Start `tests/test_deepseek_provider.py` with these injected Chat Completions fakes:

```python
class RecordingCompletions:
    def __init__(self, *outcomes: object) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeDeepSeekClient:
    def __init__(self, completions: RecordingCompletions) -> None:
        self.chat = SimpleNamespace(completions=completions)


def chat_response(
    *,
    text: object = "answer",
    finish_reason: str = "stop",
    prompt_tokens: object = 4,
    completion_tokens: object = 2,
    reasoning_content: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id="deepseek-response",
        choices=[
            SimpleNamespace(
                finish_reason=finish_reason,
                message=SimpleNamespace(
                    content=text, reasoning_content=reasoning_content
                ),
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=(
                prompt_tokens + completion_tokens
                if isinstance(prompt_tokens, int)
                and not isinstance(prompt_tokens, bool)
                and isinstance(completion_tokens, int)
                and not isinstance(completion_tokens, bool)
                else None
            ),
        ),
    )


class CapturingTracker(Tracker):
    def __init__(self) -> None:
        super().__init__(LangSmithRuntimeConfig(tracing_enabled=False))
        self.llm_inputs: list[dict[str, object]] = []

    def llm_span(self, model, inputs):
        self.llm_inputs.append(dict(inputs))
        return super().llm_span(model, inputs)
```

Use `CapturingTracker` for metadata allowlist assertions. Add:

```python
def deepseek_config(**updates: object) -> LLMConfig:
    return LLMConfig.model_validate(
        {
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "thinking_mode": "enabled",
            "reasoning_effort": "high",
            **updates,
        }
    )


def test_deepseek_requires_key_without_injected_client(monkeypatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(ProviderConfigurationError, match="DEEPSEEK_API_KEY"):
        DeepSeekChatProvider(deepseek_config(), local_tracker())


def test_explicit_blank_deepseek_key_does_not_fall_back(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "environment-key")
    with pytest.raises(ProviderConfigurationError, match="DEEPSEEK_API_KEY"):
        DeepSeekChatProvider(deepseek_config(), local_tracker(), api_key="")


def test_deepseek_builds_openai_compatible_client_with_code_owned_url(
    monkeypatch,
) -> None:
    constructed: list[dict[str, object]] = []

    class RecordingAsyncOpenAI:
        def __init__(self, **kwargs):
            constructed.append(kwargs)

    monkeypatch.setattr(deepseek_module._openai_errors(), "AsyncOpenAI", RecordingAsyncOpenAI)
    DeepSeekChatProvider(deepseek_config(), local_tracker(), api_key="deepseek-key")

    assert constructed == [
        {
            "api_key": "deepseek-key",
            "base_url": DEEPSEEK_BASE_URL,
            "timeout": 60.0,
            "max_retries": 2,
        }
    ]
```

The injected-client test must assert no environment key is required and the exact injected object is retained.

- [ ] **Step 2: Run client tests to verify RED**

Run: `python -m pytest tests/test_deepseek_provider.py -k "requires_key or blank_deepseek or code_owned_url or injected" -q`

Expected: FAIL because the DeepSeek module/adapter do not exist.

- [ ] **Step 3: Implement lazy client construction**

In `deepseek_provider.py`, lazily import `AsyncOpenAI`, `APITimeoutError`, `RateLimitError`, `APIConnectionError`, `APIStatusError`, and `OpenAIError` into a cached `SimpleNamespace`, exactly as the current OpenAI adapter does. Resolve the key with:

```python
resolved_key = os.getenv("DEEPSEEK_API_KEY", "") if api_key is None else api_key
```

Injected clients bypass key resolution. Non-injected clients use the exact base URL and retry/timeout fields in the preceding test.

- [ ] **Step 4: Write failing message/thinking/plain-response tests**

Add tests using `SimpleNamespace` responses shaped as one Chat Completions choice:

```python
@pytest.mark.asyncio
async def test_deepseek_plain_completion_translates_roles_and_thinking() -> None:
    completions = RecordingCompletions(
        chat_response(text="  concise answer  ", prompt_tokens=8, completion_tokens=3)
    )
    tracker = local_tracker()
    provider = DeepSeekChatProvider(
        deepseek_config(), tracker, client=FakeDeepSeekClient(completions)
    )

    async with tracker.session_span("session-1", "question"):
        result = await provider.complete(
            [
                ChatMessage(role="developer", content="policy"),
                ChatMessage(role="user", content="question"),
            ],
            agent_name="planner",
        )

    assert result.text == "concise answer"
    assert result.model == "deepseek-v4-flash"
    assert result.usage.model_dump() == {
        "input_tokens": 8,
        "output_tokens": 3,
        "total_tokens": 11,
    }
    call = completions.calls[0]
    assert call["messages"] == [
        {"role": "system", "content": "policy"},
        {"role": "user", "content": "question"},
    ]
    assert call["extra_body"] == {"thinking": {"type": "enabled"}}
    assert call["reasoning_effort"] == "high"
    assert call["max_tokens"] == 4096
    assert "temperature" not in call


@pytest.mark.asyncio
async def test_deepseek_disabled_thinking_omits_effort_and_sends_temperature() -> None:
    completions = RecordingCompletions(chat_response(text="answer"))
    tracker = local_tracker()
    provider = DeepSeekChatProvider(
        deepseek_config(thinking_mode="disabled", reasoning_effort="max", temperature=0.2),
        tracker,
        client=FakeDeepSeekClient(completions),
    )

    async with tracker.session_span("session-1", "question"):
        await provider.complete([ChatMessage(role="user", content="question")])

    call = completions.calls[0]
    assert call["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "reasoning_effort" not in call
    assert call["temperature"] == 0.2
```

- [ ] **Step 5: Implement request translation and plain response parsing**

Build request dictionaries with `model`, ordered translated messages, `max_tokens`, the explicit thinking toggle, conditional `reasoning_effort`, and conditional `temperature`. Map only `developer` to `system`; pass `system`, `user`, and `assistant` through unchanged without reordering. Require `choices` to be a sequence of length one, `choice.message.content` to be a non-blank string, and `finish_reason == "stop"`. Return project-owned `ChatResult` only.

Usage mapping accepts absent usage as zero tokens, but when usage exists it requires non-negative integer `prompt_tokens` and `completion_tokens`; reject booleans, strings, negatives, and a contradictory `total_tokens` with `ProviderResponseError("DeepSeek response contained malformed usage")`.

- [ ] **Step 6: Test malformed shapes and terminal finish reasons**

Add parameterized cases for no `choices`, two choices, absent message, `None`/blank/non-string content, and finish reasons `length`, `content_filter`, and `insufficient_system_resource`. Assert each raises `ProviderResponseError`, performs exactly one request, and does not include response text in `str(error)`.

- [ ] **Step 7: Test SDK error translation with real exception classes**

Mirror the OpenAI adapter's injected-error matrix using requests to `https://api.deepseek.com`:

```python
@pytest.mark.parametrize(
    ("raised", "expected"),
    [
        (APITimeoutError(request=httpx.Request("POST", DEEPSEEK_BASE_URL)), ProviderTimeoutError),
        (RateLimitError("limited", response=httpx.Response(429, request=httpx.Request("POST", DEEPSEEK_BASE_URL)), body=None), ProviderRateLimitError),
        (APIConnectionError(request=httpx.Request("POST", DEEPSEEK_BASE_URL)), ProviderResponseError),
        (APIStatusError("bad", response=httpx.Response(401, request=httpx.Request("POST", DEEPSEEK_BASE_URL)), body=None), ProviderResponseError),
    ],
)
@pytest.mark.asyncio
async def test_deepseek_plain_translates_sdk_errors(raised, expected) -> None:
    completions = RecordingCompletions(raised)
    tracker = local_tracker()
    provider = DeepSeekChatProvider(
        deepseek_config(), tracker, client=FakeDeepSeekClient(completions)
    )
    async with tracker.session_span("session-1", "question"):
        with pytest.raises(expected):
            await provider.complete([ChatMessage(role="user", content="question")])
```

Map status/authentication/invalid-request errors to safe status-only `ProviderResponseError`; map generic `OpenAIError` to `ProviderResponseError("DeepSeek chat request failed")`.

- [ ] **Step 8: Verify safe plain-call observability**

Assert the LLM metric uses model `deepseek-v4-flash`, maps 8/3/11 tokens, and records typed failure on malformed output. Serialize tracker events/errors/metrics and assert absence of the API key, `policy`, `question`, provider answer, and a fake `reasoning_content` placed on the response object. Span inputs must include only safe fields from Global Constraints.

- [ ] **Step 9: Run the DeepSeek plain adapter suite to verify GREEN**

Run: `python -m pytest tests/test_deepseek_provider.py -k "not structured and not repair and not json" -q`

Expected: PASS with no network access.

- [ ] **Step 10: Commit the plain DeepSeek adapter**

```bash
git add src/deep_research/providers/deepseek_provider.py src/deep_research/providers/__init__.py tests/test_deepseek_provider.py
git commit -m "feat: add DeepSeek plain chat adapter"
```

---

### Task 6: DeepSeek structured JSON, repair, and telemetry safety

**Files:**
- Modify: `src/deep_research/providers/deepseek_provider.py`
- Modify: `tests/test_deepseek_provider.py`

**Interfaces:**
- Produces: `complete_structured(messages: Sequence[ChatMessage], schema: type[SchemaT], *, agent_name: str | None = None) -> SchemaT`.
- Produces: deterministic `_json_instruction(schema: type[BaseModel]) -> ChatMessage` using canonical sorted compact schema JSON.
- Produces: private `_StructuredValidationFailure` carrying validation diagnostics for the repair prompt only; its string and final public error never include provider output.
- Reuses: the same request options, response-shape validation, operational error mapping, usage mapping, and safe metadata as plain completion.

- [ ] **Step 1: Write failing first-attempt JSON success test**

Add a small schema and test:

```python
class TinyAnswer(BaseModel):
    answer: str
    confidence: int


@pytest.mark.asyncio
async def test_deepseek_structured_output_prompts_json_and_validates_locally() -> None:
    completions = RecordingCompletions(
        chat_response(text='{"answer":"yes","confidence":9}')
    )
    tracker = local_tracker()
    provider = DeepSeekChatProvider(
        deepseek_config(), tracker, client=FakeDeepSeekClient(completions)
    )

    async with tracker.session_span("session-1", "question"):
        result = await provider.complete_structured(
            [ChatMessage(role="user", content="decide")], TinyAnswer
        )

    assert result == TinyAnswer(answer="yes", confidence=9)
    call = completions.calls[0]
    assert call["response_format"] == {"type": "json_object"}
    assert call["messages"][-1]["role"] == "system"
    instruction = call["messages"][-1]["content"]
    assert "JSON" in instruction
    assert json.dumps(
        TinyAnswer.model_json_schema(), sort_keys=True, separators=(",", ":")
    ) in instruction
```

- [ ] **Step 2: Write failing repair and exhaustion tests**

Add:

```python
@pytest.mark.asyncio
async def test_deepseek_structured_repairs_once_then_succeeds() -> None:
    completions = RecordingCompletions(
        chat_response(text='{"answer":3}'),
        chat_response(text='{"answer":"yes","confidence":8}'),
    )
    tracker = local_tracker()
    provider = DeepSeekChatProvider(
        deepseek_config(), tracker, client=FakeDeepSeekClient(completions)
    )

    async with tracker.session_span("session-1", "question"):
        result = await provider.complete_structured(
            [ChatMessage(role="user", content="decide")], TinyAnswer
        )

    assert result.confidence == 8
    assert len(completions.calls) == 2
    assert "previous JSON response failed TinyAnswer validation" in completions.calls[1]["messages"][-1]["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize("first", ["", "not-json", '{"answer":3}'])
async def test_deepseek_structured_raises_after_exactly_one_failed_repair(first: str) -> None:
    completions = RecordingCompletions(
        chat_response(text=first), chat_response(text="still invalid")
    )
    tracker = local_tracker()
    provider = DeepSeekChatProvider(
        deepseek_config(), tracker, client=FakeDeepSeekClient(completions)
    )

    async with tracker.session_span("session-1", "question"):
        with pytest.raises(StructuredOutputError, match="TinyAnswer") as caught:
            await provider.complete_structured(
                [ChatMessage(role="user", content="decide")], TinyAnswer
            )

    assert len(completions.calls) == 2
    assert first not in str(caught.value)
```

- [ ] **Step 3: Run structured tests to verify RED**

Run: `python -m pytest tests/test_deepseek_provider.py -k "structured" -q`

Expected: FAIL because `complete_structured` is absent.

- [ ] **Step 4: Implement deterministic JSON instructions and local validation**

Use this canonical instruction source:

```python
schema_json = json.dumps(
    schema.model_json_schema(), sort_keys=True, separators=(",", ":")
)
content = (
    "Return only one JSON object that validates against this JSON Schema. "
    "Do not add Markdown or explanatory text. JSON Schema:\n"
    f"{schema_json}"
)
```

Append it as a `system` message after translating the caller's ordered messages. Pass `response_format={"type": "json_object"}`. Parse with `schema.model_validate_json(text)` and catch `json.JSONDecodeError` plus `pydantic.ValidationError` as repairable validation failures.

- [ ] **Step 5: Implement exactly one repair without operational retries**

On the first validation failure, append a deterministic system repair instruction containing the schema name, canonical schema, and a sanitized validation summary capped at 1,000 characters. Do not include the invalid provider output. Make one second Chat Completions request. On the second validation failure, raise:

```python
StructuredOutputError(
    f"DeepSeek output failed {schema.__name__} validation after one repair attempt"
)
```

Call `_choice_text(response, allow_empty=True)` in the structured path so an empty/blank first response becomes a repairable validation failure. Plain `complete(...)` keeps `allow_empty=False`, where the same response is an operational `ProviderResponseError`.

- [ ] **Step 6: Prove operational responses are not repaired**

Add parameterized cases where the first structured request raises timeout, rate limit, connection, and status errors, or returns `finish_reason` values `length`, `content_filter`, and `insufficient_system_resource`. Assert the shared typed error and `len(completions.calls) == 1` for every case.

- [ ] **Step 7: Pin attempt metadata and secret/content exclusion**

For a repair-success call, assert two token metrics with attempt outcomes `[False, True]` and safe started-span inputs containing `operation="structured_output"`, attempt `1`/`2`, model, provider, agent name, mode, efforts, and message count only. Serialize all tracker surfaces and public errors, then assert absence of:

```python
sensitive_values = (
    "deepseek-secret",
    "decide",
    '{"answer":3}',
    '{"answer":"yes","confidence":8}',
    "hidden chain of thought",
    "previous JSON response failed",
)
```

Do not put schema name/schema JSON in span inputs; the operation and attempt number are sufficient telemetry.

- [ ] **Step 8: Run the complete DeepSeek adapter suite to verify GREEN**

Run: `python -m pytest tests/test_deepseek_provider.py -q`

Expected: PASS; all calls use injected clients and at most two structured requests.

- [ ] **Step 9: Commit structured DeepSeek output**

```bash
git add src/deep_research/providers/deepseek_provider.py tests/test_deepseek_provider.py
git commit -m "feat: validate DeepSeek structured output"
```

---

### Task 7: Chat factory, six-agent preflight, and provider-neutral runtime assembly

**Files:**
- Create: `src/deep_research/providers/factory.py`
- Create: `tests/test_provider_factory.py`
- Modify: `src/deep_research/providers/__init__.py`
- Modify: `src/deep_research/runtime/assembly.py`
- Modify: `src/deep_research/runtime/errors.py`
- Modify: `src/deep_research/runtime/__init__.py`
- Modify: `tests/test_runtime/test_assembly.py`
- Modify: `tests/test_runtime/test_errors.py`
- Modify: `tests/test_imports.py`

**Interfaces:**
- Produces: `ChatAdapter = OpenAIChatProvider | DeepSeekChatProvider`.
- Produces: `build_chat_provider(config: LLMConfig, tracker: Tracker) -> ChatAdapter`.
- Produces: `validate_agent_model_configs(config: LLMConfig, agent_names: Sequence[str]) -> dict[str, ResolvedRequestSettings]`; resolves each agent exactly once and returns an insertion-ordered mapping useful to tests.
- Changes: `build_runtime(...)` validates all `AGENT_NAMES` before memory/tool/graph construction and uses `build_chat_provider` only when `chat_provider` is not injected.
- Preserves: `OpenAIEmbeddingProvider(model=settings.llm.embedding_model)` independent of chat factory selection.

- [ ] **Step 1: Write failing factory-selection tests**

Create `tests/test_provider_factory.py`:

```python
import pytest

import deep_research.providers.factory as factory
from deep_research.providers import (
    DeepSeekChatProvider,
    OpenAIChatProvider,
    ProviderConfigurationError,
    build_chat_provider,
)
from deep_research.utils.config import LLMConfig


@pytest.mark.parametrize(
    ("provider_name", "model", "expected"),
    [
        ("deepseek", "deepseek-v4-flash", DeepSeekChatProvider),
        ("openai", "gpt-4o", OpenAIChatProvider),
    ],
)
def test_factory_builds_exactly_the_selected_adapter(
    provider_name, model, expected, tracker, monkeypatch
) -> None:
    built: list[type[object]] = []

    class RecordingAdapter:
        def __init__(self, config, received_tracker):
            built.append(expected)
            assert config.provider == provider_name
            assert received_tracker is tracker

    monkeypatch.setattr(factory, expected.__name__, RecordingAdapter)
    config = LLMConfig(
        provider=provider_name,
        model=model,
        thinking_mode="disabled" if provider_name == "openai" else "enabled",
        reasoning_effort="none" if provider_name == "openai" else "high",
    )

    result = build_chat_provider(config, tracker)

    assert isinstance(result, RecordingAdapter)
    assert built == [expected]
```

The defensive unknown-provider test may bypass Pydantic with a minimal object carrying `provider="other"`; assert `ProviderConfigurationError` names `other` and accepted values `deepseek, openai`.

- [ ] **Step 2: Run factory tests to verify RED**

Run: `python -m pytest tests/test_provider_factory.py -q`

Expected: FAIL because the factory module and export do not exist.

- [ ] **Step 3: Implement the small explicit factory**

Create `factory.py` with no model-name/key inference and no fallback:

```python
ChatAdapter: TypeAlias = OpenAIChatProvider | DeepSeekChatProvider


def build_chat_provider(config: LLMConfig, tracker: Tracker) -> ChatAdapter:
    if config.provider == "deepseek":
        return DeepSeekChatProvider(config, tracker)
    if config.provider == "openai":
        return OpenAIChatProvider(config, tracker)
    raise ProviderConfigurationError(
        f"Unsupported chat provider {config.provider!r}; accepted values: deepseek, openai"
    )
```

Export it and `ChatAdapter` from `providers/__init__.py`.

- [ ] **Step 4: Write failing all-six validation test**

Add to `tests/test_runtime/test_assembly.py`:

```python
def test_validate_agent_models_resolves_all_six_before_runtime() -> None:
    config = LLMConfig(
        model_overrides={
            "critic": {"model": "deepseek-v4-pro", "reasoning_effort": "max"}
        }
    )

    resolved = validate_agent_model_configs(config, AGENT_NAMES)

    assert tuple(resolved) == AGENT_NAMES
    assert resolved["planner"].effective.model == "deepseek-v4-flash"
    assert resolved["critic"].effective.model == "deepseek-v4-pro"
    assert resolved["critic"].reasoning_effort == "max"


@pytest.mark.asyncio
async def test_bad_critic_override_fails_before_any_runtime_collaborator(
    tracker, monkeypatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        assembly,
        "OpenAIEmbeddingProvider",
        lambda **_kwargs: calls.append("embeddings"),
    )
    settings = ConfigSettings.model_validate(
        {"llm": {"model_overrides": {"critic": {"reasoning_effort": "medium"}}}}
    )

    with pytest.raises(ResearchConfigurationError) as caught:
        await build_runtime(settings, session_id="session-1", tracker=tracker)

    assert caught.value.reason == "provider_unconfigured"
    assert "deepseek" in str(caught.value)
    assert "critic" in str(caught.value)
    assert calls == []
```

- [ ] **Step 5: Implement six-agent preflight before collaborator construction**

Define `validate_agent_model_configs` in `providers/factory.py` using `config.resolve_for(name)` and `resolve_request_settings(config.provider, effective)`. In `build_runtime`, call it immediately after tracker creation and before the `try` that constructs long-term/procedural memory. Wrap `ProviderConfigurationError` as:

```python
raise configuration_error(
    reason="provider_unconfigured",
    message=(
        f"The selected {settings.llm.provider} chat provider is not configured: "
        f"{error}"
    ),
) from error
```

When adding agent context to a validation error, use `agent {name!r}` plus the already-safe capability message; do not include the serialized override object.

- [ ] **Step 6: Test selected factory wiring, no fallback, and embeddings separation**

Add runtime tests that monkeypatch `assembly.build_chat_provider` and `assembly.OpenAIEmbeddingProvider`:

```python
@pytest.mark.asyncio
async def test_deepseek_chat_still_builds_openai_embeddings(
    tracker, tmp_path, monkeypatch
) -> None:
    built: list[tuple[str, object]] = []
    provider = RecordingProvider()
    embeddings = FakeEmbeddings()
    monkeypatch.setattr(
        assembly,
        "build_chat_provider",
        lambda config, received_tracker: built.append((config.provider, received_tracker)) or provider,
    )
    monkeypatch.setattr(
        assembly,
        "OpenAIEmbeddingProvider",
        lambda *, model: built.append(("embedding", model)) or embeddings,
    )
    monkeypatch.setattr(
        assembly.LongTermMemory,
        "from_config",
        lambda config, *, embeddings, tracker: LongTermMemory(
            collection=FakeCollection(), embeddings=embeddings
        ),
    )

    await build_runtime(
        ConfigSettings.model_validate(
            {"output": {"directory": str(tmp_path)}}
        ),
        session_id="session-1",
        tracker=tracker,
        procedural=ProceduralMemory(tmp_path / "strategies.json"),
        search_client=FakeSearchClient(),
    )

    assert built[0] == ("embedding", "text-embedding-3-small")
    assert built[1] == ("deepseek", tracker)
```

Add a factory failure test asserting `build_chat_provider` is called once and `OpenAIChatProvider` is never constructed after DeepSeek failure. This is the no-cross-provider-fallback guard.

- [ ] **Step 7: Keep injected provider tests offline and prevalidated**

Retain the existing `chat_provider` injection seam. Validation still runs for all six configured effective models, but factory construction/key lookup is skipped when the injection is present. Update any runtime test that specifically expects a missing OpenAI key to use explicit valid OpenAI settings:

```python
"llm": {
    "provider": "openai",
    "model": "gpt-4o",
    "thinking_mode": "disabled",
    "reasoning_effort": "none",
}
```

- [ ] **Step 8: Make runtime hints provider-neutral**

Change `CONFIGURATION_HINTS["missing_secrets"]` to direct users to set the keys required by the selected provider plus OpenAI embeddings and Tavily, and change `provider_unconfigured` to direct users to check the selected provider/model/reasoning settings and selected provider key. Update CLI/runtime hint assertions so neither hard-codes OpenAI as the chat provider.

- [ ] **Step 9: Update public import tests**

Extend `tests/test_imports.py` to import `DeepSeekChatProvider`, `DEEPSEEK_BASE_URL`, `ProviderError`, `build_chat_provider`, `capability_for`, `resolve_request_settings`, and `validate_agent_model_configs`. Keep the existing OpenAI chat/embedding and compatibility-alias imports.

- [ ] **Step 10: Run factory/runtime regression tests to verify GREEN**

Run: `python -m pytest tests/test_provider_factory.py tests/test_runtime/test_assembly.py tests/test_runtime/test_errors.py tests/test_graph/test_nodes.py tests/test_imports.py -q`

Expected: PASS; invalid agent-only overrides fail before memory/provider/graph construction, selected chat adapter is unique, and embeddings remain OpenAI.

- [ ] **Step 11: Commit runtime provider selection**

```bash
git add src/deep_research/providers/factory.py src/deep_research/providers/__init__.py src/deep_research/runtime/assembly.py src/deep_research/runtime/errors.py src/deep_research/runtime/__init__.py tests/test_provider_factory.py tests/test_runtime/test_assembly.py tests/test_runtime/test_errors.py tests/test_imports.py
git commit -m "feat: select and preflight chat providers"
```

---

### Task 8: Explicitly opt-in DeepSeek live smoke test

**Files:**
- Modify: `pyproject.toml`
- Create: `tests/live/test_deepseek_live.py`

**Interfaces:**
- Produces: pytest marker `live: makes an explicitly opted-in external provider request`.
- Changes: normal pytest `addopts` to `-m "not live"`.
- Requires for execution: explicit `-o addopts=` plus `-m live`, `RUN_DEEPSEEK_LIVE_TESTS=1`, and a non-blank `DEEPSEEK_API_KEY`.
- Does not consume: `OPENAI_API_KEY` or `TAVILY_API_KEY` because this tests only the adapter boundary.

- [ ] **Step 1: Register and default-deselect the marker**

Change the pytest configuration to:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = ["test_*.py"]
addopts = "-m 'not live'"
markers = [
    "live: makes an explicitly opted-in external provider request",
]
```

- [ ] **Step 2: Write the guarded bounded smoke test**

Create `tests/live/test_deepseek_live.py`:

```python
from __future__ import annotations

import os

import pytest
from pydantic import BaseModel

from deep_research.observability import (
    LangSmithRuntimeConfig,
    TokenUsageMetric,
    Tracker,
)
from deep_research.providers import ChatMessage, DeepSeekChatProvider
from deep_research.utils.config import LLMConfig


class LiveTinyAnswer(BaseModel):
    value: int


@pytest.mark.live
@pytest.mark.asyncio
async def test_deepseek_structured_adapter_live() -> None:
    if os.getenv("RUN_DEEPSEEK_LIVE_TESTS") != "1":
        pytest.skip("set RUN_DEEPSEEK_LIVE_TESTS=1 to opt in")
    if not os.getenv("DEEPSEEK_API_KEY", "").strip():
        pytest.skip("DEEPSEEK_API_KEY is required for the live smoke test")

    tracker = Tracker(LangSmithRuntimeConfig(tracing_enabled=False))
    provider = DeepSeekChatProvider(
        LLMConfig(
            provider="deepseek",
            model="deepseek-v4-flash",
            thinking_mode="enabled",
            reasoning_effort="high",
            timeout=30.0,
            retry_count=0,
            max_tokens=256,
        ),
        tracker,
    )

    async with tracker.session_span("deepseek-live-smoke", "bounded adapter check"):
        result = await provider.complete_structured(
            [
                ChatMessage(
                    role="user",
                    content="Return a JSON object with value equal to 7.",
                )
            ],
            LiveTinyAnswer,
        )

    assert result == LiveTinyAnswer(value=7)
    metric = next(
        metric for metric in tracker.metrics if isinstance(metric, TokenUsageMetric)
    )
    assert metric.input_tokens >= 0
    assert metric.output_tokens > 0
    assert metric.total_tokens == metric.input_tokens + metric.output_tokens
```

Do not print the key, prompt, raw response, or reasoning content.

- [ ] **Step 3: Verify normal selection excludes the test**

Run: `python -m pytest --collect-only -q`

Expected: collection succeeds with the live test deselected by the configured `not live` expression and no network request.

- [ ] **Step 4: Verify explicit selection safely skips without opt-in**

Run: `python -m pytest -o addopts= -m live tests/live/test_deepseek_live.py -q`

Expected: SKIP because `RUN_DEEPSEEK_LIVE_TESTS` is not exactly `1` (or because the key is blank); zero provider requests.

- [ ] **Step 5: Commit the smoke-test boundary**

```bash
git add pyproject.toml tests/live/test_deepseek_live.py
git commit -m "test: add opt-in DeepSeek live smoke"
```

---

### Task 9: Configuration examples, setup, migration, and live-test documentation

**Files:**
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `src/deep_research/providers/__init__.py`
- Modify: `src/deep_research/agents/base.py`

**Interfaces:**
- Documents: DeepSeek default chat, OpenAI selectable chat, OpenAI-only embeddings, global/agent reasoning settings, strict secret matrix, migration from old OpenAI defaults, no fallback, and safe observability.
- Documents: exact PowerShell and POSIX live-smoke commands matching Task 8.
- Preserves: blank secret placeholders only.

- [ ] **Step 1: Update the blank environment example**

Replace the provider portion of `.env.example` with:

```dotenv
# Required for default DeepSeek chat
DEEPSEEK_API_KEY=

# Required in every mode for OpenAI embeddings; also used for OpenAI chat
OPENAI_API_KEY=

# Optional chat configuration overrides
LLM_PROVIDER=deepseek
LLM_MODEL=deepseek-v4-flash
LLM_THINKING_MODE=enabled
LLM_REASONING_EFFORT=high
LLM_EMBEDDING_MODEL=text-embedding-3-small
LLM_TIMEOUT=60.0
LLM_RETRY_COUNT=2
```

Retain blank LangSmith/Tavily values and comments. Run `rg -n "(sk-|key-[A-Za-z0-9]|API_KEY=.+)" .env.example` and expect no real-looking or non-blank secret assignment.

- [ ] **Step 2: Rewrite the provider documentation around the factory**

Rename `## OpenAI Providers` to `## Chat and Embedding Providers`. Document:

- the committed DeepSeek/V4 Flash/enabled/high defaults;
- `LLM_PROVIDER`, `LLM_MODEL`, `LLM_THINKING_MODE`, `LLM_REASONING_EFFORT`, `LLM_EMBEDDING_MODEL`, timeout, and retries;
- one DeepSeek YAML example with a string planner override and structured critic max-effort override;
- one complete OpenAI switch using provider `openai`, model `gpt-5.6`, thinking `enabled`, effort `high`;
- `build_chat_provider(settings.llm, tracker)` rather than direct default adapter construction;
- OpenAI embeddings remain active in DeepSeek mode;
- unknown/unsupported combinations fail before any request and there is no cross-provider fallback.

Use this configuration example verbatim:

```yaml
llm:
  provider: deepseek
  model: deepseek-v4-flash
  thinking_mode: enabled
  reasoning_effort: high
  model_overrides:
    planner: deepseek-v4-flash
    critic:
      model: deepseek-v4-flash
      thinking_mode: enabled
      reasoning_effort: max
```

- [ ] **Step 3: Document the strict secret matrix and migration**

Add this table:

```markdown
| Selected chat provider | Required for a full research run |
| --- | --- |
| DeepSeek | `DEEPSEEK_API_KEY`, `OPENAI_API_KEY` (embeddings), `TAVILY_API_KEY` |
| OpenAI | `OPENAI_API_KEY`, `TAVILY_API_KEY` |
```

State that LangSmith requirements remain conditional on tracing. Add a migration note: existing OpenAI users must explicitly set `provider: openai`, an OpenAI model, and a compatible thinking/effort pair after this intentional default change; legacy string model overrides remain valid.

- [ ] **Step 4: Document exact live smoke commands**

Add PowerShell:

```powershell
$env:RUN_DEEPSEEK_LIVE_TESTS="1"
python -m pytest -o addopts= -m live tests/live/test_deepseek_live.py -v
```

and POSIX:

```bash
RUN_DEEPSEEK_LIVE_TESTS=1 python -m pytest -o addopts= -m live tests/live/test_deepseek_live.py -v
```

State that `DEEPSEEK_API_KEY` must already be set, the test makes one bounded structured adapter call with at most one repair, and it does not use embeddings or Tavily. State normal `python -m pytest` excludes it.

- [ ] **Step 5: Remove remaining OpenAI-only descriptions**

Update the package and `StructuredCompleter` descriptions so they refer to selectable chat providers and OpenAI embeddings. Run:

`rg -n "OpenAI only|OpenAI provider boundary|default OpenAI|OpenAI chat/embedding providers" README.md src config.yaml .env.example`

Expected: no stale statement that chat is OpenAI-only; historical phase wording may remain only if rewritten to be accurate.

- [ ] **Step 6: Run documentation/config consistency checks**

Run: `python -m pytest tests/test_config.py tests/test_imports.py -q`

Expected: PASS; committed defaults and public descriptions match exported behavior.

- [ ] **Step 7: Commit documentation and examples**

```bash
git add .env.example README.md src/deep_research/providers/__init__.py src/deep_research/agents/base.py
git commit -m "docs: explain DeepSeek provider configuration"
```

---

### Task 10: Full offline regression and safety verification

**Files:**
- Modify only files already listed if a verification failure exposes a feature regression; do not weaken assertions or enable network access.

**Interfaces:**
- Verifies: every acceptance criterion and every Global Constraint.
- Produces: no new public API.

- [ ] **Step 1: Run the full default offline suite**

Run: `python -m pytest -q`

Expected: PASS with the live test deselected and no DeepSeek, OpenAI, Tavily, LangSmith, or ChromaDB network request.

- [ ] **Step 2: Run focused provider/config/runtime suites verbosely**

Run: `python -m pytest tests/test_provider_contracts.py tests/test_provider_capabilities.py tests/test_deepseek_provider.py tests/test_openai_provider.py tests/test_provider_factory.py tests/test_config.py tests/test_runtime/test_assembly.py -v`

Expected: PASS; output shows plain/structured, error mapping, all-six validation, conditional secrets, both factory branches, and reasoning translation tests.

- [ ] **Step 3: Reconfirm the live boundary without spending money**

Run: `python -m pytest -o addopts= -m live tests/live/test_deepseek_live.py -q`

Expected: SKIP unless the developer independently set both opt-in controls. Do not set them as part of normal verification.

- [ ] **Step 4: Run Ruff over production and tests**

Run: `python -m ruff check src tests`

Expected: `All checks passed!`

- [ ] **Step 5: Scan committed surfaces for secret or raw-reasoning hazards**

Run: `rg -n "(sk-[A-Za-z0-9_-]+|DEEPSEEK_API_KEY=.+|OPENAI_API_KEY=.+|reasoning_content|chain.of.thought)" . --glob '!docs/superpowers/specs/**' --glob '!docs/superpowers/plans/**' --glob '!.git/**'`

Expected: only blank example assignments, deliberate negative-test fixtures, and code/test assertions that exclude `reasoning_content`; no real key, captured prompt/response logging, or persisted reasoning field.

- [ ] **Step 6: Verify no provider fallback or provider inference exists**

Run: `rg -n "fallback|infer.*provider|provider.*model.*startswith|DEEPSEEK_API_KEY.*OPENAI_API_KEY|OPENAI_API_KEY.*DEEPSEEK_API_KEY" src/deep_research/providers src/deep_research/runtime`

Expected: no adapter fallback/inference branch. A documentation comment or test name about the prohibited fallback is acceptable; runtime selection must depend only on `config.provider`.

- [ ] **Step 7: Review the final diff against the approved spec**

Run: `git diff --check`

Expected: no whitespace errors. Then inspect `git diff --stat` and confirm changes are limited to the file map, with no approved-spec edit, persisted-state change, CLI/API schema change, or dependency addition.

- [ ] **Step 8: Commit any verification-only corrections**

If Steps 1-7 required a code/test/doc correction, stage only those corrected files and commit:

```bash
git add src tests README.md config.yaml .env.example pyproject.toml
git commit -m "fix: close DeepSeek provider regressions"
```

If no correction was needed, do not create an empty commit; record the passing commands in the implementation handoff.

---

## Implementation Reference Notes

- DeepSeek uses `AsyncOpenAI(..., base_url="https://api.deepseek.com")` and `client.chat.completions.create(...)`; OpenAI chat remains on `client.responses.create(...)` and `client.responses.parse(...)`.
- DeepSeek thinking request shape is `extra_body={"thinking": {"type": "enabled"}}` plus `reasoning_effort="high"` or `"max"`; disabled mode sends the disabled toggle, omits effort, and may send temperature.
- OpenAI GPT-5.1/5.2/5.4/5.5 base families support their documented no-reasoning `none` value and documented enabled subsets; GPT-5.4/5.5 Pro variants support enabled `medium|high|xhigh`. GPT-5.6 supports no-reasoning via `none` plus enabled `low|medium|high|xhigh|max`; GPT-5.3 Codex supports enabled `low|medium|high|xhigh`; GPT-5 base/mini/nano support enabled `minimal|low|medium|high`, while GPT-5 Pro supports only `high`. Required o-series entries support enabled `low|medium|high` and have no disabled mode.
- Keep the capability registry source-linked in code comments to the approved spec references and current official model pages. Registry behavior is snapshot-tested; future model support is one metadata entry plus focused tests.
- DeepSeek JSON mode guarantees JSON syntax, not schema conformance. The Pydantic call is the application authority, and invalid JSON/schema content gets one repair only. DeepSeek-reported completion tokens may already include reasoning tokens; persist only that aggregate output count, never reasoning text.
- `Tracker.llm_span(...)` inputs are emitted to local and optional remote observability. Treat its input mapping as a strict allowlist of safe metadata; never pass translated messages, schema JSON, repair text, output text, or SDK response objects.
