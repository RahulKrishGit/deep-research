# Shared Native ReAct Tools and Prompt Conformance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace prompt-encoded ReAct tool selection with provider-native tool calls for every tool-selecting agent, and apply the proven Critic/Judge prompt-conformance rules to every agent's separate tool-free structured call.

**Architecture:** Add a provider-neutral native-tool-turn contract implemented by DeepSeek Chat Completions and OpenAI Responses, then adapt it once in `BaseAgent` to the existing internal `ReActDecision` and `run_react_loop`. Remove the simulated tool catalogue/action schema from ReAct prompts, give the Planner a distinct tool-free finalization prompt, and preserve the already-verified Critic review and Judge prompts unchanged.

**Tech Stack:** Python 3.11+, Pydantic v2, OpenAI Python SDK 2+, DeepSeek Chat Completions, OpenAI Responses, pytest/pytest-asyncio, Ruff, LangSmith evaluation harness.

**Spec:** `docs/superpowers/specs/2026-09-12-shared-native-react-tools-and-prompt-conformance-design.md`

## Global Constraints

- Work only in `C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.worktrees\cross-agent-planner-fix-parity` on `codex/cross-agent-planner-fix-parity`. Verify the branch and tracked status before each task; preserve `.deepseek-runs/` and `tools/` unstaged.
- At the start of each PowerShell session set `$nativeRoot = (Get-Location).Path`, `$nativePython = 'C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.venv\Scripts\python.exe'`, and `$env:PYTHONPATH = Join-Path $nativeRoot 'src'`; verify `deep_research.__file__` resolves inside `$nativeRoot`.
- Use TDD for every production change: add one focused failing test, run it and confirm the intended failure, implement the minimum behavior, rerun the focused test, then run the task gate.
- Keep `deepseek-v4-flash`, thinking enabled, configured reasoning efforts, temperature behavior, every `32768` output budget, `max_iterations=5`, `tool_budget=10`, HTTP retry policy, one structured repair, routing, cases, rubrics, weights, and the `0.75` live threshold unchanged.
- DeepSeek native ReAct calls use Chat Completions with `tool_choice="auto"`. Never send function-specific or `required` tool choice with thinking enabled; sections 78-79 measured deterministic HTTP 400 for those shapes.
- Native ReAct calls do not use `response_format`, JSON schema structured output, or the structured repair loop. Tool-free finalization/extraction/review calls continue to use the existing Responses `json_schema` path and one repair.
- Never execute a tool call decoded from ordinary text, DSML, XML, Markdown, or a code fence. Only the typed native tool-call field can request execution, and only one allow-listed tool may cross the boundary per iteration.
- Never retain or expose provider reasoning, raw provider responses, prompts, tool arguments, secrets, or provider exception text in public errors, artifacts, logs, or review evidence. Offline tests use harmless sentinels.
- Do not change `JudgeVerdict`, `JUDGE_SYSTEM_PROMPT`, `JUDGE_PROMPT_TEMPLATE`, Judge weights, score bands, or Judge routing. The pinned Judge prompt fingerprint must remain `74b9cddfbbee`.
- Keep the Critic's tool-free review prompt, dynamic report fence, H1 envelope, score bands, examples, lenient `unsupported_claims` rule, score bands, and review reply format unchanged.
- Preserve the Critic/Judge complete weak/strong JSON examples and their score bands. Both examples must remain schema-valid; never show malformed JSON as a negative example. Preserve Source Evaluator's dimension-specific positive/negative prose anchors, but do not add a complete static JSON example whose invented URL would contradict the exact-dossier-URL rule. Do not add domain examples to planning, extraction, verification, or synthesis without a measurement that supports them.
- No DeepSeek, OpenAI, Tavily, LangSmith, evaluation, live test, or other paid/network call is authorized by implementing Tasks 1-7. Task 8 stops for a separately stated request inventory and explicit authorization before each batch.
- Preserve old artifacts. Every live run after the shared prompt and transport change uses a fresh output namespace and records the new commit, prompt fingerprint, and target ReAct transport.
- Stage exact paths only. Every task ends with Ruff on touched Python, `git diff --check`, and an independently reviewable commit.

---

### Task 1: Define provider-neutral native tool contracts and exact tool schemas

**Files:**
- Modify: `src/deep_research/providers/contracts.py`
- Modify: `src/deep_research/providers/__init__.py`
- Modify: `src/deep_research/agents/toolset.py`
- Modify: `src/deep_research/tools/base.py`
- Modify: `src/deep_research/tools/web_search.py`
- Modify: `src/deep_research/tools/web_scraper.py`
- Modify: `src/deep_research/tools/document_reader.py`
- Modify: `src/deep_research/tools/memory_tools.py`
- Modify: `src/deep_research/tools/write_document.py`
- Modify: `tests/agent_fakes.py`
- Modify: `tests/test_imports.py`
- Test: `tests/test_provider_contracts.py`
- Test: `tests/test_agents/test_toolset.py`

**Interfaces:**
- Consumes: existing `ChatMessage`, `TokenUsage`, `BaseTool.input_schema`, and `AgentToolset` allow-list.
- Produces: `ToolDefinition`, `NativeToolCall`, `NativeToolTurn`, `BaseTool.required_arguments`, `ToolDescriptor.provider_definition()`, and `AgentToolset.provider_definitions()`.

- [ ] **Step 1: Write failing exclusive-result contract tests**

Add imports for `NativeToolCall`, `NativeToolTurn`, `ToolDefinition`, and `TokenUsage` in `tests/test_provider_contracts.py`, then add:

```python
def test_a_native_tool_turn_carries_exactly_one_tool_call() -> None:
    turn = NativeToolTurn(
        model="deepseek-v4-flash",
        usage=TokenUsage(input_tokens=3, output_tokens=2, total_tokens=5),
        tool_call=NativeToolCall(
            tool_name="web_search",
            arguments_json='{"query":"qec capacity"}',
        ),
    )
    assert turn.final_answer is None
    assert turn.tool_call.tool_name == "web_search"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {
            "tool_call": {
                "tool_name": "web_search",
                "arguments_json": "{}",
            },
            "final_answer": "done",
        },
    ],
)
def test_a_native_tool_turn_rejects_zero_or_two_outcomes(payload) -> None:
    with pytest.raises(ValueError, match="exactly one"):
        NativeToolTurn(
            model="deepseek-v4-flash",
            usage=TokenUsage(),
            **payload,
        )
```

- [ ] **Step 2: Run the contract tests and confirm RED**

Run:

```powershell
& $nativePython -m pytest tests/test_provider_contracts.py -q -k native_tool_turn
```

Expected: collection or import fails because the native contracts do not exist.

- [ ] **Step 3: Add the immutable provider contracts**

In `providers/contracts.py`, import `model_validator` and add:

```python
class ToolDefinition(ProviderContract):
    model_config = ConfigDict(
        extra="forbid", str_strip_whitespace=True, frozen=True
    )

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    parameters: dict[str, JsonValue]


class NativeToolCall(ProviderContract):
    model_config = ConfigDict(
        extra="forbid", str_strip_whitespace=True, frozen=True
    )

    tool_name: str = Field(min_length=1)
    arguments_json: str = Field(min_length=1)


class NativeToolTurn(ProviderContract):
    model_config = ConfigDict(
        extra="forbid", str_strip_whitespace=True, frozen=True
    )

    model: str = Field(min_length=1)
    usage: TokenUsage
    tool_call: NativeToolCall | None = None
    final_answer: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_outcome(self) -> "NativeToolTurn":
        if (self.tool_call is None) == (self.final_answer is None):
            raise ValueError(
                "native tool turns require exactly one tool call or final answer"
            )
        return self
```

Export all three from `providers/__init__.py`.

- [ ] **Step 4: Write failing tool-definition projection tests**

In `tests/test_agents/test_toolset.py`, add a tool with a nullable optional
argument and assert the exact provider schema:

```python
class SearchTool(BaseTool):
    name = "search"
    description = "Search one index."
    input_schema = {"query": "string", "limit": "integer|null"}
    required_arguments = ("query",)
    output_schema = {"results": "array"}

    async def _execute(self, context, **kwargs):
        return ToolExecution(data=[], output_summary={"count": 0})


def test_provider_definitions_are_real_object_json_schemas(tracker) -> None:
    toolset = AgentToolset([SearchTool(tracker)], allowed=("search",))
    assert toolset.provider_definitions() == (
        ToolDefinition(
            name="search",
            description="Search one index.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {
                        "anyOf": [{"type": "integer"}, {"type": "null"}]
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        ),
    )
```

Also add tests that an unsupported compact type and a required name absent from
`input_schema` raise `AgentConfigurationError` while constructing the
descriptor, before any provider exists.

- [ ] **Step 5: Run the toolset tests and confirm RED**

Run:

```powershell
& $nativePython -m pytest tests/test_agents/test_toolset.py -q -k "provider_definitions or compact_type or required_name"
```

Expected: FAIL because `required_arguments` and provider projection are absent.

- [ ] **Step 6: Implement the finite compact-schema conversion**

Add `required_arguments: ClassVar[tuple[str, ...]] = ()` to `BaseTool`. In
`agents/toolset.py`, import `ToolDefinition` and implement an exact finite map:

```python
_JSON_TYPES: dict[str, dict[str, JsonValue]] = {
    "string": {"type": "string"},
    "integer": {"type": "integer"},
    "number": {"type": "number"},
    "boolean": {"type": "boolean"},
    "object": {"type": "object"},
    "array": {"type": "array"},
}


def _provider_type_schema(compact: JsonValue) -> dict[str, JsonValue]:
    if not isinstance(compact, str):
        raise AgentConfigurationError("tool input types must be compact strings")
    members = compact.split("|")
    schemas = [
        {"type": "null"} if member == "null" else _JSON_TYPES.get(member)
        for member in members
    ]
    if any(schema is None for schema in schemas):
        raise AgentConfigurationError(f"unsupported compact tool input type: {compact}")
    retained = [schema for schema in schemas if schema is not None]
    return retained[0] if len(retained) == 1 else {"anyOf": retained}
```

Extend `ToolDescriptor` with `required_arguments: tuple[str, ...] = ()`, validate
that required names exist in `input_schema`, and add:

```python
def provider_definition(self) -> ToolDefinition:
    return ToolDefinition(
        name=self.name,
        description=self.description,
        parameters={
            "type": "object",
            "properties": {
                name: _provider_type_schema(compact)
                for name, compact in self.input_schema.items()
            },
            "required": list(self.required_arguments),
            "additionalProperties": False,
        },
    )
```

`ToolDescriptor.from_tool` copies `tool.required_arguments`, and
`AgentToolset.provider_definitions()` maps selected descriptors in declaration
order.

- [ ] **Step 7: Declare the production tools' required arguments**

Add these exact class attributes:

```python
# WebSearchTool
required_arguments = ("query",)

# WebScraperTool
required_arguments = ("url",)

# DocumentReaderTool
required_arguments = ("source",)

# SaveToMemoryTool
required_arguments = ("content",)

# QueryMemoryTool
required_arguments = ("query",)

# WriteDocumentTool
required_arguments = ("filename", "content")
```

Add matching attributes to `EchoTool` and `StrictEchoTool` in
`tests/agent_fakes.py`; leave no-argument fake tools at the empty default.

- [ ] **Step 8: Run Task 1's green gate and commit**

Run:

```powershell
& $nativePython -m pytest tests/test_provider_contracts.py tests/test_agents/test_toolset.py tests/test_tools -q
& $nativePython -m ruff check src/deep_research/providers/contracts.py src/deep_research/agents/toolset.py src/deep_research/tools tests/agent_fakes.py tests/test_provider_contracts.py tests/test_agents/test_toolset.py
git diff --check
```

Expected: PASS. Then commit exact paths:

```powershell
git add -- src/deep_research/providers/contracts.py src/deep_research/providers/__init__.py src/deep_research/agents/toolset.py src/deep_research/tools/base.py src/deep_research/tools/web_search.py src/deep_research/tools/web_scraper.py src/deep_research/tools/document_reader.py src/deep_research/tools/memory_tools.py src/deep_research/tools/write_document.py tests/agent_fakes.py tests/test_provider_contracts.py tests/test_agents/test_toolset.py
git commit -m "feat(tools): define provider-native tool contracts"
```

---

### Task 2: Implement DeepSeek native ReAct turns

**Files:**
- Modify: `src/deep_research/providers/deepseek_provider.py`
- Test: `tests/test_deepseek_provider.py`

**Interfaces:**
- Consumes: `Sequence[ChatMessage]`, `Sequence[ToolDefinition]`, existing DeepSeek request settings and retry/error helpers.
- Produces: `DeepSeekSchemaChatProvider.complete_react(...) -> NativeToolTurn` through inherited `DeepSeekChatProvider` behavior.

- [ ] **Step 1: Extend the fake DeepSeek response builder**

Change `chat_response` in `tests/test_deepseek_provider.py` to accept
`tool_calls: object = None` and place it on `message`. Add this helper:

```python
def native_call(name: str, arguments: str) -> SimpleNamespace:
    return SimpleNamespace(
        type="function",
        function=SimpleNamespace(name=name, arguments=arguments),
    )
```

- [ ] **Step 2: Write the failing native-call request test**

Add a test that calls `complete_react` with one `ToolDefinition` and asserts the
recorded request contains:

```python
assert call["tool_choice"] == "auto"
assert call["tools"] == [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    }
]
assert "response_format" not in call
assert turn.tool_call == NativeToolCall(
    tool_name="web_search",
    arguments_json='{"query":"qec capacity"}',
)
```

The fake response must use `finish_reason="tool_calls"`, exactly one
`native_call`, and no message text.

- [ ] **Step 3: Run the native-call test and confirm RED**

Run:

```powershell
& $nativePython -m pytest tests/test_deepseek_provider.py -q -k native_react
```

Expected: FAIL because `complete_react` is absent.

- [ ] **Step 4: Implement the DeepSeek request and parser**

Add `complete_react` to `DeepSeekChatProvider` with this public signature:

```python
async def complete_react(
    self,
    messages: Sequence[ChatMessage],
    tools: Sequence[ToolDefinition],
    *,
    agent_name: str | None = None,
    max_tokens: int | None = None,
) -> NativeToolTurn:
```

Validate non-empty messages and tools before `_request_options`. Resolve the
per-call budget through `_resolve_max_tokens`, translate messages through
`_translated_messages`, and send:

```python
await self._client.chat.completions.create(
    **request,
    messages=payload,
    tools=[
        {
            "type": "function",
            "function": definition.model_dump(mode="json"),
        }
        for definition in tools
    ],
    tool_choice="auto",
)
```

Use `operation="react_tool_turn"`, `message_count`, and `tool_count` in span
metadata; never record names or arguments. Reuse `with_retries`,
`_raise_deepseek_error`, `_response_telemetry`, `_set_span_result`, and
`last_model_returned`.

Parse exactly one choice. For `tool_calls`, require exactly one call of type
`function`, an allowed name, and string arguments, then return a
`NativeToolTurn(tool_call=...)`. For `stop`, require no calls and non-blank
message content, then return `NativeToolTurn(final_answer=...)`. Map `length` to
`ProviderOutputLimitError`; every other or mixed shape raises a content-free
`ProviderResponseError`.

- [ ] **Step 5: Add failing fail-closed response tests**

Parameterize these cases and assert `ProviderResponseError` with no raw sentinel
in `str(error)` or `_provider_exception_surfaces(error)`:

- `tool_calls` with zero calls;
- `tool_calls` with two calls;
- non-function call type;
- unknown function name;
- non-string arguments;
- `stop` with a tool call;
- `stop` with blank text;
- `content_filter`, `insufficient_system_resource`, and unknown finish reasons.

Add separate assertions that `length` raises `ProviderOutputLimitError`, the
per-call `max_tokens` override is sent, and one transient HTTP error is handled
by the existing retry policy without changing the logical result.

- [ ] **Step 6: Run Task 2's green gate and commit**

Run:

```powershell
& $nativePython -m pytest tests/test_deepseek_provider.py -q
& $nativePython -m ruff check src/deep_research/providers/deepseek_provider.py tests/test_deepseek_provider.py
git diff --check
```

Expected: PASS. Commit:

```powershell
git add -- src/deep_research/providers/deepseek_provider.py tests/test_deepseek_provider.py
git commit -m "feat(deepseek): support native ReAct tool turns"
```

---

### Task 3: Implement OpenAI native ReAct parity

**Files:**
- Modify: `src/deep_research/providers/openai_provider.py`
- Test: `tests/test_openai_provider.py`

**Interfaces:**
- Consumes: the same provider-neutral `ToolDefinition` sequence.
- Produces: `OpenAIChatProvider.complete_react(...) -> NativeToolTurn` with the same one-call-or-final contract.

- [ ] **Step 1: Extend the fake Responses builder**

Allow the `response(...)` helper in `tests/test_openai_provider.py` to receive
`status`, `output`, and `incomplete_reason`. A native call item has this exact
fake shape:

```python
SimpleNamespace(
    type="function_call",
    name="web_search",
    arguments='{"query":"qec capacity"}',
)
```

- [ ] **Step 2: Write and run the failing OpenAI parity test**

Call `complete_react` and assert `responses.create_calls[0]` includes:

```python
"tools": [
    {
        "type": "function",
        "name": "web_search",
        "description": "Search the web.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    }
],
"tool_choice": "auto",
```

Run:

```powershell
& $nativePython -m pytest tests/test_openai_provider.py -q -k native_react
```

Expected: FAIL because `complete_react` is absent.

- [ ] **Step 3: Implement Responses native-tool parsing**

Add the same public method signature as Task 2. Send `input`, native function
tools, `tool_choice="auto"`, and `max_output_tokens`. Accept output containing
arbitrary reasoning items plus exactly one `type="function_call"`, but never
retain or trace reasoning items. Accept non-blank `output_text` only when no
function call exists. Reject mixed final text and function calls, multiple calls,
unknown names, malformed call fields, and malformed status with safe
`ProviderResponseError` values. Map `incomplete_details.reason ==
"max_output_tokens"` to `ProviderOutputLimitError`.

- [ ] **Step 4: Add parity failure, telemetry, and privacy tests**

Mirror Task 2's one-call, allow-list, empty-final, mixed-shape, output-limit,
budget, retry, and sentinel privacy cases. Assert span inputs record
`operation="react_tool_turn"` and `tool_count`, not tool names or arguments.

- [ ] **Step 5: Run Task 3's green gate and commit**

Run:

```powershell
& $nativePython -m pytest tests/test_openai_provider.py -q
& $nativePython -m pytest tests/test_provider_factory.py tests/test_runtime/test_assembly.py -q
& $nativePython -m ruff check src/deep_research/providers/openai_provider.py tests/test_openai_provider.py
git diff --check
```

Expected: PASS and no provider-factory change. Commit:

```powershell
git add -- src/deep_research/providers/openai_provider.py tests/test_openai_provider.py
git commit -m "feat(openai): support native ReAct tool turns"
```

---

### Task 4: Route every model-directed ReAct loop through one shared adapter

**Files:**
- Modify: `src/deep_research/agents/base.py`
- Modify: `src/deep_research/agents/steps.py`
- Modify: `src/deep_research/agents/prompts.py`
- Modify: `src/deep_research/agents/__init__.py`
- Modify: `src/deep_research/agents/researcher.py`
- Modify: `src/deep_research/agents/source_evaluator.py`
- Modify: `src/deep_research/agents/fact_checker.py`
- Modify: `src/deep_research/agents/synthesizer.py`
- Modify: `src/deep_research/agents/critic.py`
- Modify: `src/deep_research/runtime/assembly.py`
- Modify: `src/deep_research/evaluation/factory.py`
- Modify: `tests/agent_fakes.py`
- Test: `tests/test_agents/test_base.py`
- Test: `tests/test_agents/test_steps.py`
- Test: `tests/test_agents/test_prompts.py`
- Test: `tests/test_agents/test_planner.py`
- Test: `tests/test_agents/test_researcher.py`
- Test: `tests/test_agents/test_source_evaluator.py`
- Test: `tests/test_agents/test_fact_checker.py`
- Test: `tests/test_agents/test_synthesizer.py`
- Test: `tests/test_agents/test_critic.py`
- Test: `tests/test_runtime/test_assembly.py`
- Test: `tests/test_evaluation/test_factory.py`

**Interfaces:**
- Consumes: `AgentToolset.provider_definitions()` and `AgentCompleter.complete_react`.
- Produces: `react_decision_from_native_turn(...)` and `BaseAgent._complete_react_decision(...)`, the only production bridge into `run_react_loop`.

- [ ] **Step 1: Write the native-turn adapter tests**

In `tests/test_agents/test_steps.py`, assert:

```python
def test_a_native_tool_call_becomes_an_internal_react_decision() -> None:
    decision = react_decision_from_native_turn(
        NativeToolTurn(
            model="deepseek-v4-flash",
            usage=TokenUsage(),
            tool_call=NativeToolCall(
                tool_name="web_search", arguments_json='{"query":"qec"}'
            ),
        )
    )
    assert decision == ReActDecision(
        thought="Selected tool through provider-native calling.",
        action="use_tool",
        tool_name="web_search",
        tool_input_json='{"query":"qec"}',
    )


def test_a_native_final_answer_becomes_an_internal_finish_decision() -> None:
    decision = react_decision_from_native_turn(
        NativeToolTurn(
            model="deepseek-v4-flash",
            usage=TokenUsage(),
            final_answer="The available evidence is sufficient.",
        )
    )
    assert decision.action == "finish"
    assert decision.thought == "Finished without another tool call."
    assert decision.tool_input_json == "{}"
```

Update `ReActDecision`'s docstring: it is internal loop state, no longer a
provider structured-output schema.

- [ ] **Step 2: Run the adapter tests and confirm RED**

Run:

```powershell
& $nativePython -m pytest tests/test_agents/test_steps.py -q -k native
```

Expected: FAIL because the adapter is absent.

- [ ] **Step 3: Split the provider protocols and add the shared helper**

Keep `StructuredCompleter` unchanged for the Judge. Import
`runtime_checkable`, decorate the new combined protocol, and add:

```python
@runtime_checkable
class AgentCompleter(StructuredCompleter, Protocol):
    async def complete_react(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolDefinition],
        *,
        agent_name: str | None = None,
        max_tokens: int | None = None,
    ) -> NativeToolTurn:
        raise NotImplementedError
```

Type `BaseAgent.provider` and its constructor as `AgentCompleter`. Update the
provider annotations on all six concrete agents, `runtime.assembly.build_agent`,
`runtime.assembly.build_agents`, `runtime.assembly.build_runtime`, and the
target side of `evaluation.factory` to `AgentCompleter`. Keep every Judge
factory and Judge evaluator annotation as `StructuredCompleter`; Judge wiring
must neither require nor invoke the new tool capability merely because target
adapters implement it. Add offline
assembly/factory tests whose injected provider implements only
`complete_structured` and assert construction fails the runtime-checkable
protocol guard before an agent runs. Add `complete_react` recording methods to
the existing `RecordingProvider` fakes in `tests/test_runtime/test_assembly.py`
and `tests/test_evaluation/test_factory.py`, then update the shared fake to
implement both capabilities. Add:

```python
if not isinstance(provider, AgentCompleter):
    raise AgentConfigurationError(
        "agent provider must implement structured and native ReAct completion"
    )
```

```python
async def _complete_react_decision(
    self,
    task: AgentTask,
    *,
    iteration: int,
) -> ReActDecision:
    turn = await self._provider.complete_react(
        render_react_messages(
            system_prompt=self.system_prompt(task),
            task=task,
            scratchpad=self._scratchpad.recent(
                self._config.prompt_context_entries
            ),
            iteration=iteration,
            max_iterations=self._config.max_iterations,
        ),
        self._toolset.provider_definitions(),
        agent_name=self._name,
        max_tokens=self._config.react_decision_max_tokens,
    )
    return react_decision_from_native_turn(turn)
```

The inherited `BaseAgent.run` closure calls this helper. Keep
`complete_output` on the structured method.

- [ ] **Step 4: Remove the simulated tool protocol from ReAct prompts**

Replace `REACT_RESPONSE_CONTRACT` with:

```python
NATIVE_REACT_RESPONSE_CONTRACT = (
    "Call at most one tool supplied with this request when another lookup or "
    "action is needed. Use provider-native tool calling; never write or imitate "
    "a tool call in text, JSON, XML, DSML, or a Markdown fence. When no tool is "
    "needed, return the final answer directly."
)
```

Remove the `descriptors` parameter, `## Tools`, `render_tool_catalog`, and the
`action`/`tool_name`/`tool_input_json` response contract from
`render_react_messages`. Render `## How to respond` with the native contract,
after notes and budget. Remove the obsolete exports from `agents/__init__.py`
and replace their import-surface assertions in `tests/test_imports.py` with
`AgentCompleter`, `NATIVE_REACT_RESPONSE_CONTRACT`, and the three native
provider contracts.

Update prompt tests to assert ReAct text contains none of `## Tools`,
`tool_input_json`, `ReActDecision`, or descriptor descriptions, while retaining
task, guidance, notes, and budget.

- [ ] **Step 5: Update the shared fake and BaseAgent tests**

Add `complete_react` to `ScriptedCompleter`. It pops the existing scripted
`ReActDecision`, converts a tool decision to `NativeToolTurn(tool_call=...)` and
a finish decision to `NativeToolTurn(final_answer=...)`, and records the passed
tool definitions in `react_calls`. Keep `complete_structured` for final outputs
only and raise if its schema is `ReActDecision`.

Update `test_run_renders_the_task_tools_and_scratchpad_into_the_prompt` to assert
the tool is present in `react_calls[0].tools` but absent from both message bodies.
Update thought assertions to the deterministic summaries from Step 1.

- [ ] **Step 6: Replace every duplicated ReAct closure**

In `researcher.py`, `fact_checker.py`, and `critic.py`, replace each direct
`complete_structured(render_react_messages(...), ReActDecision, ...)` call with:

```python
return await self._complete_react_decision(task, iteration=iteration)
```

Planner inherits the same path from `BaseAgent`. Remove now-unused
`render_react_messages` and `ReActDecision` imports from the three specialized
modules where possible.

- [ ] **Step 7: Add a cross-agent mutation guard**

Add one parameterized offline test covering Planner, Researcher, Fact Checker,
and Critic. Run each with `ScriptedCompleter` and controlled fake tools, then
assert:

```python
assert completer.react_calls
assert all(schema_name != "ReActDecision" for schema_name, _, _ in completer.calls)
```

Add control assertions that Source Evaluator and Synthesizer make zero
`complete_react` calls in their existing full-run tests.

- [ ] **Step 8: Run Task 4's green gate and commit**

Run:

```powershell
& $nativePython -m pytest tests/test_imports.py tests/test_agents/test_base.py tests/test_agents/test_steps.py tests/test_agents/test_prompts.py tests/test_agents/test_planner.py tests/test_agents/test_researcher.py tests/test_agents/test_source_evaluator.py tests/test_agents/test_fact_checker.py tests/test_agents/test_synthesizer.py tests/test_agents/test_critic.py tests/test_runtime/test_assembly.py tests/test_evaluation/test_factory.py -q
& $nativePython -m ruff check src/deep_research/agents src/deep_research/runtime/assembly.py src/deep_research/evaluation/factory.py tests/agent_fakes.py tests/test_imports.py tests/test_agents tests/test_runtime/test_assembly.py tests/test_evaluation/test_factory.py
git diff --check
```

Expected: PASS. Commit:

```powershell
git add -- src/deep_research/agents/base.py src/deep_research/agents/steps.py src/deep_research/agents/prompts.py src/deep_research/agents/__init__.py src/deep_research/agents/researcher.py src/deep_research/agents/source_evaluator.py src/deep_research/agents/fact_checker.py src/deep_research/agents/synthesizer.py src/deep_research/agents/critic.py src/deep_research/runtime/assembly.py src/deep_research/evaluation/factory.py tests/agent_fakes.py tests/test_imports.py tests/test_agents/test_base.py tests/test_agents/test_steps.py tests/test_agents/test_prompts.py tests/test_agents/test_researcher.py tests/test_agents/test_source_evaluator.py tests/test_agents/test_fact_checker.py tests/test_agents/test_synthesizer.py tests/test_agents/test_critic.py tests/test_runtime/test_assembly.py tests/test_evaluation/test_factory.py
git commit -m "refactor(agents): share native ReAct tool selection"
```

---

### Task 5: Apply the effective Critic/Judge prompt-conformance rules to all six agents

**Files:**
- Modify: `src/deep_research/agents/prompts.py`
- Modify: `src/deep_research/agents/planner.py`
- Modify: `src/deep_research/agents/researcher.py`
- Modify: `src/deep_research/agents/source_evaluator.py`
- Modify: `src/deep_research/agents/fact_checker.py`
- Modify: `src/deep_research/agents/synthesizer.py`
- Test: `tests/test_agents/test_prompts.py`
- Test: `tests/test_agents/test_planner.py`
- Test: `tests/test_agents/test_researcher.py`
- Test: `tests/test_agents/test_source_evaluator.py`
- Test: `tests/test_agents/test_fact_checker.py`
- Test: `tests/test_agents/test_synthesizer.py`
- Test: `tests/test_agents/test_critic.py`
- Test: `tests/test_evaluation/test_judging.py`

**Interfaces:**
- Consumes: existing field-level instructions and provider-appended JSON schema.
- Produces: `STRUCTURED_REPLY_FORMAT`, `PLANNER_PLAN_SYSTEM_PROMPT`, H1 request envelopes, an explicit prompt-conformance matrix, and cross-agent prompt/transport congruence tests.

The implementation must satisfy this matrix:

| Operation class | Literal `JSON object` instruction | Request-owned H1 sections | Positive/negative cases | Score guidance |
| --- | --- | --- | --- | --- |
| Native ReAct turns | No; output selection is provider-native | Existing task structure | None | Not applicable |
| Planner, Researcher, Fact Checker, Synthesizer tool-free calls | Exactly one final reply-format instruction | Yes | No invented domain examples; retain explicit empty/no-evidence behavior where valid | Not applicable |
| Source Evaluator scoring | Exactly one final reply-format instruction | Yes | Dimension-specific strong/weak prose anchors; no static JSON object with a fake URL | One 0.0-1.0 direction, all three dimensions defined, current/superseded/undated recency cases explicit |
| Critic review | Preserve current wording | Preserve current H1 plus computed report fence | Preserve two complete schema-valid weak/strong JSON examples | Preserve complete 1-10 bands without routing-threshold leakage |
| Judge evaluation | Preserve current wording and fingerprint | Preserve current H1/H2 hierarchy | Preserve two complete schema-valid weak/strong JSON examples | Preserve complete 0.0-1.0 bands and explicit weights |

- [ ] **Step 1: Write the failing Planner mismatch test**

In `tests/test_agents/test_planner.py`, render `plan_messages` and assert the
developer message contains neither `query_memory` nor `web_search`, while
`PlannerAgent.system_prompt(task)` still names both for the native ReAct call.

Run:

```powershell
& $nativePython -m pytest tests/test_agents/test_planner.py -q -k tool_free
```

Expected: FAIL because `plan_messages` currently reuses
`PLANNER_SYSTEM_PROMPT`.

- [ ] **Step 2: Add the Planner's distinct tool-free finalization prompt**

Add this adjacent to `PLANNER_SYSTEM_PROMPT`:

```python
PLANNER_PLAN_SYSTEM_PROMPT = (
    "You are the planner of a multi-agent research system. Turn the research "
    "question, context, and completed scoping notes printed below into a final "
    "research plan. Everything needed for this structured plan is already in "
    "the request. Do not propose or describe another lookup."
)
```

Use it only in `plan_messages`; keep `PlannerAgent.system_prompt` returning the
tool-aware `PLANNER_SYSTEM_PROMPT` for native ReAct.

- [ ] **Step 3: Add one shared tool-free reply-format rule**

In `agents/prompts.py`, add and export:

```python
STRUCTURED_REPLY_FORMAT = (
    "Return exactly one JSON object matching the supplied response schema, "
    "with no Markdown fence and no text before or after it."
)
```

For `plan_messages`, `extraction_messages`, `scoring_messages`,
`claim_extraction_messages`, `claim_verification_messages`, and
`report_messages`:

- promote each request-owned section from `##` to `#`, matching the effective
  Critic/Judge separation between instruction-owned headings and nested data;
- keep the existing semantic response contract;
- append `# Reply format\n{STRUCTURED_REPLY_FORMAT}` as the final section,
  matching their explicit prose reply contracts;
- ensure this is the only prompt-owned output-shape sentence containing the
  literal phrase `JSON object`; field semantics may say `return`, but must not
  introduce a competing JSON convention;
- keep evidence and findings inside the existing one-line/JSON renderers so
  embedded content cannot create a peer request heading. If a builder preserves
  raw multi-line Markdown, use the Critic's computed-fence helper rather than a
  fixed fence or pseudo-XML delimiter.

Do not change the Critic's already-specialized reply format or the Judge prompt.
Do not copy the Judge's `extra="ignore"` exception or widened rationale safety
bound into agent schemas: measurements support those only for `JudgeVerdict`.
Do not claim the heading/reply-format changes alone caused the earlier validity
improvements; the six fresh canaries in Task 8 are the falsification test.

- [ ] **Step 4: Lock the scoring guidance and example scope**

In `tests/test_agents/test_source_evaluator.py`, add
`test_source_scoring_guidance_has_one_direction_and_anchor_for_every_dimension`.
Before the existing dimension definitions in `SOURCE_SCORING_INSTRUCTION`, add
this exact shared direction:

```python
"All three scores use one direction: 0.0 is weakest and 1.0 is strongest. "
"Use intermediate values in proportion to the evidence in the dossier.\n"
```

In the `recency` definition, retain the exact neutral rule and add: `A clearly
current version scores high; a demonstrably superseded source on a
time-sensitive topic scores low.` Assert the rendered scoring request contains
exactly one 0.0-to-1.0 direction, defines `authority`, `recency`, and `relevance`
once each, retains both the strong and weak authority cases, retains the
direct-versus-mention relevance cases, and retains `Use 0.5 when the excerpts
carry no dating signal at all`. Assert it does not ask the model for
`corroboration_score`, `overall_score`, or `low_confidence`, which remain locally
computed.

In the existing Critic and Judge prompt tests, parse every weak/strong example
with `json.loads`, validate it with `CritiqueDraft` or `JudgeVerdict`, and assert
the weak values fall inside the declared weak band and the strong values inside
the declared strong band. Assert no parsed example contains an angle-bracket
sentinel or a value rejected by its schema. Keep the Judge fingerprint at
`74b9cddfbbee`; these are preservation tests, not Judge changes.

For Planner, Researcher, Fact Checker, and Synthesizer, assert no production
prompt labels a domain-content example as weak, strong, positive, or negative.
Where the current contract permits an empty/no-evidence result, assert that
behavior remains explicit instead of demonstrating invented content.

- [ ] **Step 5: Add the cross-agent tool-free prompt inventory test**

Build the real messages for each tool-free structured operation using existing
fixtures. Check only the developer message against the finite registered tool
name set:

```python
REGISTERED_TOOL_NAMES = {
    "web_search",
    "web_scraper",
    "document_reader",
    "query_memory",
    "save_to_memory",
    "write_document",
}


@pytest.mark.parametrize("messages", TOOL_FREE_STRUCTURED_MESSAGE_CASES)
def test_tool_free_structured_system_prompts_advertise_no_tool(messages) -> None:
    developer = messages[0].content
    advertised = {
        name
        for name in REGISTERED_TOOL_NAMES
        if re.search(
            rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])",
            developer,
        )
    }
    assert advertised == set()
```

Construct `TOOL_FREE_STRUCTURED_MESSAGE_CASES` through the actual message
builders, not copied prompt strings. Include Planner plan, Researcher extraction,
Source Evaluator scoring, both Fact Checker operations, Synthesizer report, and
Critic review.

- [ ] **Step 6: Add heading and reply-format guards**

For every builder from Step 3, assert all request-owned heading lines start with
exactly `# `, semantic fields remain named, the last section is `# Reply format`,
the phrase `JSON object` appears exactly once, and no output example outside the
matrix above was added. Retain existing Critic tests proving its dynamic fence
cannot be closed by report content and its two score examples validate.

Add a Judge control assertion:

```python
assert judge_prompt_fingerprint(rubric_version=1) == "74b9cddfbbee"
```

This test must pass before and after Task 5; a Judge fingerprint move is a stop
condition, not a literal to update.

- [ ] **Step 7: Run Task 5's green gate and commit**

Run:

```powershell
& $nativePython -m pytest tests/test_agents/test_prompts.py tests/test_agents/test_planner.py tests/test_agents/test_researcher.py tests/test_agents/test_source_evaluator.py tests/test_agents/test_fact_checker.py tests/test_agents/test_synthesizer.py tests/test_agents/test_critic.py tests/test_evaluation/test_judging.py -q
& $nativePython -m ruff check src/deep_research/agents tests/test_agents tests/test_evaluation/test_judging.py
git diff --check
```

Expected: PASS with the Judge fingerprint unchanged. Commit:

```powershell
git add -- src/deep_research/agents/prompts.py src/deep_research/agents/planner.py src/deep_research/agents/researcher.py src/deep_research/agents/source_evaluator.py src/deep_research/agents/fact_checker.py src/deep_research/agents/synthesizer.py tests/test_agents/test_prompts.py tests/test_agents/test_planner.py tests/test_agents/test_researcher.py tests/test_agents/test_source_evaluator.py tests/test_agents/test_fact_checker.py tests/test_agents/test_synthesizer.py tests/test_agents/test_critic.py tests/test_evaluation/test_judging.py
git commit -m "fix(prompts): align structured calls with tool-free transport"
```

---

### Task 6: Record the target ReAct transport and prompt invalidation

**Files:**
- Modify: `src/deep_research/evaluation/config.py`
- Test: `tests/test_evaluation/test_config.py`

**Interfaces:**
- Produces: `target_react_transport(provider)`, experiment metadata, and target configuration fingerprint coverage.
- Preserves: dataset identity and Judge prompt/configuration fingerprints.

- [ ] **Step 1: Write failing transport-provenance tests**

Add:

```python
def test_target_react_transport_is_provider_specific() -> None:
    assert target_react_transport("deepseek") == "deepseek_chat_tools_auto_v1"
    assert target_react_transport("openai") == "openai_responses_tools_auto_v1"


@pytest.mark.parametrize(
    ("provider", "transport"),
    [
        ("deepseek", "deepseek_chat_tools_auto_v1"),
        ("openai", "openai_responses_tools_auto_v1"),
    ],
)
def test_target_react_transport_is_recorded(provider, transport) -> None:
    settings = ConfigSettings(llm=LLMConfig(provider=provider))
    metadata = experiment_metadata(build(settings=settings), settings)
    assert metadata["target_react_transport"] == transport
```

Also assert changing provider changes `configuration_fingerprint`, never the
dataset name, and never `judge_prompt_fingerprint(rubric_version=1)`. Add a
stronger coverage test that monkeypatches the `deepseek` value in
`_TARGET_REACT_TRANSPORT`, rebuilds otherwise-identical runtime configuration,
and observes a changed `configuration_fingerprint`; changing providers alone
does not prove the new field participates because provider is already part of
the application settings.

- [ ] **Step 2: Run the provenance tests and confirm RED**

Run:

```powershell
& $nativePython -m pytest tests/test_evaluation/test_config.py -q -k target_react_transport
```

Expected: FAIL because the target transport identifier is absent.

- [ ] **Step 3: Implement explicit transport provenance**

Add:

```python
_TARGET_REACT_TRANSPORT = {
    "deepseek": "deepseek_chat_tools_auto_v1",
    "openai": "openai_responses_tools_auto_v1",
}


def target_react_transport(provider: ProviderName) -> str:
    return _TARGET_REACT_TRANSPORT[provider]
```

Include `target_react_transport(settings.llm.provider)` in the payload used for
`configuration_fingerprint` and add `target_react_transport` to
`experiment_metadata`. Do not add it to the Judge fingerprint.

- [ ] **Step 4: Update the intentional target prompt pin**

Run this network-free command after Tasks 4-5:

```powershell
& $nativePython -c "from deep_research.evaluation.config import agent_prompt_fingerprint; print(agent_prompt_fingerprint('critic'))"
```

Replace `CRITIC_PROMPT_FINGERPRINT` in `tests/test_evaluation/test_config.py` with
the exact emitted 12-character value and update its comment to name this plan and
the shared native ReAct prompt change. Record all six newly emitted target prompt
fingerprints in the fix log during Task 7. Do not change the Judge pin.

- [ ] **Step 5: Run Task 6's green gate and commit**

Run:

```powershell
& $nativePython -m pytest tests/test_evaluation/test_config.py -q
& $nativePython -m ruff check src/deep_research/evaluation/config.py tests/test_evaluation/test_config.py
git diff --check
```

Expected: PASS. Commit:

```powershell
git add -- src/deep_research/evaluation/config.py tests/test_evaluation/test_config.py
git commit -m "chore(evaluation): fingerprint native ReAct transport"
```

---

### Task 7: Run the consolidated offline gate and obtain scoped review

**Files:**
- Modify: `docs/superpowers/2026-09-08-cross-agent-planner-fix-parity-fix-log.md`
- No production file change is expected after review unless a concrete finding produces a new RED test.

**Interfaces:**
- Produces: a reviewed, offline-green candidate and exact evidence for deciding whether paid validation may begin.

- [ ] **Step 1: Prove no production ReAct call uses structured `ReActDecision`**

Run:

```powershell
rg -n -U "complete_structured\([\s\S]{0,500}ReActDecision" src/deep_research/agents
rg -n "render_tool_catalog|## Tools|tool_input_json" src/deep_research/agents/prompts.py
```

Expected: both searches return no production match. `ReActDecision` remains only
as internal loop state and in tests.

- [ ] **Step 2: Run focused contracts/providers/agents tests**

Run:

```powershell
& $nativePython -m pytest tests/test_provider_contracts.py tests/test_deepseek_provider.py tests/test_openai_provider.py tests/test_provider_factory.py tests/test_runtime/test_assembly.py tests/test_agents tests/test_evaluation/test_config.py tests/test_evaluation/test_judging.py -q
```

Expected: PASS. Record the fresh count rather than copying a historical total.

- [ ] **Step 3: Run the full offline suite and static checks**

Run:

```powershell
& $nativePython -m pytest -q
& $nativePython -m ruff check .
git diff --check
```

Expected: no new failure relative to the branch's pre-task baseline. If the
branch still contains a named pre-existing failure, reproduce it at the Task 1
base before classifying it as unrelated; do not accept a count-only comparison.

- [ ] **Step 4: Verify frozen behavior and fingerprints**

Run this network-free check:

```powershell
& $nativePython -c "import yaml; from pathlib import Path; from deep_research.evaluation.judging import judge_prompt_fingerprint; raw=yaml.safe_load(Path('config.yaml').read_text(encoding='utf-8')); assert raw['llm']['max_tokens']==32768; assert raw['agents']['react_decision_max_tokens']==32768; assert raw['evaluation']['live_threshold']==0.75; assert judge_prompt_fingerprint(rubric_version=1)=='74b9cddfbbee'; print('frozen invariants: OK')"
```

Expected: `frozen invariants: OK`.

- [ ] **Step 5: Record the offline evidence**

Append one fix-log section containing:

- Task 1 base SHA and candidate SHA;
- exact production/test paths changed;
- focused and full pytest results;
- Ruff and `git diff --check` results;
- all six old/new target prompt fingerprints;
- unchanged Judge prompt fingerprint `74b9cddfbbee`;
- target transport identifiers for both providers;
- confirmation that cases, case versions, model, efforts, budgets, retries,
  thresholds, Judge semantics, Critic review semantics, and D2 wording did not
  change;
- confirmation that no network or paid command ran.

Do not include prompts, responses, tool arguments, secrets, or reasoning.

- [ ] **Step 6: Commit the evidence record**

Run:

```powershell
git add -- docs/superpowers/2026-09-08-cross-agent-planner-fix-parity-fix-log.md
git commit -m "docs: record native ReAct offline gate"
```

- [ ] **Step 7: Request task-scoped and whole-change review**

Use `superpowers:requesting-code-review` twice:

1. review Tasks 1-3 for provider contracts, request/response correctness,
   fail-closed behavior, retries, telemetry, and privacy;
2. review Tasks 4-7 for all-agent routing, prompt/transport congruence,
   Critic/Judge preservation, fingerprints, and evaluation isolation.

Provide the exact base and head from:

```powershell
git merge-base origin/codex/cross-agent-planner-fix-parity HEAD
git rev-parse HEAD
```

Any unresolved Critical or Important finding is NO-GO for Task 8. Fix each
accepted finding with a focused RED test, rerun the affected task gate and full
offline gate, commit it separately, and repeat the relevant review.

---

### Task 8: Validate native tool shape and all six agents with separately authorized paid gates

**Files:**
- Create before paid authorization: `docs/superpowers/2026-09-12-shared-native-react-live-validation.md`
- Modify after each batch: `docs/superpowers/2026-09-08-cross-agent-planner-fix-parity-fix-log.md`
- Create before paid authorization: `output/transport-probes/native-react-v1/native_react_shape_probe.py`
- Create after authorization: `output/transport-probes/native-react-v1/native_react_shape_probe_30.jsonl`; do not stage either ignored probe file.

**Interfaces:**
- Consumes: reviewed clean candidate from Task 7.
- Produces: one shape-gate verdict and six independent three-repetition canary verdicts.

- [ ] **Step 1: Verify the paid-run preconditions without contacting a service**

Check:

```powershell
git status --short --branch
git rev-parse HEAD
& $nativePython -c "import deep_research; print(deep_research.__file__)"
& $nativePython -m deep_research.evaluation list
```

Expected: reviewed commit, only preserved untracked `.deepseek-runs/` and
`tools/`, worktree-local import, and the six registered agents. Do not print or
inspect secret values.

- [ ] **Step 2: Build and dry-run the 30-request native shape inventory**

Create the ignored probe script with `apply_patch`. It must build the registered
`critic-live-review` state through the repository's case and dependency helpers,
call `CriticAgent.build_task`, render the real first spot-check messages, and
obtain the real `AgentToolset.provider_definitions()`. It calls only the reviewed
public `DeepSeekSchemaChatProvider.complete_react`; it must not copy private
provider serialization code from the earlier prompt-shape probes.

Implement two mandatory modes:

- `--dry-run --requests 30` injects a recording fake Chat Completions client,
  makes one offline `complete_react` call, and refuses success unless the captured
  wire request has the reviewed model, `reasoning_effort="max"`, thinking
  enabled, `max_tokens=32768`, two real Critic function definitions,
  `tool_choice="auto"`, no `response_format`, no simulated action schema, and
  `retry_count=0`. It also proves neither fake nor real tools execute.
- `--execute --requests 30 --output <path>` constructs the real provider with
  tracing disabled and `retry_count=0`, makes exactly 30 sequential first-attempt
  calls, never executes a selected tool, writes only bounded structural records,
  and exits non-zero when the predeclared gate in Step 3 fails.

Run the offline mode:

```powershell
& $nativePython output/transport-probes/native-react-v1/native_react_shape_probe.py --dry-run --requests 30
```

Its dry run prints only:

```json
{"agent":"critic","logical_requests":30,"provider_http_request_ceiling":30,"tool_execution_requests":0,"langsmith_requests":0,"effort":"max","max_tokens":32768,"tool_choice":"auto"}
```

Commit the reviewed probe script's SHA-256 to the validation document, present
that exact 30-request inventory to the user, and obtain explicit authorization.
Do not execute on authorization for a different count or service.

- [ ] **Step 3: Execute and classify the 30-request shape gate**

After exact authorization, run:

```powershell
& $nativePython output/transport-probes/native-react-v1/native_react_shape_probe.py --execute --requests 30 --output output/transport-probes/native-react-v1/native_react_shape_probe_30.jsonl
```

For each response retain only run number, outcome class, finish category,
selected allow-listed tool name, whether arguments decode to an object, usage,
and response character length. Never retain content or arguments.

The predeclared pass gate is all of:

- 0/30 DSML or other tool markup in ordinary text;
- 0/30 fenced or JSON action envelopes in ordinary text;
- 0/30 malformed native envelopes;
- 0/30 unknown or multiple calls;
- 0/30 provider or output-limit failures;
- every call is one allow-listed native call or one non-blank final answer;
- at least one native tool call is observed;
- exactly 30 provider requests and zero repair requests.

Any miss is FAIL. Do not reinterpret zero as equivalence, rerun the same batch,
or proceed to canaries.

- [ ] **Step 4: Predeclare the six canary request ceilings**

With `max_iterations=5`, one native request per ReAct iteration, at most two
provider attempts per structured call, and at most two Judge attempts, the
logical DeepSeek request ceilings for three repetitions are:

| Agent | Maximum per repetition | Maximum for three repetitions |
| --- | ---: | ---: |
| Planner | 11 | 33 |
| Researcher | 23 | 69 |
| Source Evaluator | 4 | 12 |
| Fact Checker | 39 | 117 |
| Synthesizer | 4 | 12 |
| Critic | 9 | 27 |

These are authorization ceilings, not expected spend. Before each agent, run a
network-free inventory against its actual registered live case and report:

- three repetitions;
- the table ceiling and actual case-derived ceiling when lower;
- DeepSeek target and Judge logical calls;
- the configured HTTP retry multiplier separately;
- possible Tavily/search, memory, document-read, and document-write calls;
- LangSmith evaluation usage;
- fresh output namespace.

Obtain separate explicit authorization for that one agent only.

- [ ] **Step 5: Run three one-repetition canaries sequentially**

The checked-in live setting is intentionally one repetition. For one authorized
agent at a time, run the command three times with distinct experiment prefixes;
this matches the existing Critic canary method, keeps each artifact attributable,
and requires no config edit or unsupported environment override. For Planner:

```powershell
& $nativePython -m deep_research.evaluation agent planner --tier live --experiment-prefix native-react-planner-r1 --output-directory output/evaluations/native-react-planner
if ($LASTEXITCODE -ne 0) { throw "Planner native ReAct canary r1 failed" }
& $nativePython -m deep_research.evaluation agent planner --tier live --experiment-prefix native-react-planner-r2 --output-directory output/evaluations/native-react-planner
if ($LASTEXITCODE -ne 0) { throw "Planner native ReAct canary r2 failed" }
& $nativePython -m deep_research.evaluation agent planner --tier live --experiment-prefix native-react-planner-r3 --output-directory output/evaluations/native-react-planner
```

Replace only the agent name, prefix, failure text, and output directory for later
individually authorized batches, in this order:

1. Planner;
2. Researcher;
3. Source Evaluator;
4. Fact Checker;
5. Synthesizer;
6. Critic.

Stop the sequence on the first failed batch. Do not spend a later agent's
authorization early or reuse an earlier authorization.

- [ ] **Step 6: Apply the canary acceptance rules**

Every agent requires:

- 3/3 repetitions completed;
- all existing hard gates passed;
- Judge `status="scored"` with no fallback or diagnostic;
- aggregate quality at or above the unchanged threshold;
- reviewed target ReAct transport and target prompt fingerprint recorded;
- Judge prompt fingerprint exactly `74b9cddfbbee`;
- no `react_decision` provider fallback;
- no DSML, fenced action envelope, malformed native call, unknown tool, multiple
  call, or unexpected provider repair.

Planner, Researcher, Fact Checker, and Critic additionally require at least one
successfully executed native tool call across their three repetitions and no
prohibited call. Source Evaluator and Synthesizer must make zero model-selected
native tool calls.

- [ ] **Step 7: Record and commit each paid verdict without raw content**

After each batch, record commit, dirty state, exact request count spent, services,
case id/version, fingerprints, transport id, hard gates, Judge status, aggregate
quality, native tool-call counts, fallback diagnostics, artifact paths, and the
PASS/FAIL decision. Commit only the two documentation files after each batch;
never stage `output/`, `.deepseek-runs/`, or `tools/`.

---

### Task 9: Resume the Critic calibration sequence only after cross-agent readiness

**Files:**
- Modify: `docs/superpowers/2026-09-12-critic-decision2-canary-report.md`
- Modify: `docs/superpowers/2026-09-12-critic-calibration-recommendation-request.md`
- Modify: `docs/superpowers/2026-09-08-cross-agent-planner-fix-parity-fix-log.md`

**Interfaces:**
- Consumes: six passing canary verdicts from Task 8.
- Produces: an evidence-backed restart point for Decision 2, then Decision 1.

- [ ] **Step 1: Reinterpret Decision 2 on the clean native-tool Critic batch**

Use only the three new Critic repetitions. Confirm that the lenient
`unsupported_claims` wording remains in the prompt, count the lists, scores,
routes, hard gates, Judge scores, and any target diagnostics, and compare them
with the pre-native D2 artifact without treating runs with transport fallback as
quality observations.

- [ ] **Step 2: Apply the original D2 gate without relaxation**

Decision 2 remains accepted only if the three native-tool repetitions have no
target fallback, all hard gates pass, Judge feedback is scored, and the lenient
definition does not create a new false-negative unsupported-claim pattern. If it
fails, stop with the typed evidence; do not alter the acceptance threshold.

- [ ] **Step 3: Keep Decision 1 separate and later**

Only after D2 passes, change the Critic live case reference expectation in
`src/deep_research/evaluation/cases/critic.py`, bump that case's version from 1
to 2, update the pinned reference in
`tests/test_evaluation/test_cases_critic.py`, and run its separately authorized
three-repetition canary. Do not combine this case calibration change with the
native transport or prompt rollout artifacts.

- [ ] **Step 4: Close the plan**

Update the three documentation files with final request accounting, artifact
namespaces, fingerprints, and explicit readiness verdicts for all six agents.
Run documentation tests, `git diff --check`, and commit:

```powershell
git add -- docs/superpowers/2026-09-12-critic-decision2-canary-report.md docs/superpowers/2026-09-12-critic-calibration-recommendation-request.md docs/superpowers/2026-09-08-cross-agent-planner-fix-parity-fix-log.md
git commit -m "docs: close shared native ReAct validation"
```

Do not declare the Critic or the six-agent system ready before Tasks 1-8 pass.

---

## Plan Self-Review

### Spec coverage

- Shared native tool boundary: Tasks 1-4.
- DeepSeek measured `auto` behavior and forced-choice prohibition: Task 2 and Global Constraints.
- OpenAI offline parity: Task 3.
- All four model-directed ReAct agents on one implementation: Task 4.
- Source Evaluator and Synthesizer no-native-tool controls: Tasks 4, 5, and 8.
- Critic/Planner tool-free prompt mismatch prevention: Task 5.
- Proven Critic/Judge heading, reply-shape, and score-example scope: Task 5.
- Critic review and Judge semantic preservation: Global Constraints and Tasks 5-7.
- Target transport provenance and prompt invalidation: Task 6.
- Secret-safe, fail-closed behavior: Tasks 1-3 and Global Constraints.
- Full offline evidence and two-stage review: Task 7.
- Separately authorized shape probe and six-agent canaries: Task 8.
- D2 before D1 calibration sequencing: Task 9.

### Placeholder scan

The plan contains no deferred implementation marker, unknown file, unspecified
error-handling instruction, or fabricated future hash. The one hash that must be
computed after prompt implementation has an exact network-free command and an
exact destination. Paid request ceilings and pass/fail rules are explicit.

### Type consistency

- Both providers consume `Sequence[ToolDefinition]` and return
  `NativeToolTurn`.
- `AgentCompleter` combines `StructuredCompleter` and `complete_react`; the Judge
  remains typed only against `StructuredCompleter`.
- `BaseAgent._complete_react_decision` is the sole adapter to internal
  `ReActDecision`.
- `NativeToolCall.arguments_json` feeds the unchanged
  `ReActDecision.tool_input_json` and unchanged `parse_tool_input` path.
- `AgentToolset.provider_definitions()` preserves the existing allow-list and
  declaration order.
- No provider module imports an agent model, so the dependency direction remains
  agents -> providers.

## Execution Handoff

Recommended mode: **Subagent-Driven Development**. Use a fresh implementation
worker for each task and the two-stage review gates specified in Task 7. Inline
execution with `superpowers:executing-plans` is also valid, using checkpoints
after Tasks 1, 3, 5, and 7. Neither mode authorizes Task 8's paid calls.
