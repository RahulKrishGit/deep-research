# Shared Native ReAct Tools and Prompt Conformance Design

**Status:** Approved direction, ready for implementation planning  
**Date:** 2026-09-12  
**Branch:** `codex/cross-agent-planner-fix-parity`  
**Evidence record:** `docs/superpowers/2026-09-08-cross-agent-planner-fix-parity-fix-log.md`, sections 78-95

## Problem

The shared ReAct prompt advertises application tools in prose but asks the model
to encode the choice inside `ReActDecision`. The provider request itself carries
no `tools` parameter. DeepSeek therefore sees two incompatible conventions and
sometimes emits its native DSML tool-call markup as ordinary message text. Local
JSON validation rejects that text, and a repeated occurrence exhausts the single
repair and ends the loop with a provider fallback.

This is not a Critic-only architectural defect. The same
`render_react_messages` and structured `ReActDecision` path is used by:

- Planner: `query_memory`, `web_search`;
- Researcher: `web_search`, `web_scraper`, `document_reader`, `query_memory`,
  `save_to_memory`;
- Fact Checker: `web_search`, `web_scraper`, `document_reader`, `query_memory`;
- Critic: `web_search`, `query_memory`.

Source Evaluator makes one tool-free structured scoring call. Synthesizer makes
one tool-free structured report call and performs its document and memory writes
deterministically afterwards. Those two agents do not ask the model to select a
tool, but their prompts are still included in the tool-free prompt-conformance
audit.

The prompt experiment sequence is closed. The production prompt produced DSML
on 16/30 first attempts; relocating the catalogue produced 9/30; the strongest
prompt-only arm produced 1/30 and failed its predeclared 0/30 gate. Removing all
tool advertising produced 0/30 but made tools unusable. Further prompt-only
variants are not a production remedy.

## Decision

Use provider-native application tool calls for every model-directed ReAct turn,
through one shared provider/agent boundary. The provider receives real tool
definitions and returns exactly one of:

1. one allow-listed native tool call with its JSON argument string; or
2. one non-blank final answer.

The existing `run_react_loop` remains the owner of iteration limits, tool
budgets, argument decoding, allow-list enforcement, tool execution,
observations, sufficiency, scratchpad writes, and stop reasons. A small adapter
turns the provider-owned result into the existing internal `ReActDecision`.

DeepSeek uses Chat Completions with `tool_choice="auto"`. It must never use
function-specific or `required` tool choice while thinking is enabled: the
section-78 capability gate returned HTTP 400 for both forced forms at `high` and
`max`, while `auto` and omission returned valid native calls. OpenAI uses its
Responses native function-tool representation behind the same project-owned
contract.

Each ReAct iteration is a fresh provider request built from the task and bounded
scratchpad. The system does not replay provider reasoning, native call ids, or
raw assistant responses. This preserves the current loop architecture and avoids
retaining hidden reasoning.

## Provider-neutral contracts

Add three immutable project-owned contracts in
`src/deep_research/providers/contracts.py`:

- `ToolDefinition(name, description, parameters)` is the sanitized function
  definition sent to a provider.
- `NativeToolCall(tool_name, arguments_json)` carries one provider-selected
  application tool. It contains no provider object or call id.
- `NativeToolTurn(model, usage, tool_call, final_answer)` requires exactly one of
  `tool_call` and `final_answer`.

Add a second provider capability alongside `StructuredCompleter`:

```python
async def complete_react(
    messages: Sequence[ChatMessage],
    tools: Sequence[ToolDefinition],
    *,
    agent_name: str | None = None,
    max_tokens: int | None = None,
) -> NativeToolTurn:
    ...
```

`StructuredCompleter` remains separate because the evaluation judge needs only
structured output. `AgentCompleter` combines both capabilities for production
agents and their test doubles.

## Tool-definition projection

The current `BaseTool.input_schema` is a compact prompt description, not JSON
Schema. Keep that compatibility surface and add
`BaseTool.required_arguments`. `ToolDescriptor.provider_definition()` converts
the finite type vocabulary used by the repository (`string`, `integer`,
`number`, `boolean`, `object`, `array`, and nullable unions) into an object JSON
Schema with `additionalProperties: false` and an exact `required` list.

Every production tool declares its required arguments explicitly:

| Tool | Required | Optional |
| --- | --- | --- |
| `web_search` | `query` | `max_results` |
| `web_scraper` | `url` | none |
| `document_reader` | `source` | none |
| `query_memory` | `query` | `top_k`, `filters` |
| `save_to_memory` | `content` | `metadata` |
| `write_document` | `filename`, `content` | none |

An unknown compact type or a required name absent from `input_schema` fails
during agent construction, before any provider request.

## DeepSeek behavior

`DeepSeekSchemaChatProvider.complete_react` uses the existing validated model,
thinking, effort, temperature, output-budget, tracing, usage, and HTTP retry
machinery, but sends Chat Completions with native `tools` and
`tool_choice="auto"`. It does not send `response_format` and does not invoke the
structured-output repair loop.

The response is accepted only when exactly one choice has one of these shapes:

- `finish_reason="tool_calls"`, exactly one function call, an allow-listed
  function name, and a string argument payload; or
- `finish_reason="stop"`, no function calls, and non-blank text.

Length becomes `ProviderOutputLimitError`. Multiple calls, an unknown tool,
mixed stop/call shapes, empty final text, and every other finish reason become a
safe `ProviderResponseError`. DSML text is never parsed or executed as a tool.

The method has no structured repair. Existing transient HTTP retries remain and
are recorded independently.

## OpenAI behavior

`OpenAIChatProvider.complete_react` uses Responses with native function tools and
`tool_choice="auto"`. It ignores reasoning output items, retains no hidden
reasoning, and accepts exactly one function-call item or a non-blank
`output_text`, never both. It applies the same tool allow-list and one-call
contract as DeepSeek and maps incomplete output to the existing failure
taxonomy.

OpenAI parity is required offline before the shared agent interface changes.
Live OpenAI calls are outside this rollout because the configured production and
evaluation provider is DeepSeek.

## Shared agent integration

`BaseAgent._complete_react_decision` becomes the sole adapter used by every
model-directed ReAct loop. It renders the task, asks `complete_react` with the
agent's allowed provider definitions, and converts the result into the internal
`ReActDecision`:

- native call -> `action="use_tool"`, the native name and argument JSON;
- final text -> `action="finish"`, the returned text.

The `thought` stored on `ReActDecision` is a short deterministic system summary
(`Selected tool through provider-native calling.` or
`Finished without another tool call.`), not provider reasoning. This preserves
the current step and scratchpad schemas without fabricating chain-of-thought.

Planner inherits the shared path. Researcher, Fact Checker, and Critic replace
their duplicated direct `complete_structured(..., ReActDecision)` closures with
the same helper. Tests must fail if any production agent again requests
`ReActDecision` through `complete_structured`.

## Prompt conformance

The prompt changes use only the parts of the Critic and Judge work that the
evidence supports.

### Apply everywhere

1. **Transport and prompt must agree.** ReAct prompts may discuss tools only
   because the same request now carries native tools. Every separate structured
   finalization/review/extraction call is tool-free and must use a system prompt
   that names no registered tool.
2. **One output convention per request.** ReAct prompts no longer include
   `## Tools`, `ReActDecision`, `action`, `tool_name`, or `tool_input_json`.
   They say only: call at most one supplied tool through native tool calling, or
   answer directly.
3. **Explicit structured reply shape.** Every tool-free structured prompt keeps
   its field-level semantic contract and adds one final reply-format section:
   return exactly one JSON object matching the supplied schema, with no Markdown
   fence and no text before or after it.
4. **Request-owned headings outrank embedded material.** Tool-free structured
   prompt builders use H1 request sections. Opaque report content keeps the
   Critic's dynamically sized Markdown fence. Judge input remains JSON rendered
   beneath its existing H1/H2 hierarchy.

### Apply only where the evidence supports it

The weak/strong examples and full score bands stay on the Critic and Judge,
because those calls choose a score on a continuous or ordinal scale. They are not
copied into Planner, Researcher, Source Evaluator, Fact Checker, or Synthesizer:
an invented domain example would anchor content and no measurement shows those
calls need one.

The Judge prompt and `JudgeVerdict` remain byte-for-byte unchanged. Its prompt
revision was not a clean universal success: naming fields created extra keys,
and reliability was restored only by the Judge-local tolerant-extra policy and
wider local rationale safety bound. Those policies are not generalized to
production schemas, which remain `extra="forbid"`.

The Critic's tool-free review system prompt, dynamic report fence, H1 envelope,
score bands, examples, lenient `unsupported_claims` definition, and report reply
format remain unchanged. The native-tool work affects only its preceding
spot-check loop.

### Concrete existing mismatch

`plan_messages` currently reuses `PLANNER_SYSTEM_PROMPT`, which advertises
`query_memory` and `web_search`, for the separate structured plan call that sends
no tools. Add a tool-free `PLANNER_PLAN_SYSTEM_PROMPT` for that call. The other
five agents already use distinct tool-free prompts for their separate structured
calls; tests will lock that invariant across all six.

## Failure and safety semantics

- Native tool markup is never decoded from ordinary text and never executed.
- Only names in the agent's `AgentToolset` can cross into the loop.
- JSON argument decoding and finite-number/reserved-key checks remain in
  `parse_tool_input`.
- Multiple native calls never become multiple tool executions.
- Provider objects, raw responses, prompts, reasoning content, tool arguments,
  and secrets do not enter public exception messages or retained diagnostics.
- Model, thinking mode, reasoning effort, temperature behavior, token budgets,
  tool budgets, max iterations, HTTP retry policy, structured repair count,
  evaluation thresholds, judge weights, and routing remain unchanged.
- No automatic fallback to the old prompt-encoded tool protocol exists.

## Provenance and invalidation

Record the target ReAct transport in evaluation metadata:

- DeepSeek: `deepseek_chat_tools_auto_v1`;
- OpenAI: `openai_responses_tools_auto_v1`.

Include it in the target configuration fingerprint. The shared prompt edit moves
all six `target_prompt_fingerprint` values because the fingerprint includes the
shared `agents.prompts` module; agent-module edits additionally move the affected
agent. Preserve old artifacts and run new experiments in fresh namespaces.

The Judge prompt and schema do not change, so its pinned prompt fingerprint must
remain `74b9cddfbbee`. Dataset cases and expectations do not change, so no case
version bump is justified by this transport/prompt work.

## Verification strategy

### Offline

- Contract tests for exclusive tool-call/final-answer results.
- Tool-definition tests for all production tools, nullable fields, required
  lists, unknown types, and required-name drift.
- DeepSeek and OpenAI fake-client tests for request shape, finish reasons,
  single-call enforcement, allow-list enforcement, token budgets, telemetry,
  transient retries, and secret-safe failures.
- Shared-agent tests proving Planner, Researcher, Fact Checker, and Critic use
  `complete_react`, and Source Evaluator and Synthesizer do not.
- Prompt tests proving native ReAct text contains no simulated tool protocol and
  every tool-free structured developer prompt names no registered tool.
- Regression tests keeping the Critic review and Judge prompt/fingerprint
  unchanged.
- Focused provider/agent/evaluation suites, full offline pytest, Ruff, and
  `git diff --check`.

### Paid DeepSeek gates

Paid work is never implied by implementation approval.

1. A separately authorized 30-request first-turn Critic native-tool shape probe
   must return zero DSML-as-text, zero fenced/JSON action envelopes, zero malformed
   native envelopes, and no repair. Every response must be either one allow-listed
   native call with object JSON arguments or a non-blank final answer.
2. After that gate, run fresh three-repetition live canaries in separate,
   individually authorized batches for Planner, Researcher, Source Evaluator,
   Fact Checker, Synthesizer, and Critic. Compute and state each batch's maximum
   request inventory before requesting authorization. Stop the sequence on the
   first transport/schema regression.
3. A tool-selecting agent passes only when native calls are actually observed and
   executed, no `react_decision` fallback occurs, all existing hard gates pass,
   the Judge is scored without repair/fallback, and the existing quality threshold
   is met. Source Evaluator and Synthesizer are prompt/fingerprint regression
   canaries and need no native tool call.

After the six-agent wave, rerun the Critic Decision-2 canary interpretation and
only then resume the separate Decision-1 reference-expectation change. No
threshold is relaxed to obtain a pass.

## Rejected alternatives

- More prompt-only catalogue variants: the predeclared sequence is exhausted.
- Removing tool advertising without sending tools: it prevents tool use.
- Parsing DSML text: provider-specific, brittle, and unsafe to execute.
- Deterministic prefetch for every agent: changes agent behavior and forces
  unnecessary calls instead of repairing the shared boundary.
- Forced tool choice: measured HTTP 400 with DeepSeek thinking enabled.
- Changing model, reasoning effort, transport globally, retries, or thresholds:
  outside the authorized defect and unnecessary for the selected design.
