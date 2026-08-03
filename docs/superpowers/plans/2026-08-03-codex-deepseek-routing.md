# Codex Dual-Provider DeepSeek Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve GPT as Codex’s default while allowing explicit DeepSeek child runs from the same workspace.

**Architecture:** A localhost Python bridge translates Codex Responses requests to DeepSeek Chat Completions. A PowerShell launcher preflights the requested model, manages the bridge, invokes an ephemeral Codex child, and cleans up.

**Tech Stack:** Python standard library, PowerShell, Codex CLI, pytest, and existing repository tooling.

## Global Constraints

- Bind the bridge only to `127.0.0.1`.
- Read the key only from `DEEPSEEK_API_KEY`; never store or log it.
- Require exact model identity from `/models` and completion responses.
- Never fall back to GPT or silently substitute another DeepSeek model.
- Keep `model = "gpt-5.6-luna"` unchanged.
- Add no runtime dependencies.
- Keep runtime logs under `/.deepseek-runs/`, which is ignored by Git.

---

### Task 1: Request translation

**Files:**
- Create: `scripts/__init__.py`
- Create: `scripts/codex_deepseek_bridge.py`
- Create: `tests/test_codex_routing/__init__.py`
- Create: `tests/test_codex_routing/test_request_translation.py`

**Interfaces:**
- Produce `BridgeError`, `BridgeState`, and `translate_responses_request(request, state)`.
- Convert `instructions`, user/developer messages, text parts, function calls, function outputs, tools, tool choice, token limits, user IDs, and reasoning effort.
- Reject images, files, unknown item types, non-function tools, JSON-schema output, unknown models, and non-null `previous_response_id`.

- [ ] Write failing tests for system/user conversion, function-call continuation, tools, reasoning mapping, and rejected request shapes.
- [ ] Run `pytest tests/test_codex_routing/test_request_translation.py -v` and confirm failure because the module is missing.
- [ ] Implement the minimal translation functions and reasoning state required by the tests.
- [ ] Re-run the focused tests and confirm they pass.

### Task 2: Non-streaming response translation

**Files:**
- Modify: `scripts/codex_deepseek_bridge.py`
- Create: `tests/test_codex_routing/test_response_translation.py`

**Interfaces:**
- Produce `translate_chat_completion(request, completion, state)`.
- Map assistant text to a Responses message, DeepSeek tool calls to Responses function-call items, usage fields, and finish reasons.
- Reject a response whose `completion["model"]` differs from `request["model"]`.

- [ ] Write failing tests for text, tool calls, usage, incomplete responses, and model mismatch.
- [ ] Run the focused tests and confirm failure.
- [ ] Implement the minimal non-streaming response mapping.
- [ ] Re-run the focused tests and confirm they pass.

### Task 3: Streaming translation

**Files:**
- Modify: `scripts/codex_deepseek_bridge.py`
- Create: `tests/test_codex_routing/test_stream_translation.py`

**Interfaces:**
- Produce `parse_chat_sse(lines)`, `translate_chat_stream(request, chunks, state)`, and `encode_response_sse(event)`.
- Ignore keep-alive comments and `[DONE]`.
- Emit valid Responses SSE events with contiguous sequence numbers for text and tool calls.

- [ ] Write failing tests for SSE parsing, text event order, tool argument deltas, terminal events, and malformed upstream data.
- [ ] Run the focused tests and confirm failure.
- [ ] Implement the stream translator.
- [ ] Re-run the focused tests and confirm they pass.

### Task 4: Local HTTP bridge and preflight

**Files:**
- Modify: `scripts/codex_deepseek_bridge.py`
- Create: `tests/test_codex_routing/test_bridge_server.py`

**Interfaces:**
- Serve `GET /healthz` and `POST /v1/responses`.
- Implement `preflight --model <model>` and `serve --host 127.0.0.1 --port 8765 --audit-log <path>`.
- Call DeepSeek `GET https://api.deepseek.com/models` and `POST https://api.deepseek.com/chat/completions`.

- [ ] Write failing server tests for loopback-only binding, missing key, bearer authentication, body limits, invalid JSON, preflight model matching, and sanitized upstream failures.
- [ ] Run the focused tests and confirm failure.
- [ ] Implement the standard-library server, client, preflight, and sanitized audit logging.
- [ ] Re-run the focused tests and confirm they pass.

### Task 5: PowerShell launcher lifecycle

**Files:**
- Create: `scripts/invoke-codex-deepseek.ps1`
- Create: `tests/test_codex_routing/test_launcher.py`

**Interfaces:**
- Support `-Profile deepseek-pro|deepseek-flash`, `-Prompt`, `-Sandbox`, `-WorkingDirectory`, `-BridgePort`, and `-PreflightOnly`.
- Validate the key, map the profile to an exact model, run preflight, start the bridge hidden, poll `/healthz`, pipe the prompt to `codex exec --profile ... --ephemeral --json`, propagate the child exit code, and terminate only the bridge process in `finally`.

- [ ] Write failing tests for missing keys, preflight failure, occupied ports, health timeout, child failure, cleanup, stdin prompt delivery, and secret-free logs.
- [ ] Run the focused tests and confirm failure.
- [ ] Implement the launcher with explicit process ownership and cleanup.
- [ ] Re-run the focused tests and confirm they pass.

### Task 6: User-level Codex configuration

**Files:**
- Modify: `C:/Users/Rahul Krishnamoorthy/.codex/config.toml`
- Create: `C:/Users/Rahul Krishnamoorthy/.codex/deepseek-pro.config.toml`
- Create: `C:/Users/Rahul Krishnamoorthy/.codex/deepseek-flash.config.toml`

- [ ] Back up the current config before editing.
- [ ] Add a `deepseek` provider pointing to `http://127.0.0.1:8765/v1`, using `env_key = "DEEPSEEK_API_KEY"` and `wire_api = "responses"`.
- [ ] Add `deepseek-pro` and `deepseek-flash` profiles with exact model IDs, `model_provider = "deepseek"`, high reasoning effort, and web search disabled.
- [ ] Validate both profiles with `codex --profile <name> --strict-config --help` or an equivalent non-network config parse.
- [ ] Confirm the root GPT model and the absence of API-key values in TOML.

### Task 7: Documentation and repository protection

**Files:**
- Modify: `.gitignore`
- Modify: `README.md`

- [ ] Ignore `/.deepseek-runs/`.
- [ ] Document environment-key setup, profile examples, GPT-default preservation, ephemeral DeepSeek children, and the prohibition on `setx` or committed secrets.
- [ ] Run `git diff --check`.

### Task 8: Live verification

**Files:**
- No additional files.

- [ ] Run preflight for both DeepSeek profiles without printing the key.
- [ ] Send minimal direct non-streaming requests to both models and require exact returned model IDs.
- [ ] Run a minimal Codex child through each profile and inspect bridge audit metadata.
- [ ] Run a tool-use continuation test that reads the first line of `pyproject.toml`.
- [ ] Test an invalid model and confirm no fallback.
- [ ] Run `pytest -q`, `ruff check src scripts tests` when Ruff is available, and `git diff --check`.

## Self-review checklist

- [ ] Every required design constraint maps to a task.
- [ ] No task contains a placeholder or unbounded implementation instruction.
- [ ] Names and paths are consistent across tasks.
- [ ] Live checks distinguish reachability, model identity, and Codex integration.
