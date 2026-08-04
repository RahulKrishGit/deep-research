# DeepSeek Provider and Model Reasoning Design

Date: 2026-08-03
Status: Design approved; written spec pending review

## Goal

Add DeepSeek V4 Flash as the default chat provider for deep research while
preserving OpenAI as a selectable chat provider and as the embedding provider.
Make model selection, thinking mode, and reasoning effort configurable at the
shared default and per-agent levels.

## Decisions

- DeepSeek is the default chat provider.
- `deepseek-v4-flash` is the default chat model.
- OpenAI remains available through configuration and continues to provide all
  embeddings.
- Provider and model selection use the existing YAML, environment, and
  request-override configuration paths. No new CLI or HTTP parameters are
  added.
- Thinking is enabled by default with `high` effort.
- Model and reasoning settings have global defaults and optional per-agent
  overrides.
- Unsupported provider, model, thinking-mode, and effort combinations fail
  before the first model request.
- Provider failures do not trigger an automatic cross-provider fallback.
- Normal tests remain offline. A separate opt-in smoke test may call DeepSeek
  when explicitly requested and `DEEPSEEK_API_KEY` is available.

## Scope

This feature adds:

- A DeepSeek chat adapter for plain-text and Pydantic-validated structured
  output.
- A provider factory that selects the configured chat adapter.
- Provider-neutral chat contracts and error handling.
- Provider-aware model capability validation.
- Configurable thinking mode and reasoning effort for supported DeepSeek and
  OpenAI models.
- Structured per-agent model and reasoning overrides while retaining the
  existing string model-override shorthand.
- Conditional runtime-secret validation for the selected chat provider.
- DeepSeek-specific unit coverage and an opt-in live smoke test.
- Updated configuration examples and setup documentation.

## Non-Goals

- DeepSeek embeddings.
- Automatic failover, load balancing, or cost-based routing between providers.
- Selecting different providers for different agents in one research run.
- Provider inference from model names or available API keys.
- New CLI flags or HTTP request fields dedicated to provider selection.
- A custom DeepSeek-compatible gateway, proxy, or configurable base URL.
- DeepSeek function calling; the existing agents continue to execute tools in
  the project-owned ReAct loop.
- Streaming model output.
- Persisting or exposing provider reasoning content.

## External API Basis

The DeepSeek adapter targets the official OpenAI-compatible Chat Completions
endpoint at `https://api.deepseek.com` and the model IDs
`deepseek-v4-flash` and `deepseek-v4-pro`. DeepSeek V4 supports thinking and
non-thinking modes, with `high` and `max` effort. Thinking mode ignores
sampling temperature. DeepSeek JSON mode guarantees valid JSON syntax but
still requires the caller to instruct the model to produce JSON and to perform
application-side schema validation.

The OpenAI adapter remains on the Responses API. For supported reasoning
models, it passes the resolved effort through the Responses `reasoning`
parameter. Supported effort values are model-dependent, so configuration must
be checked against project-owned capability metadata before a request.

References:

- [DeepSeek first API call](https://api-docs.deepseek.com/guides/function_calling/)
- [DeepSeek thinking mode](https://api-docs.deepseek.com/guides/thinking_mode)
- [DeepSeek JSON output](https://api-docs.deepseek.com/guides/json_mode/)
- [DeepSeek models and pricing](https://api-docs.deepseek.com/quick_start/pricing)
- [OpenAI reasoning models](https://developers.openai.com/api/docs/guides/reasoning)

## Architecture

### Provider-neutral contracts

Move chat messages, chat results, and the provider error hierarchy to a
provider-neutral module under `deep_research.providers`. The shared hierarchy
is:

- `ProviderError`
- `ProviderConfigurationError`
- `ProviderTimeoutError`
- `ProviderRateLimitError`
- `ProviderResponseError`
- `StructuredOutputError`

Agents and graph/runtime boundaries catch `ProviderError`, not an
OpenAI-specific base class. Keep `OpenAIProviderError` as a compatibility alias
for `ProviderError` so existing imports and callers continue to work during
the migration.

The existing `StructuredCompleter` protocol remains the agent-facing
interface. Agents continue to depend only on `complete_structured(...)` and
project-owned `ChatMessage` objects.

### Dedicated adapters

`OpenAIChatProvider` retains its existing Responses implementation, including
native parsed structured output and one repair attempt.

Add `DeepSeekChatProvider` with the same project-owned public methods:

- `complete(messages, *, agent_name=None) -> ChatResult`
- `complete_structured(messages, schema, *, agent_name=None) -> schema`

The DeepSeek adapter uses the existing `openai` Python dependency's
`AsyncOpenAI` client configured with the DeepSeek API key and code-owned base
URL. The SDK is still lazily imported, and a client remains injectable for
offline tests.

### Provider factory

Add a small factory at the runtime/provider boundary. Given `LLMConfig` and a
`Tracker`, it constructs exactly one chat adapter:

- `provider == "deepseek"` -> `DeepSeekChatProvider`
- `provider == "openai"` -> `OpenAIChatProvider`

The provider field is a closed configuration enum. Any other value is a
configuration error before runtime assembly.

`build_runtime` continues to construct `OpenAIEmbeddingProvider` independently
of the selected chat adapter. This keeps the memory subsystem and existing
vectors unchanged.

## Configuration

### Defaults

The default YAML and `LLMConfig` defaults become:

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

`thinking_mode` accepts `enabled` or `disabled`. `reasoning_effort` accepts
the provider-neutral superset `none`, `minimal`, `low`, `medium`, `high`,
`xhigh`, and `max`, then the selected adapter validates the effective value
against the selected model. When thinking is disabled, the configured effort
is retained as a dormant default but is not sent; this is intentional because
the explicit mode controls whether reasoning occurs.

Add these environment overrides:

- `LLM_THINKING_MODE`
- `LLM_REASONING_EFFORT`

Existing `LLM_PROVIDER`, `LLM_MODEL`, request-scoped config overrides, and YAML
loading remain the provider/model switching surface. A complete environment
switch therefore looks like:

```text
LLM_PROVIDER=openai
LLM_MODEL=gpt-5.6
LLM_THINKING_MODE=enabled
LLM_REASONING_EFFORT=high
```

### Per-agent overrides

`model_overrides` accepts either the existing model-name string or a structured
override:

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

The string form changes only the model and inherits the global thinking mode
and effort. A structured override may set any subset of `model`,
`thinking_mode`, and `reasoning_effort`; omitted fields inherit global values.
Provider overrides are deliberately excluded, so every agent in one run uses
the selected provider.

`LLMConfig.model_for(...)` is replaced or supplemented by a resolver that
returns an immutable effective model configuration containing model, thinking
mode, and effort. Runtime assembly resolves and validates all six agent names
before constructing the graph, ensuring a bad critic-only override cannot fail
late in the run.

### Capability validation

Each provider owns code-defined capability metadata rather than scattering
model-name checks through request methods. A capability entry declares:

- accepted model IDs or documented model-family aliases;
- supported thinking modes;
- allowed reasoning efforts when thinking is enabled;
- whether temperature is accepted in each mode.

The initial DeepSeek entries are explicit:

| Model | Thinking modes | Enabled efforts | Temperature |
| --- | --- | --- | --- |
| `deepseek-v4-flash` | enabled, disabled | high, max | disabled mode only |
| `deepseek-v4-pro` | enabled, disabled | high, max | disabled mode only |

The OpenAI table covers non-reasoning models already supported by the project
as disabled-only and every model family that the official OpenAI model catalog
identifies as supporting reasoning effort on the implementation date. At a
minimum, this includes the `gpt-5.4*`, `gpt-5.5*`, `gpt-5.6*`, `o1*`, `o3*`,
and `o4-mini*` reasoning families, plus the existing `gpt-4o*` and `gpt-4.1*`
non-reasoning families. Each entry uses the exact effort subset documented for
that family; an effort is not inferred merely because it belongs to the
provider-neutral superset. Snapshot IDs inherit the matching family entry.

The table is fail-closed: an unknown model or an effort outside the matching
entry raises a configuration error naming the provider, model, and unsupported
setting. Adding a future model requires one capability entry and its focused
tests, not adapter control-flow changes.

This local registry is necessary to honor the fail-fast requirement. It is not
an attempt to discover account entitlements or live model availability; an
otherwise valid model can still be rejected by the remote API if the account
cannot use it.

## Runtime Secret Rules

Secrets remain environment-only and never enter `ConfigSettings`.

Strict configuration loading requires:

| Selected chat provider | Required secrets |
| --- | --- |
| DeepSeek | `DEEPSEEK_API_KEY`, `OPENAI_API_KEY`, `TAVILY_API_KEY` |
| OpenAI | `OPENAI_API_KEY`, `TAVILY_API_KEY` |

`OPENAI_API_KEY` remains mandatory in DeepSeek mode because long-term memory
still uses OpenAI embeddings. `DEEPSEEK_API_KEY` is not required in OpenAI
mode. Existing conditional LangSmith requirements remain unchanged.

The committed `.env.example` contains blank placeholders only. Key values
must never appear in serialized configuration, spans, tracker records, error
details, test snapshots, logs, or committed files.

## DeepSeek Request Behavior

### Message translation

The project contract permits `developer` messages, while DeepSeek Chat
Completions accepts `system`, `user`, `assistant`, and `tool`. At the DeepSeek
adapter boundary, each `developer` message becomes a `system` message without
changing its content or order. Other supported roles pass through unchanged.
The provider does not expose its SDK message types to agents.

### Plain completion

For `complete(...)`, the adapter calls `chat.completions.create(...)` with the
resolved model, translated messages, output-token bound, provider-specific
thinking parameters, and temperature only when supported. It requires exactly
one usable choice with non-empty text.

The adapter rejects responses with missing choices or text, malformed usage,
or a terminal finish reason indicating truncation, filtering, or insufficient
provider resources. A normal stop is accepted. The returned `ChatResult` uses
project-owned types.

### Structured completion

For `complete_structured(...)`, the adapter:

1. Produces a deterministic JSON instruction from the supplied Pydantic
   schema, including the word `JSON` and the schema itself.
2. Appends that instruction as a system message.
3. Requests `response_format={"type": "json_object"}`.
4. Parses the response with `schema.model_validate_json(...)`.
5. On JSON or Pydantic validation failure, appends a repair instruction and
   makes exactly one additional request.
6. Raises `StructuredOutputError` if the second response is invalid.

An empty response is treated as a validation failure on the first attempt and
may use the one repair. Truncated, filtered, transport, authentication, and
rate-limit responses are operational failures and are not repaired as schema
errors.

The repair prompt may describe validation problems but must not be written to
user-facing errors or telemetry. Provider output and reasoning content are not
logged.

### Thinking and temperature translation

For DeepSeek thinking mode, the adapter sends:

```python
extra_body={"thinking": {"type": "enabled"}}
reasoning_effort="high"  # or "max"
```

It omits `temperature` while thinking is enabled. When thinking is disabled,
it sends the disabled toggle, omits reasoning effort, and may send the
configured temperature.

For OpenAI reasoning models, the existing Responses request adds:

```python
reasoning={"effort": resolved_effort}
```

When OpenAI thinking is disabled, the adapter uses the capability entry's
documented no-reasoning value when one exists; for a non-reasoning model it
omits the reasoning parameter. Temperature is sent only when that model/mode
combination supports it. The adapter never sends a known-ignored setting.

## Errors and Partial Progress

OpenAI SDK errors from either adapter map to the shared provider hierarchy:

- timeout -> `ProviderTimeoutError`
- rate limit -> `ProviderRateLimitError`
- connection failure -> `ProviderResponseError`
- status/authentication/invalid request -> `ProviderResponseError`
- unusable response shape or finish reason -> `ProviderResponseError`
- exhausted structured repair -> `StructuredOutputError`

Configuration errors identify the provider, model, setting name, and accepted
values, but never a key value. Runtime assembly reports the selected provider
in its safe configuration error instead of hard-coding OpenAI.

There is no automatic OpenAI fallback when DeepSeek fails. Once a run starts,
provider failures follow the existing ReAct, agent, graph, and outcome paths:
partial findings are retained, typed research errors are recorded, and
provider text remains excluded from user-facing errors.

## Observability

Every model span records safe metadata:

- provider (`deepseek` or `openai`);
- resolved model;
- agent name when present;
- operation (`chat` or `structured_output`);
- structured attempt number when applicable;
- effective thinking mode;
- requested/effective reasoning effort when applicable;
- message count;
- input, output, and total token usage when valid;
- latency and typed success/failure through the existing tracker.

Raw reasoning content, prompts, responses, schema-validation payloads, and API
keys are excluded. DeepSeek `prompt_tokens` and `completion_tokens` map to the
project's existing input/output token fields. Additional reasoning-token
details may be counted in the provider's reported output total but are not
persisted as reasoning text.

## Testing

### Offline tests

All normal tests use injected clients and perform no provider requests.

DeepSeek adapter tests cover:

- explicit and environment API-key handling;
- the code-owned base URL and injectable client;
- developer-to-system role translation;
- plain completion parsing;
- JSON schema prompting and local Pydantic validation;
- first-attempt success, one successful repair, and exhausted repair;
- empty content and malformed response shapes;
- terminal finish reasons;
- thinking toggles, effort mapping, and temperature omission/application;
- token-usage mapping and span metadata;
- timeout, rate-limit, connection, and status-error translation;
- absence of keys, prompts, provider output, and reasoning content from
  telemetry and serialized errors.

Configuration and runtime tests cover:

- DeepSeek/V4 Flash/enabled/high defaults;
- `LLM_PROVIDER`, `LLM_MODEL`, `LLM_THINKING_MODE`, and
  `LLM_REASONING_EFFORT` overrides;
- legacy string and structured per-agent overrides;
- inheritance of omitted override fields;
- validation of all six effective agent configurations;
- unknown providers/models and unsupported modes/efforts;
- provider-conditional secret requirements;
- OpenAI embeddings in DeepSeek mode;
- factory selection and provider-neutral exception handling;
- OpenAI reasoning parameters without regression to Responses structured
  parsing.

The complete existing suite must continue to pass. Ruff must report no lint
errors.

### Opt-in live smoke test

Add a live test excluded from normal pytest selection. It runs only when the
developer selects the live marker, sets `RUN_DEEPSEEK_LIVE_TESTS=1`, and has a
non-empty `DEEPSEEK_API_KEY`; otherwise it skips. The README documents the
exact PowerShell and POSIX commands. The test performs one bounded structured
request to `deepseek-v4-flash`, validates a tiny Pydantic response, and asserts
safe token usage without printing the key, prompt, raw response, or reasoning
content.

The live test does not exercise embeddings or Tavily and therefore does not
require their keys. It verifies the adapter boundary, not an end-to-end
research run, and keeps accidental cost and network use out of the default
suite.

## Documentation and Migration

Update:

- `config.yaml` with the new defaults and reasoning fields;
- `.env.example` with a blank `DEEPSEEK_API_KEY` placeholder;
- README setup, configuration, provider examples, secret requirements, and
  live-smoke-test command;
- provider exports and descriptions that currently say OpenAI-only.

Existing YAML `model_overrides` string values remain valid. Existing callers
that import `OpenAIProviderError` continue to work through the compatibility
alias. Existing OpenAI users must explicitly select `provider: openai` and an
OpenAI model after the default changes; this default change is intentional.

No persisted research-state, checkpoint, memory-vector, CLI argument, or HTTP
request/response schema changes are required.

## Acceptance Criteria

- A default configuration resolves DeepSeek V4 Flash with thinking enabled and
  high effort for all agents.
- Changing configuration to OpenAI constructs the OpenAI Responses adapter
  without code changes.
- Per-agent model and effort overrides resolve, inherit, and validate before
  graph construction.
- Unsupported provider/model/reasoning combinations fail with safe, actionable
  configuration errors before an API call.
- DeepSeek plain and structured requests use the correct API shape, validate
  locally, and perform at most one structured repair.
- DeepSeek mode continues to use OpenAI embeddings.
- Normal tests make no live provider requests.
- The full offline test suite and Ruff pass.
- The explicitly invoked DeepSeek live smoke test passes with a valid key.
- No secret or raw reasoning content is serialized, logged, traced, or
  committed.
