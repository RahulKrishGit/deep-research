# Codex Dual-Provider DeepSeek Routing Design

## Goal

Allow Codex to keep its current GPT provider as the default while launching DeepSeek workers from the same workspace through Codex's custom provider/profile mechanism.

## Constraints

- Current Codex CLI accepts only the Responses wire protocol for custom providers.
- DeepSeek's documented OpenAI-compatible endpoint exposes Chat Completions at `https://api.deepseek.com`.
- The parent Codex process must retain its existing GPT authentication and default model.
- The DeepSeek API key must come from `DEEPSEEK_API_KEY` and must not be written to repository files or Codex TOML.
- A DeepSeek task must fail closed if the configured model is not returned by DeepSeek's `/models` endpoint.
- The bridge must be local-only and bind to `127.0.0.1`.

## Architecture

The repository will contain a small Python standard-library bridge. It accepts Codex Responses API requests on `127.0.0.1:8765/v1/responses`, translates the request into DeepSeek Chat Completions format, and translates both non-streaming and streaming responses back into Responses API events. Function tools and tool-result continuation items are preserved so a DeepSeek child can perform agentic work.

The user-level Codex configuration will add a `deepseek` provider pointing to the bridge and two profiles, `deepseek-pro` and `deepseek-flash`. The provider reads the API key from `DEEPSEEK_API_KEY`. A PowerShell launcher will start the bridge, run `codex exec --profile <profile>`, and terminate the bridge in a `finally` block. The launcher will preflight the requested model against DeepSeek's `/models` endpoint before starting Codex.

## Failure handling

- Missing `DEEPSEEK_API_KEY` fails before the bridge starts.
- A missing requested model fails before a task request is sent.
- Invalid JSON, unsupported request shapes, or upstream non-2xx responses return explicit HTTP errors.
- The bridge never falls back to GPT or silently substitutes a DeepSeek model.
- The launcher terminates only the bridge process it started.

## Verification

- Unit tests cover input-item conversion, tool conversion, response conversion, SSE conversion, and fail-closed model checks.
- A live preflight calls DeepSeek `/models` using the existing environment key without printing the key.
- A live non-streaming request confirms the returned model is the requested DeepSeek model.
- A live Codex child invocation is run with a minimal prompt through the configured profile; the output must complete and identify the DeepSeek model in the bridge audit log.
- Existing repository tests must remain green.
