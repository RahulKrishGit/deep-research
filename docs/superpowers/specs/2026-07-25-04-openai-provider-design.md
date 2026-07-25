# OpenAI Provider Design

Date: 2026-07-25
Status: Draft for review
Parent Spec: 2026-07-25-agentic-deep-research-design.md

## Purpose

Provide the first-build model and embedding layer using OpenAI only.

## Scope

This feature adds:

- OpenAI chat model wrapper.
- OpenAI structured output helper.
- OpenAI embedding wrapper.
- Token usage capture.
- Observability integration around model calls.

## Non-Goals

- No Anthropic, Google, Ollama, or other provider adapters.
- No model routing optimization.
- No prompt library beyond minimal call support.

## Design

Create `src/deep_research/providers/openai_provider.py`.

Interfaces may be narrow, but only OpenAI is implemented:

- `OpenAIChatProvider`
- `OpenAIEmbeddingProvider`

Chat capabilities:

- Plain chat responses.
- Structured output from a supplied Pydantic schema.
- Optional streaming support if the chosen OpenAI client path supports it cleanly.

Embedding capabilities:

- Embed one query.
- Embed a list of texts.
- Report embedding dimension.

Configuration:

- Default chat model.
- Default embedding model.
- Per-agent model overrides.
- Timeout.
- Retry count.
- Temperature.
- Max tokens.

## Observability

Every model call emits:

- Provider name.
- Model name.
- Token usage when returned.
- Latency.
- Success or error.
- Structured output validation failures.

## Error Handling

Handle:

- Missing API key.
- Timeout.
- Rate limit.
- Invalid structured output.
- Provider response errors.

Structured output should get one repair attempt before returning a typed error.

## Testing

Tests should mock the OpenAI client and cover:

- Chat response parsing.
- Structured output success.
- Structured output repair failure.
- Embedding response parsing.
- Token metric capture.
- Missing key validation.

## Acceptance Criteria

- Agents can call OpenAI through this provider without depending on OpenAI client details.
- Embeddings can be generated for memory.
- Token usage is available to observability.
- All provider tests run without real API calls.
