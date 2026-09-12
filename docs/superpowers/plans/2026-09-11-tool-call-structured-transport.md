# Tool-Call Structured Transport Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans only when implementation is authorized. This document is the reviewed implementation plan; its review and commit do not authorize paid calls or claim the agents are fixed. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Test whether a validated tool-call transport improves structured-output reliability for all six DeepSeek target agents, retaining the current Responses transport until the evidence supports a rollout.

**Architecture:** Keep one repair loop in `_DeepSeekSchemaStructuredProvider.complete_structured`. Select the attempt through a target-only hook on `DeepSeekSchemaChatProvider`; `DeepSeekJudgeProvider` always selects Responses `json_schema`. Tool arguments still require local Pydantic validation. A forced function invocation is not a guarantee of valid JSON or correct output.

**Tech Stack:** Python 3.12, Pydantic v2, pytest + pytest-asyncio, Ruff, the installed OpenAI SDK against DeepSeek, and the existing evaluation harness.

**References:** `docs/superpowers/specs/2026-09-11-critic-live-call-production-readiness-design.md`, `docs/superpowers/2026-09-11-critic-readiness-confirmation.md`, and `docs/superpowers/2026-09-11-critic-readiness-final-canary.md`.

## Review findings addressed

Reviewed against local and remote `a24c62ef9213a82fbea271356fe877e6d21c9c32`, branch `codex/cross-agent-planner-fix-parity`. The tracked tree matched; `.deepseek-runs/` and `tools/` already contained untracked work. Preserve both.

| Severity | Finding in the reviewed version | Impact and amendment |
| --- | --- | --- |
| **Critical** | Task 0 omitted the thinking toggle in its alleged off mode, used only 512 output tokens, treated any parseable JSON as success, and probed `high` although the Critic evaluation uses `max`. | **Invalidates the whole plan's proceed gate.** Explicitly disable thinking in controls, use the configured budget, distinguish inconclusive failures, validate the exact tool and schema, and cover production and evaluation settings. |
| **Critical** | Task 3 changed the shared base's dispatch. `DeepSeekJudgeProvider` inherits that method, so it would switch to tools too. | **Invalidates Task 3 and every downstream quality comparison.** Add a target-only hook, with the base pinned to Responses and a judge regression under every setting. |
| **Critical** | The extraction signature was false: both loops clear `messages`, `agent_name`, and `instruction`. The extracted loop retained a bound `attempt_call`, and its new caller retained the original prompts and provider in traceback locals. | **Invalidates Task 3's extraction and privacy contract.** Keep the loop in its existing frame, clear the bound callable, and verify public exception graphs with harmless sentinels. |
| **Critical** | `tool_call` as a global default plus the OpenAI validator rejects `LLMConfig(provider="openai")`, the standard config fixture, and provider factories. Task 1's own positive OpenAI assertion cannot pass. | **Invalidates Task 1.** Use provider-neutral `auto`, initially resolving to `json_schema`; reject only explicitly unsupported OpenAI/tool combinations. Promote DeepSeek's automatic selection only after Task 7. |
| **Important** | Prose, missing tools, and multiple calls raise `ProviderResponseError`, so they never reach attempt 2. The parser ignores function name/type; the repair asks for plain JSON even in tool mode. | Treat invalid tool envelopes as bounded `schema_output` diagnostics, validate name and type, retry once using a transport-specific instruction, and never execute the synthetic function. |
| **Important** | Task 2 expects public-method tests to pass before Task 3 connects that method; Task 3 expects only six target-test failures despite also changing the judge. | Task 2 tests the private attempt directly. Add public integration tests only with dispatch in Task 3; pin the five Responses-specific target tests in that same task. Every task ends green relative to baseline. |
| **Important** | `JudgeVerdict` labelled `synthesizer` and source-string scans do not establish real agent coverage. Flipping a default cannot invalidate tests that explicitly set `tool_call`. | Exercise the seven actual draft schemas and four ReAct call sites through the factory, assert shared provider wiring, and use a targeted dispatch mutation. |
| **Important** | Task 5 demands textual braces and supplies a score of 8, competing with the tool channel and anchoring the score. Four clean runs are called decisive; OpenAI conclusions are inferred from DeepSeek. | Use a transport-neutral format reminder without a scored example. Pre-register a meaningful canary, report its limitations, and keep OpenAI decisions dependent on OpenAI evidence. |
| **Minor** | The spec path omits `specs/`; Task 6 depends on itself; five Responses tests are called six; the original test arithmetic is wrong; a commit placeholder is unnecessary. | Correct paths/dependencies, compute the SHA in PowerShell, and count collected parametrized cases. Original code adds **16** tests (5 + 5 + 2 + 2 + 2), predicting 2102 passes from 2086, not 2098. |

## Provider evidence and limits

Checked 2026-09-11. The [thinking-mode guide](https://api-docs.deepseek.com/guides/thinking_mode/) says thinking defaults to enabled and documents explicit toggles and reasoning round-tripping for tool conversations. The [Chat Completions reference](https://api-docs.deepseek.com/api/create-chat-completion/) documents the five `tool_choice` values/shapes and warns that function arguments can be invalid JSON. It does not document `parallel_tool_calls`; do not send it or rely on it to ensure one call. The [Responses compatibility guide](https://api-docs.deepseek.com/guides/responses_api/) explicitly says that parameter is ignored on Responses.

The reports [DeepSeek #1376](https://github.com/deepseek-ai/DeepSeek-V3/issues/1376) and [Pydantic AI #5193](https://github.com/pydantic/pydantic-ai/issues/5193) are evidence of forced-choice failures under V4 thinking, not proof of today's behavior for every endpoint. `auto` and omission permit prose; they do not satisfy a forced-call hypothesis. The [tool guide](https://api-docs.deepseek.com/guides/tool_calls/) also describes strict tool schemas on `/beta`, including unsupported string/array bounds and required-property rules. Beta strict mode and Responses function tools are additional experiments, not a drop-in version of this Chat Completions plan. Do not silently change endpoints, strip constraints, or claim all tool calling is impossible if this probe fails.

**Assessment:** tool arguments are a reasonable hypothesis to test; unforced tool calls alone are not an established fix for this provider's intermittent failures. Preserve the current fallback if the forced shape cannot be demonstrated with the configured thinking settings.

## Global constraints

- Work only in `C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.worktrees\cross-agent-planner-fix-parity`, branch `codex/cross-agent-planner-fix-parity`. Verify branch and tracked status before edits and commits; never stage unrelated untracked files.
- Run commands from this worktree root. At the start of **each PowerShell session**, establish these variables. Every Python invocation below uses this source binding; the shared interpreter's editable install can point elsewhere.

  ```powershell
  $transportRoot = (Get-Location).Path
  $transportPython = 'C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.venv\Scripts\python.exe'
  $env:PYTHONPATH = Join-Path $transportRoot 'src'
  & $transportPython -c "import deep_research; print(deep_research.__file__)"
  git branch --show-current
  git status --short
  ```

- Historical full gate at `a2ecc1c`: `3 failed, 2086 passed, 1 deselected`. The named failures are `tests/test_evaluation/test_runner.py::test_a_live_experiment_requests_one_repetition`, `tests/test_evaluation/test_targets.py::test_the_ledger_records_real_services_for_a_live_run`, and `tests/test_evaluation/test_targets.py::test_a_live_researcher_records_only_source_url_fingerprints`. Reproduce and compare failure reasons, not just counts. Do not repair these in this transport plan or assume an unrelated new failure must have been caused by this change.
- Preserve all five output budgets at `32768`, retry policy, temperature, thinking/effort configuration, frozen cases, rubrics, weights, `0.75` live threshold, domain validators, and fallback behavior. Two **structured attempts** maximum; existing transient HTTP retries are separate and retain their own budget.
- Never store live response bodies, prompts, secrets, or reasoning content in review evidence or diagnostics. Offline tests may use synthetic sentinel values. Repair receives only original caller messages, bounded diagnostics, and the schema/instructions; it must not replay the failed assistant response.
- No paid DeepSeek, LangSmith, search, or other provider calls during this plan review. Task 0 and Task 7 each require authorization for a concrete request count and services before executing. Read-only documentation checks and offline fake tests need no additional permission.
- Keep automatic transport selection on `json_schema` through Tasks 1–6. Paid tool candidates select it explicitly with `LLM_STRUCTURED_TRANSPORT=tool_call`. No runtime downgrade to a different transport, thinking mode, or model after an error.
- Each implementation task runs its named checks, Ruff on touched Python files, and `git diff --check`, and stages exact paths only. No knowingly broken intermediate commits.

## File structure and coverage

| File | Responsibility |
| --- | --- |
| `src/deep_research/utils/config.py`, `config.yaml`, `tests/test_config.py` | Selection, legacy compatibility, env override, delayed rollout |
| `src/deep_research/providers/deepseek_provider.py`, `tests/test_deepseek_provider.py` | Tool attempt, shared loop in place, bounded validation, judge isolation |
| `tests/test_runtime/test_assembly.py` | All six agents receive the selected provider |
| `src/deep_research/agents/prompts.py`, `tests/test_agents/test_critic.py` | Additive, transport-neutral Critic format reminder |
| `docs/superpowers/2026-09-08-cross-agent-planner-fix-parity-fix-log.md` | Capability decision, exact offline evidence, rollout decision |
| `docs/superpowers/2026-09-11-tool-call-transport-canary.md` | Pre-registered live results, provenance, explicit limitations |

Actual provider schema inventory at the reviewed commit:

| Agent | Draft schema(s) | Decision schema |
| --- | --- | --- |
| Planner | `ResearchPlanDraft` | `ReActDecision` |
| Researcher | `SubTopicFindingsDraft` | `ReActDecision` |
| Source Evaluator | `SourceScoresDraft` | none |
| Fact Checker | `ClaimsDraft`, `ClaimVerdictDraft` | `ReActDecision` |
| Synthesizer | `ReportDraft` | none |
| Critic | `CritiqueDraft` | `ReActDecision` |

The transport tool wraps these output contracts; it is distinct from application tools such as search. `ReActDecision.tool_input_json` remains a JSON **string**, and its existing action validators remain active. `JudgeVerdict` belongs only to the judge boundary.

---
### Task 0: Establish the exact capability gate

**Files:** temporary probe under a new task-owned directory in `output/transport-probes/`; update the fix log and this plan's selected-choice constant only after recording the outcome. Preserve the existing untracked `tools/probe_deepseek_tool_call.py`.

**Interfaces:** consumes loaded production settings and evaluation settings, produces `PROCEED_FUNCTION`, `PROCEED_REQUIRED`, `BLOCKED_CAPABILITY`, or `INCONCLUSIVE`. HTTP acceptance and output conformance are separate facts.

- [ ] **Step 1: Save this probe in a fresh directory and dry-run its request inventory**

Create a UUID-named subdirectory under `output/transport-probes/` and save the following Python block as `$transportProbe`. The probe deliberately resolves the repository from the working directory, not the temporary script's parent. It does not print provider bodies or error messages.

```powershell
$transportProbeDirectory = Join-Path 'output/transport-probes' ([guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $transportProbeDirectory | Out-Null
$transportProbe = Join-Path $transportProbeDirectory 'probe.py'
```

```python
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, ValidationError

from deep_research.observability import LangSmithRuntimeConfig, Tracker
from deep_research.providers.deepseek_provider import (
    DEEPSEEK_BASE_URL,
    DeepSeekChatProvider,
)
from deep_research.utils.config import load_config

AGENTS = (
    "planner",
    "researcher",
    "source_evaluator",
    "fact_checker",
    "synthesizer",
    "critic",
)


class ProbeAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    score: int
    rationale: str


def inventory():
    settings = load_config(str(Path.cwd() / "config.yaml"), strict=False)
    if settings.llm.provider != "deepseek":
        raise SystemExit("This capability probe requires the deepseek provider")
    tracker = Tracker(LangSmithRuntimeConfig(tracing_enabled=False))
    configurations = []
    for agent in AGENTS:
        effective = settings.llm.resolve_for(agent)
        production = settings.llm.model_copy(
            update={
                **effective.model_dump(),
                "model_overrides": {},
            }
        )
        evaluation = settings.llm.model_copy(
            update={
                "model": settings.evaluation.target_model,
                "thinking_mode": "enabled",
                "reasoning_effort": (
                    settings.evaluation.target_reasoning_effort_overrides.get(
                        agent, settings.evaluation.target_reasoning_effort
                    )
                ),
                "model_overrides": {},
            }
        )
        configurations.extend((production, evaluation))
    unique = {}
    for config in configurations:
        # Include an explicit disabled control for each configured model.
        for candidate in (
            config,
            config.model_copy(
                update={
                    "thinking_mode": "disabled",
                    "reasoning_effort": "none",
                }
            ),
        ):
            provider = DeepSeekChatProvider(candidate, tracker, client=object())
            _, request, _ = provider._request_options(None)
            key = json.dumps(request, sort_keys=True)
            unique[key] = request
    return settings, list(unique.values())


async def main(execute: bool):
    settings, requests = inventory()
    choices = {
        "function": {"type": "function", "function": {"name": "ProbeAnswer"}},
        "required": "required",
        "auto": "auto",
        "omitted": None,
        "none": "none",
    }
    print(
        json.dumps(
            {
                "request_count": len(requests) * len(choices),
                "settings": requests,
                "execute": execute,
            },
            sort_keys=True,
        )
    )
    if not execute:
        return
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not key.strip():
        raise SystemExit("DEEPSEEK_API_KEY is not set")
    async with AsyncOpenAI(
        api_key=key,
        base_url=DEEPSEEK_BASE_URL,
        max_retries=0,
        timeout=settings.llm.timeout,
    ) as client:
        for request in requests:
            for label, choice in choices.items():
                payload = {
                    **request,
                    "messages": [
                        {
                            "role": "user",
                            "content": "Call ProbeAnswer exactly once with score 1 and a "
                            "short rationale. Supply no ordinary text answer.",
                        }
                    ],
                    "tools": [
                        {
                            "type": "function",
                            "function": {
                                "name": "ProbeAnswer",
                                "description": "Submit the requested structured answer.",
                                "parameters": ProbeAnswer.model_json_schema(),
                            },
                        }
                    ],
                }
                if choice is not None:
                    payload["tool_choice"] = choice
                record = {"request": request, "choice": label}
                try:
                    response = await client.chat.completions.create(**payload)
                except Exception as error:
                    # Classify known rejection text locally without retaining it.
                    status = getattr(error, "status_code", None)
                    rejection = status == 400 and "tool_choice" in str(error).lower()
                    record.update(
                        outcome="request_failed",
                        error_type=type(error).__name__,
                        http_status=status,
                        tool_choice_rejection=rejection,
                    )
                    print(json.dumps(record, sort_keys=True))
                    continue
                options = getattr(response, "choices", None) or []
                finish = options[0].finish_reason if len(options) == 1 else None
                message = options[0].message if len(options) == 1 else None
                calls = getattr(message, "tool_calls", None) or []
                valid = False
                if len(calls) == 1 and calls[0].type == "function":
                    function = calls[0].function
                    if function.name == "ProbeAnswer":
                        try:
                            result = ProbeAnswer.model_validate_json(function.arguments)
                            valid = result.score == 1 and bool(result.rationale.strip())
                        except (ValidationError, TypeError):
                            pass
                record.update(
                    outcome="http_success",
                    finish_reason=finish,
                    tool_count=len(calls),
                    valid=valid,
                    complete=finish in {"stop", "tool_calls"},
                )
                print(json.dumps(record, sort_keys=True))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    asyncio.run(main(parser.parse_args().execute))
```

Run with the source binding from Global constraints. Set `$transportProbe` to the newly created script path, using `Join-Path` and the generated directory, rather than writing over an existing file. Run `& $transportPython $transportProbe` first. The current shipped config yields **15 requests**: `high`, `max`, and explicitly disabled, each with five choices. It is not a single cheap call. The `none` row is a negative control, never a usable candidate. No `parallel_tool_calls`, beta `strict`, or unverified shape is inserted.

- [ ] **Step 2: Authorize and run the enumerated probe**

Show the exact count, models, thinking modes, budget ceiling, and that this uses DeepSeek only, with no LangSmith tracing or external tools. Obtain authorization before running. Load credentials with the existing launcher; if the launcher is absent, stop and locate it instead of dumping `.env` or inventing credentials.

```powershell
$transportLauncher = '.superpowers\sdd\2026-09-08-cross-agent-planner-fix-parity\run_with_repo_env.py'
$transportEnvFile = 'C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.env'
if (-not (Test-Path -LiteralPath $transportLauncher)) { throw 'Missing approved launcher' }
& $transportPython $transportLauncher $transportEnvFile $transportPython $transportProbe --execute
if ($LASTEXITCODE -ne 0) { throw 'Probe did not complete' }
```

- [ ] **Step 3: Apply this decision table to each required production/evaluation setting**

Success means HTTP acceptance **and** a complete finish **and** exactly one correctly named function with locally valid arguments. A single success demonstrates feasibility, not reliability.

| Observation | Decision |
| --- | --- |
| Function-specific choice succeeds for every required setting | `PROCEED_FUNCTION`. Keep `_STRUCTURED_TOOL_CHOICE = 'function'` in Task 2. |
| Function-specific has a classified tool-choice rejection for a required setting, but `required` succeeds for every required setting | `PROCEED_REQUIRED`. Replace that constant with `'required'` in Task 2 before implementation. Keep cardinality validation; the API permits more than one call. A generic 400 does not establish this condition. |
| Success depends on different choice shapes per effective model/effort | Stop this version and amend the capability policy; do not choose a global shape that fails one target. |
| Only `auto` or omitted choice returns calls with the configured thinking mode | `BLOCKED_CAPABILITY` for the forced-call hypothesis. An unforced experiment requires its own decision and acceptance criteria; do not automatically enable it for all agents. |
| Only explicitly disabled thinking succeeds | Stop and report the tradeoff. Preserve configured thinking. This shows a restriction on the tested forced mechanism, not that all tool use and thinking are incompatible. |
| 401/403, timeout, connection failure, 429/5xx, malformed SDK result, or `length` | `INCONCLUSIVE`. Preserve the fallback; investigate the cause and obtain authorization for any extra calls. Do not call it unsupported transport. |
| Valid `auto` produces prose, or a forced request produces invalid arguments | Record envelope/conformance failure separately from HTTP rejection; one sample is not a reliability estimate. No unconditional rollout. |
| The `none` negative control returns a tool call | The endpoint may be ignoring `tool_choice`; stop and investigate rather than treating another row as proof of forcing. |
| No usable forced shape after a complete, non-infrastructure matrix | Stop Tasks 1–7. Keep the current Responses implementation. Reconsider prompt/model, Responses function tools, or beta strict mode as separate hypotheses; do not claim the current mechanism is universally best. |

- [ ] **Step 4: Commit the decision and close the gate**

Append safe matrix rows, current SDK version, candidate SHA, endpoint, exact request settings, and chosen outcome to the numbered fix log. Preserve the probe in its ignored output directory for reproducibility; do not delete the user's `tools/` files. If stopping, this evidence commit is the only requested repository change in execution. If proceeding with `required`, amend the constant and its expected test value in the plan before starting Task 1.

```powershell
git diff --check
git add docs/superpowers/2026-09-08-cross-agent-planner-fix-parity-fix-log.md docs/superpowers/plans/2026-09-11-tool-call-structured-transport.md
git commit -m "docs: record verified deepseek transport capability gate"
```

---

### Task 1: Add a compatible transport setting with a delayed default

**Files:** `src/deep_research/utils/config.py`, `config.yaml`, `tests/test_config.py`.

**Interfaces:** `structured_transport: Literal['auto', 'tool_call', 'json_schema'] = 'auto'`; `effective_structured_transport` returns a concrete transport. `auto` resolves to `json_schema` for both providers until Task 7. Explicit OpenAI `tool_call` is rejected. This preserves existing direct construction and provider-only environment overrides.

- [ ] **Step 1: Append the configuration tests and observe RED**

```python
@pytest.mark.parametrize("provider", ["deepseek", "openai"])
def test_auto_transport_preserves_the_existing_default(provider):
    config = LLMConfig(provider=provider)
    assert config.structured_transport == "auto"
    assert config.effective_structured_transport == "json_schema"


@pytest.mark.parametrize("transport", ["auto", "json_schema", "tool_call"])
def test_deepseek_transport_resolves_explicit_selection(transport):
    config = LLMConfig(structured_transport=transport)
    expected = "json_schema" if transport == "auto" else transport
    assert config.effective_structured_transport == expected


def test_openai_rejects_only_an_explicit_unsupported_transport():
    with pytest.raises(ValidationError, match="tool_call"):
        LLMConfig(provider="openai", structured_transport="tool_call")
    assert LLMConfig(provider="openai", structured_transport="json_schema")


def test_unknown_structured_transport_is_rejected():
    with pytest.raises(ValidationError):
        LLMConfig(structured_transport="magic")


def test_transport_environment_override(monkeypatch, config_path):
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("LLM_STRUCTURED_TRANSPORT", "tool_call")
    assert (
        load_config(str(config_path)).llm.effective_structured_transport == "tool_call"
    )


def test_shipped_auto_transport_keeps_provider_switching_compatible(
    monkeypatch,
    tmp_path,
):
    raw = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    assert raw["llm"]["structured_transport"] == "auto"
    # Copy YAML away from any sibling .env; this is an offline config check.
    path = tmp_path / "shipping.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.delenv("LLM_STRUCTURED_TRANSPORT", raising=False)
    assert load_config(str(path)).llm.effective_structured_transport == "json_schema"
```

Run `& $transportPython -m pytest tests/test_config.py -q -k transport -p no:cacheprovider`. Expect failures caused by the missing field/property, not an import problem.

- [ ] **Step 2: Add the field, property, validator, and override**

In `LLMConfig`, after `max_tokens`:

```python
    structured_transport: Literal["auto", "tool_call", "json_schema"] = "auto"


    @property
    def effective_structured_transport(self) -> Literal["tool_call", "json_schema"]:
        if self.structured_transport == "auto":
            # Task 7 may promote DeepSeek after the capability and quality gates.
            return "json_schema"
        return self.structured_transport


    @model_validator(mode="after")
    def validate_structured_transport(self) -> "LLMConfig":
        if self.provider == "openai" and self.structured_transport == "tool_call":
            raise ValueError(
                "structured_transport 'tool_call' is supported only for deepseek; "
                "use 'auto' or 'json_schema' with openai"
            )
        return self
```

`model_validator` is already imported. Add this entry after `LLM_MAX_TOKENS` in `_ENVIRONMENT_OVERRIDES`:

```python
    'LLM_STRUCTURED_TRANSPORT': ('llm', 'structured_transport'),
```

Add after `llm.max_tokens` in `config.yaml`:

```yaml
  # auto preserves Responses until the transport rollout gate passes.
  # tool_call is an explicit DeepSeek target experiment; the judge stays on Responses.
  # OpenAI supports auto/json_schema; explicit tool_call is rejected.
  structured_transport: auto
```

- [ ] **Step 3: Verify all config and factory compatibility checks, then commit**

```powershell
& $transportPython -m pytest tests/test_config.py tests/test_provider_factory.py tests/test_openai_provider.py tests/test_evaluation/test_config.py -q -p no:cacheprovider
& $transportPython -m ruff check src/deep_research/utils/config.py tests/test_config.py
git diff --check
git add src/deep_research/utils/config.py config.yaml tests/test_config.py
git commit -m "feat(config): add compatible opt-in structured transport"
```

Expected: all pass. Do not paper over regressions by pinning every OpenAI fixture to `json_schema`; preserving omitted/default configuration is the point of `auto`.

---

### Task 2: Implement and test one tool attempt independently

**Files:** `src/deep_research/providers/deepseek_provider.py`, `tests/test_deepseek_provider.py`.

**Interfaces:** `_tool_schema(schema) -> dict[str, object]`, `_tool_choice(schema_name) -> object`, `_tool_instruction(schema) -> ChatMessage`, `_tool_call_arguments(response, *, expected_name, attempt) -> str`, and `_DeepSeekSchemaStructuredProvider._tool_call_structured_attempt` with the same keyword interface as `_responses_structured_attempt`.

No public dispatch changes in this task. Direct-attempt tests make this task independently green; public repair tests belong to Task 3.

- [ ] **Step 1: Add response builders and direct-attempt tests, then observe RED**

Append these to `tests/test_deepseek_provider.py`, using its existing `SimpleNamespace`, `TinyAnswer`, `RecordingCompletions`, `FakeDeepSeekClient`, `local_tracker`, and `deepseek_config`. Synthetic response data is permitted.

```python
def tool_call_response(*, arguments, name="TinyAnswer", finish_reason="tool_calls"):
    return SimpleNamespace(
        model="deepseek-v4-flash",
        choices=[
            SimpleNamespace(
                finish_reason=finish_reason,
                message=SimpleNamespace(
                    content=None,
                    tool_calls=[
                        SimpleNamespace(
                            id="call-1",
                            type="function",
                            function=SimpleNamespace(name=name, arguments=arguments),
                        )
                    ],
                ),
            )
        ],
        usage=SimpleNamespace(prompt_tokens=4, completion_tokens=2, total_tokens=6),
    )


async def direct_tool_attempt(completions, *, config=None, max_tokens=32768):
    tracker = local_tracker()
    provider = deepseek_module.DeepSeekSchemaChatProvider(
        config or deepseek_config(structured_transport="tool_call"),
        tracker,
        client=FakeDeepSeekClient(completions),
    )
    effective, request, metadata = provider._request_options("critic")
    async with tracker.session_span("session-1", "synthetic question"):
        return await provider._tool_call_structured_attempt(
            [{"role": "user", "content": "decide"}],
            TinyAnswer,
            model=effective.model,
            request=request,
            metadata=metadata,
            configured_max_tokens=max_tokens,
            attempt=1,
        )


@pytest.mark.asyncio
async def test_tool_attempt_sends_exact_schema_choice_and_budget():
    completions = RecordingCompletions(
        tool_call_response(
            arguments='{"answer":"yes","confidence":9}',
        )
    )
    result = await direct_tool_attempt(completions, max_tokens=12345)
    assert result == TinyAnswer(answer="yes", confidence=9)
    assert len(completions.calls) == 1
    call = completions.calls[0]
    assert call["max_tokens"] == 12345
    # Task 0 chooses one of these two values; pin the recorded choice here.
    assert call["tool_choice"] == {
        "type": "function",
        "function": {"name": "TinyAnswer"},
    }
    assert call["tools"][0]["function"]["parameters"] == TinyAnswer.model_json_schema()
    assert call["extra_body"] == {"thinking": {"type": "enabled"}}
    assert call["reasoning_effort"] == "high"
    assert not {"response_format", "text", "parallel_tool_calls"} & call.keys()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "prose",
        "zero",
        "multiple",
        "wrong_name",
        "wrong_type",
        "no_function",
        "empty_arguments",
        "non_string",
        "zero_choices",
        "two_choices",
    ],
)
async def test_tool_attempt_rejects_invalid_envelopes_with_bounded_diagnostic(case):
    response = tool_call_response(arguments='{"answer":"yes","confidence":9}')
    message = response.choices[0].message
    if case == "prose":
        message.content = "SYNTHETIC_PROSE_MARKER"
        message.tool_calls = None
    elif case == "zero":
        message.tool_calls = []
    elif case == "multiple":
        message.tool_calls *= 2
    elif case == "wrong_name":
        message.tool_calls[0].function.name = "SYNTHETIC_WRONG_NAME"
    elif case == "wrong_type":
        message.tool_calls[0].type = "custom"
    elif case == "no_function":
        message.tool_calls[0].function = None
    elif case == "empty_arguments":
        message.tool_calls[0].function.arguments = "  "
    elif case == "non_string":
        message.tool_calls[0].function.arguments = {
            "answer": "SYNTHETIC_ARGUMENT_MARKER"
        }
    elif case == "zero_choices":
        response.choices = []
    else:
        response.choices *= 2
    with pytest.raises(deepseek_module._StructuredValidationFailure) as caught:
        await direct_tool_attempt(RecordingCompletions(response))
    assert caught.value.diagnostic.category == "schema_output"
    assert caught.value.diagnostic.field_paths == ("$",)
    assert caught.value.diagnostic.attempt == 1
    assert "SYNTHETIC_" not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("arguments", "category"),
    [
        ("SYNTHETIC_NON_JSON", "json_invalid"),
        ('{"answer":"yes"}', "missing"),
    ],
)
async def test_tool_attempt_validates_arguments_locally(arguments, category):
    completions = RecordingCompletions(tool_call_response(arguments=arguments))
    with pytest.raises(deepseek_module._StructuredValidationFailure) as caught:
        await direct_tool_attempt(completions)
    assert caught.value.diagnostic.category == category
    assert len(completions.calls) == 1


@pytest.mark.asyncio
async def test_tool_attempt_output_limit_is_not_a_validation_repair():
    completions = RecordingCompletions(
        tool_call_response(
            arguments="{}",
            finish_reason="length",
        )
    )
    with pytest.raises(ProviderOutputLimitError) as caught:
        await direct_tool_attempt(completions)
    assert caught.value.telemetry.finish_reason_category == "length"
    assert len(completions.calls) == 1
```

Run `& $transportPython -m pytest tests/test_deepseek_provider.py -q -k tool_attempt -p no:cacheprovider`. Expected: missing-method failures before implementation. If Task 0 chose `required`, the success test must assert the literal `'required'`, not reuse the production helper to compute its expected value.

- [ ] **Step 2: Add the tool helpers before `_DeepSeekSchemaStructuredProvider`**

```python
# Fixed by the recorded Task 0 gate; never guess or downgrade at runtime.
_STRUCTURED_TOOL_CHOICE = "function"


def _tool_schema(schema: type[BaseModel]) -> dict[str, object]:
    # Preserve descriptions, constraints, $defs, references, and defaults.
    return schema.model_json_schema()


def _tool_choice(schema_name: str) -> object:
    if _STRUCTURED_TOOL_CHOICE == "required":
        return "required"
    return {"type": "function", "function": {"name": schema_name}}


def _tool_instruction(schema: type[BaseModel]) -> ChatMessage:
    return ChatMessage(
        role="system",
        content=(
            f"Submit the structured result by calling {schema.__name__} exactly once. "
            "Its arguments must contain the requested result and conform to the "
            "provided function schema. Do not answer with ordinary message text. "
            "This function submits the result; do not call any other function."
        ),
    )


def _tool_call_arguments(
    response: Any,
    *,
    expected_name: str,
    attempt: int,
) -> str:
    choices = getattr(response, "choices", None)
    message = (
        getattr(choices[0], "message", None)
        if isinstance(choices, (list, tuple)) and len(choices) == 1
        else None
    )
    calls = getattr(message, "tool_calls", None)
    call = calls[0] if isinstance(calls, (list, tuple)) and len(calls) == 1 else None
    function = getattr(call, "function", None)
    arguments = getattr(function, "arguments", None)
    if (
        getattr(call, "type", None) == "function"
        and getattr(function, "name", None) == expected_name
        and isinstance(arguments, str)
        and arguments.strip()
    ):
        return arguments
    # The returned name/content never enters a diagnostic or repair prompt.
    raise _StructuredValidationFailure(
        expected_name,
        StructuredValidationDiagnostic(
            attempt=attempt,
            field_paths=("$",),
            category="schema_output",
        ),
    ) from None
```

- [ ] **Step 3: Add the attempt method to `_DeepSeekSchemaStructuredProvider`**

The method offers a single submission function and does not execute it. Do not add `strict=True`: that is a separate endpoint/schema compatibility decision. Keep the existing retry, telemetry, output-limit, and local-validation contracts.

```python
    async def _tool_call_structured_attempt(
        self,
        messages: list[dict[str, str]],
        schema: type[SchemaT],
        *,
        model: str,
        request: dict[str, object],
        metadata: dict[str, JsonValue],
        configured_max_tokens: int,
        attempt: int,
    ) -> SchemaT:
        """One tool-call attempt, with local argument validation."""
        async with self._tracker.llm_span(
            model,
            {
                **metadata,
                "operation": "structured_output",
                "attempt": attempt,
                "message_count": len(messages),
                "structured_transport": "tool_call",
            },
        ) as span:
            _sdk = _openai_errors()
            request_attempt = 0

            async def _request() -> Any:
                nonlocal request_attempt
                request_attempt += 1
                try:
                    return await self._client.chat.completions.create(
                        **{
                            **request,
                            "messages": messages,
                            "max_tokens": configured_max_tokens,
                            "tools": [
                                {
                                    "type": "function",
                                    "function": {
                                        "name": schema.__name__,
                                        "description": (
                                            "Return the structured result as "
                                            "this function's arguments."
                                        ),
                                        "parameters": _tool_schema(schema),
                                    },
                                }
                            ],
                            "tool_choice": _tool_choice(schema.__name__),
                        }
                    )
                except (
                    _sdk.APITimeoutError,
                    _sdk.RateLimitError,
                    _sdk.APIConnectionError,
                    _sdk.APIStatusError,
                ) as error:
                    _raise_deepseek_error(error)
                except _sdk.OpenAIError as error:
                    raise ProviderResponseError(
                        "DeepSeek tool-call request failed"
                    ) from error

            response = await with_retries(
                _request,
                retry_count=self._config.retry_count,
                initial_delay=self._config.retry_initial_delay,
                max_delay=self._config.retry_max_delay,
            )
            telemetry = _response_telemetry(
                response,
                configured_max_tokens=configured_max_tokens,
                request_attempt=request_attempt,
                structured_attempt=attempt,
            )
            _set_span_result(span, telemetry)
            if telemetry.finish_reason_category == "length":
                raise ProviderOutputLimitError(telemetry)
            arguments = _tool_call_arguments(
                response, expected_name=schema.__name__, attempt=attempt
            )
            try:
                parsed = schema.model_validate_json(arguments)
            except (json.JSONDecodeError, ValidationError) as error:
                diagnostic = _validation_diagnostic(error, attempt=attempt, schema=schema)
            else:
                self._last_model_returned = getattr(response, "model", None) or model
                return parsed
            response = None
            arguments = ""
            parsed = None
            raise _StructuredValidationFailure(schema.__name__, diagnostic) from None
```

- [ ] **Step 4: Verify the whole provider test file and commit**

```powershell
& $transportPython -m pytest tests/test_deepseek_provider.py -q -p no:cacheprovider
& $transportPython -m ruff check src/deep_research/providers/deepseek_provider.py tests/test_deepseek_provider.py
git diff --check
git add src/deep_research/providers/deepseek_provider.py tests/test_deepseek_provider.py
git commit -m "feat(provider): validate one structured tool-call attempt"
```

Expected: direct attempt tests and all existing tests pass. Public target and judge methods still use Responses at this task boundary.

---

### Task 3: Share the existing loop in place and isolate the judge

**Files:** `src/deep_research/providers/deepseek_provider.py`, `tests/test_deepseek_provider.py`.

**Interfaces:** `_structured_transport()` selects a concrete transport; the shared base returns `json_schema`, only `DeepSeekSchemaChatProvider` reads the setting. `_structured_repair_message(schema, diagnostic, *, transport) -> str` carries no raw failed output.

**Extraction correction:** At `a24c62e`, both cleanup blocks clear `messages`, `agent_name`, and `instruction`. Those locals cannot distinguish the loops. Identify the exact class and the call `return await self._responses_structured_attempt(`. Leave `DeepSeekChatProvider.complete_structured` and its `_structured_attempt` loop unchanged. No `_structured_repair_loop` wrapper is introduced: retaining one existing frame avoids an extra unsanitized traceback owner. A bound method retains `self`, so clearing `self` alone is insufficient; clear `attempt_call` too.

- [ ] **Step 1: Add public integration tests and observe RED**

```python
@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["tool_call", "json_schema"])
@pytest.mark.parametrize("repaired", [False, True])
async def test_target_transport_has_one_bounded_repair(transport, repaired):
    good = '{"answer":"yes","confidence":9}'
    bad = "SYNTHETIC_INVALID_OUTPUT"
    if transport == "tool_call":
        recorder = RecordingCompletions(
            tool_call_response(arguments=bad),
            tool_call_response(arguments=good if repaired else bad),
        )
        client = FakeDeepSeekClient(recorder)
        message_key = "messages"
    else:
        recorder = RecordingResponses(
            responses_response(output_text=bad),
            responses_response(output_text=good if repaired else bad),
        )
        client = FakeDeepSeekClient(responses=recorder)
        message_key = "input"
    tracker = local_tracker()
    provider = deepseek_module.DeepSeekSchemaChatProvider(
        deepseek_config(structured_transport=transport),
        tracker,
        client=client,
    )
    original_messages = [ChatMessage(role="user", content="SYNTHETIC_PROMPT")]
    async with tracker.session_span("session-1", "synthetic question"):
        if repaired:
            result = await provider.complete_structured(original_messages, TinyAnswer)
            assert result.confidence == 9
        else:
            with pytest.raises(StructuredOutputError) as caught:
                await provider.complete_structured(original_messages, TinyAnswer)
            assert [d.attempt for d in caught.value.diagnostics] == [1, 2]
            assert caught.value.__cause__ is None
            assert caught.value.__context__ is None
            surface = repr(_provider_exception_surfaces(caught.value))
            assert bad not in surface
            assert "SYNTHETIC_PROMPT" not in surface
            tb = caught.value.__traceback__
            while tb:
                if tb.tb_frame.f_code.co_name == "complete_structured":
                    assert tb.tb_frame.f_locals.get("attempt_call") is None
                    assert tb.tb_frame.f_locals.get("self") is None
                tb = tb.tb_next
    assert len(recorder.calls) == 2
    assert len(original_messages) == 1
    first = recorder.calls[0][message_key]
    second = recorder.calls[1][message_key]
    assert second[:-1] == first
    assert all(m["role"] not in {"assistant", "tool"} for m in second)
    assert bad not in str(second)
    assert "Validation summary:" in second[-1]["content"]
    if transport == "tool_call":
        assert "calling TinyAnswer exactly once" in second[-1]["content"]
        assert "JSON Schema:" not in str(first)
        assert recorder.calls[0]["tools"] == recorder.calls[1]["tools"]
        assert recorder.calls[0]["tool_choice"] == recorder.calls[1]["tool_choice"]
    else:
        assert "Return only one JSON object" in second[-1]["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize("repaired", [False, True])
async def test_prose_answer_gets_one_tool_aware_repair(repaired):
    prose = chat_response(text="SYNTHETIC_PROSE")
    completions = RecordingCompletions(
        prose,
        tool_call_response(arguments='{"answer":"yes","confidence":9}')
        if repaired
        else prose,
    )
    tracker = local_tracker()
    provider = deepseek_module.DeepSeekSchemaChatProvider(
        deepseek_config(structured_transport="tool_call"),
        tracker,
        client=FakeDeepSeekClient(completions),
    )
    async with tracker.session_span("session-1", "synthetic question"):
        if repaired:
            assert (
                await provider.complete_structured(
                    [ChatMessage(role="user", content="decide")],
                    TinyAnswer,
                )
            ).confidence == 9
        else:
            with pytest.raises(StructuredOutputError) as caught:
                await provider.complete_structured(
                    [ChatMessage(role="user", content="decide")],
                    TinyAnswer,
                )
            assert [d.category for d in caught.value.diagnostics] == [
                "schema_output",
                "schema_output",
            ]
    assert len(completions.calls) == 2
    assert "SYNTHETIC_PROSE" not in str(completions.calls[1]["messages"])
    assert (
        "calling TinyAnswer exactly once"
        in completions.calls[1]["messages"][-1]["content"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["auto", "json_schema", "tool_call"])
async def test_judge_keeps_responses_under_every_target_setting(transport):
    responses = RecordingResponses(
        responses_response(
            output_text=json.dumps(_judge_payload(rationale="Synthetic verdict.")),
        )
    )
    client = FakeDeepSeekClient(responses=responses)
    tracker = local_tracker()
    provider = deepseek_module.DeepSeekJudgeProvider(
        deepseek_config(structured_transport=transport),
        tracker,
        client=client,
    )
    async with tracker.session_span("session-1", "synthetic question"):
        result = await provider.complete_structured(
            [ChatMessage(role="user", content="judge")],
            JudgeVerdict,
            agent_name="judge",
        )
    assert isinstance(result, JudgeVerdict)
    assert len(responses.calls) == 1
    assert responses.calls[0]["text"]["format"]["type"] == "json_schema"
    assert not client.chat.completions.calls
```

Run `& $transportPython -m pytest tests/test_deepseek_provider.py -q -k 'target_transport_has or prose_answer_gets or judge_keeps' -p no:cacheprovider`. The target tool cases fail before dispatch; judge and Responses controls should already pass.

- [ ] **Step 2: Add the transport-specific repair renderer**

Before `_DeepSeekSchemaStructuredProvider`, add:

```python
def _structured_repair_message(
    schema: type[BaseModel],
    diagnostic: StructuredValidationDiagnostic,
    *,
    transport: str,
) -> str:
    if transport == "tool_call":
        instruction = (
            f"The previous structured submission failed {schema.__name__} validation. "
            f"Regenerate the result by calling {schema.__name__} exactly once. "
            "Use the provided function schema for its arguments; do not answer "
            "with ordinary message text. "
        )
        schema_suffix = ""  # Both attempts already carry the identical tool schema.
    else:
        instruction = (
            f"The previous JSON response failed {schema.__name__} validation. "
            "Return only one JSON object that validates against the supplied "
            "JSON Schema. Do not add Markdown or explanatory text. "
        )
        schema_suffix = "JSON Schema:\n" + json.dumps(
            schema.model_json_schema(),
            sort_keys=True,
            separators=(",", ":"),
        )
    return (
        instruction
        + f"Validation summary: {_validation_summary(diagnostic)}\n"
        + _validation_repair_guidance(diagnostic)
        + schema_suffix
    )
```

- [ ] **Step 3: Add hooks and replace only the shared base's public method**

Add to `_DeepSeekSchemaStructuredProvider`:

```python
    def _structured_transport(self) -> str:
        return "json_schema"
```

Add to `DeepSeekSchemaChatProvider` (not `DeepSeekJudgeProvider`):

```python
    def _structured_transport(self) -> str:
        return self._config.effective_structured_transport
```

Replace `_DeepSeekSchemaStructuredProvider.complete_structured` with the following. Keep its `_responses_structured_attempt` unchanged, including its output-limit handling and raw-response cleanup. Update the shared class's obsolete Responses-only docstring to describe the selection hook and single loop; update the target class's docstring to describe both transports.

```python
    async def complete_structured(
        self,
        messages: Sequence[ChatMessage],
        schema: type[SchemaT],
        *,
        agent_name: str | None = None,
        max_tokens: int | None = None,
    ) -> SchemaT:
        if not messages:
            raise ValueError("messages must contain at least one item")
        resolved_max_tokens = _resolve_max_tokens(self._config.max_tokens, max_tokens)
        transport = self._structured_transport()
        if transport == "tool_call":
            effective, request, metadata = self._request_options(agent_name)
            instruction = _tool_instruction(schema)
            attempt_call = self._tool_call_structured_attempt
        else:
            effective, request, metadata = _responses_request_options(
                self._config, agent_name
            )
            instruction = _json_instruction(schema)
            attempt_call = self._responses_structured_attempt
        metadata = {**metadata, "structured_transport": transport}
        current_messages = [
            *_translated_messages(messages),
            {"role": "system", "content": instruction.content},
        ]

        diagnostics: list[StructuredValidationDiagnostic] = []
        final_error: StructuredOutputError | None = None
        for attempt in (1, 2):
            try:
                return await attempt_call(
                    current_messages,
                    schema,
                    model=effective.model,
                    request=request,
                    metadata=metadata,
                    configured_max_tokens=resolved_max_tokens,
                    attempt=attempt,
                )
            except _StructuredValidationFailure as error:
                diagnostics.append(error.diagnostic)
                if attempt == 2:
                    final_error = StructuredOutputError(
                        f"DeepSeek output failed {schema.__name__} validation "
                        "after one repair attempt",
                        diagnostics=tuple(diagnostics),
                    )
                    break
                repair = _structured_repair_message(
                    schema,
                    error.diagnostic,
                    transport=transport,
                )
                current_messages = [
                    *current_messages,
                    {"role": "system", "content": repair},
                ]

        if final_error is None:
            raise AssertionError("structured output attempt loop did not return")

        # Do not raise while handling the internal validation failure: that
        # would retain it through ``__context__``/``__cause__``. Clear all
        # provider-adjacent locals before the public error's traceback is
        # captured, leaving only the bounded typed diagnostics.
        attempt_call = None
        self = None
        messages = []
        current_messages = []
        request = {}
        metadata = {}
        effective = None
        agent_name = None
        schema = BaseModel
        instruction = None
        repair = ""
        raise final_error
```

**Exactly what attempt 2 sends:** the original translated caller messages, the same submission instruction, one additional system message containing the bounded category/field paths and regeneration instruction, the same `tools` and forced choice, and unchanged request settings/budget. It does not contain the failed prose, malformed JSON, assistant tool call, tool result, or reasoning. This is a fresh regeneration request, not a continuation of the failed assistant turn. Therefore it does not create a missing tool-result or reasoning round-trip obligation. Existing caller-supplied assistant history is preserved; arbitrary native tool conversations are outside the project's `ChatMessage` contract.

- [ ] **Step 4: Pin existing transport assertions and verify all error boundaries**

Pin `deepseek_config(structured_transport='json_schema')` in these **five** existing tests: `test_schema_target_structured_uses_responses_json_schema`, `test_schema_target_responses_carries_thinking_effort`, `test_schema_target_validation_failure_repairs_exactly_once`, `test_schema_target_unparseable_output_is_json_invalid_at_root`, and `test_schema_target_output_limit_stays_typed`. Wrap the surrounding constructor arguments over separate lines to respect the 88-column limit. Leave `test_schema_target_plain_completion_stays_on_chat_completions` unchanged. These pins are in this task so the default promotion cannot invalidate them later.

Preserve existing keyword arguments while adding the pin. In particular, `test_schema_target_responses_carries_thinking_effort` currently uses `deepseek_config(reasoning_effort="max")`; change that argument to `deepseek_config(reasoning_effort="max", structured_transport="json_schema")`. Replacing it with a bare transport-only config would invalidate its effort assertion.

Keep the original judge privacy coverage. Append these target-specific checks for private data retention, public output-limit behavior, and HTTP retry ownership:

```python
@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["tool_call", "json_schema"])
async def test_target_exhaustion_drops_private_request_and_schema_data(
    transport, monkeypatch
):
    response_marker = "SYNTHETIC_RESPONSE_7C23"
    prompt_marker = "SYNTHETIC_PROMPT_24AB"
    request_marker = "SYNTHETIC_REQUEST_9A21"
    schema_marker = "SYNTHETIC_SCHEMA_55CE"

    class MarkedTinyAnswer(BaseModel):
        answer: str
        confidence: int
        model_config = ConfigDict(json_schema_extra={"description": schema_marker})

    invalid = json.dumps({"answer": response_marker, "confidence": "invalid-int"})
    tracker = CapturingTracker()
    if transport == "tool_call":
        recorder = RecordingCompletions(
            *[
                tool_call_response(arguments=invalid, name="MarkedTinyAnswer")
                for _ in range(2)
            ]
        )
        client = FakeDeepSeekClient(recorder)
    else:
        recorder = RecordingResponses(
            *[responses_response(output_text=invalid) for _ in range(2)]
        )
        client = FakeDeepSeekClient(responses=recorder)
    provider = deepseek_module.DeepSeekSchemaChatProvider(
        deepseek_config(structured_transport=transport),
        tracker,
        client=client,
    )
    if transport == "tool_call":
        original_options = provider._request_options

        def marked_options(agent_name):
            effective, request, metadata = original_options(agent_name)
            return effective, {**request, "marker": request_marker}, metadata

        monkeypatch.setattr(provider, "_request_options", marked_options)
    else:
        original_options = deepseek_module._responses_request_options

        def marked_options(config, agent_name):
            effective, request, metadata = original_options(config, agent_name)
            return effective, {**request, "marker": request_marker}, metadata

        monkeypatch.setattr(
            deepseek_module, "_responses_request_options", marked_options
        )
    with pytest.raises(StructuredOutputError) as caught:
        async with tracker.session_span("session-1", "synthetic question"):
            await provider.complete_structured(
                [ChatMessage(role="user", content=prompt_marker)],
                MarkedTinyAnswer,
            )
    from deep_research.evaluation.failure_taxonomy import safe_failure_details

    details = safe_failure_details(caught.value)
    assert details is not None
    surface = (
        repr(_provider_exception_surfaces(caught.value)) + details.model_dump_json()
    )
    for marker in (response_marker, prompt_marker, request_marker, schema_marker):
        assert marker not in surface
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert len(recorder.calls) == 2
    recorded_requests = json.dumps(recorder.calls)
    for marker in (prompt_marker, request_marker, schema_marker):
        assert marker in recorded_requests  # Non-vacuous sanitization assertions.
    assert response_marker not in recorded_requests


@pytest.mark.asyncio
async def test_public_tool_output_limit_skips_repair():
    completions = RecordingCompletions(
        tool_call_response(
            arguments="{}",
            finish_reason="length",
        )
    )
    tracker = local_tracker()
    provider = deepseek_module.DeepSeekSchemaChatProvider(
        deepseek_config(structured_transport="tool_call"),
        tracker,
        client=FakeDeepSeekClient(completions),
    )
    async with tracker.session_span("session-1", "synthetic question"):
        with pytest.raises(ProviderOutputLimitError):
            await provider.complete_structured(
                [ChatMessage(role="user", content="decide")],
                TinyAnswer,
            )
    assert len(completions.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 503])
async def test_tool_http_retries_are_separate_from_structured_repairs(status):
    error = APIStatusError(
        "Synthetic HTTP failure",
        response=httpx.Response(
            status, request=httpx.Request("POST", "https://example.com")
        ),
        body={"error": "synthetic"},
    )
    completions = RecordingCompletions(
        error,
        tool_call_response(arguments='{"answer":"yes","confidence":9}'),
    )
    config = deepseek_config(
        structured_transport="tool_call",
        retry_count=1,
        retry_initial_delay=0,
        retry_max_delay=0,
    )
    if status == 400:
        with pytest.raises(ProviderResponseError) as caught:
            await direct_tool_attempt(completions, config=config)
        assert caught.value.http_status_code == 400
        assert len(completions.calls) == 1
    else:
        assert (await direct_tool_attempt(completions, config=config)).confidence == 9
        assert len(completions.calls) == 2
        assert completions.calls[0] == completions.calls[1]
```

```powershell
& $transportPython -m pytest tests/test_deepseek_provider.py tests/test_provider_factory.py tests/test_evaluation/test_judging.py tests/test_evaluation/test_failure_taxonomy.py -q -p no:cacheprovider
& $transportPython -m ruff check src/deep_research/providers/deepseek_provider.py tests/test_deepseek_provider.py
git diff --check
git add src/deep_research/providers/deepseek_provider.py tests/test_deepseek_provider.py
git commit -m "fix(provider): share target repair without changing judge transport"
```

All targeted tests must pass before committing. Malformed tool envelopes use `schema_output`, malformed argument JSON uses `json_invalid`, and output limits/HTTP failures retain their original typed categories.

---

### Task 4: Verify real schemas, factory routing, and all-agent wiring

**Files:** `tests/test_deepseek_provider.py`, `tests/test_runtime/test_assembly.py`.

**Interfaces:** public `build_chat_provider`, the seven actual draft schemas and four ReAct call sites, and `build_agents`. This proves common transport coverage, not live semantic quality for each agent.

- [ ] **Step 1: Test the real schema inventory through the factory**

Append to `tests/test_deepseek_provider.py`. Use a nested value where a schema has `$defs`, retaining exactly the same schema supplied by the agent.

```python
from deep_research.agents.critic import CritiqueDraft
from deep_research.agents.fact_checker import ClaimsDraft, ClaimVerdictDraft
from deep_research.agents.planner import ResearchPlanDraft
from deep_research.agents.researcher import SubTopicFindingsDraft
from deep_research.agents.source_evaluator import SourceScoresDraft
from deep_research.agents.synthesizer import ReportDraft

AGENT_SCHEMA_CASES = [
    (
        "planner",
        ResearchPlanDraft,
        {
            "sub_topics": [
                {
                    "title": "Evidence",
                    "rationale": "Find evidence.",
                    "search_queries": ["evidence"],
                    "success_criteria": ["Find a source."],
                    "priority": 1,
                }
            ]
        },
    ),
    (
        "researcher",
        SubTopicFindingsDraft,
        {
            "findings": [
                {
                    "content": "Synthetic finding.",
                    "source_url": "https://example.com/a",
                    "source_title": "Synthetic source",
                    "confidence": 0.8,
                }
            ]
        },
    ),
    (
        "source_evaluator",
        SourceScoresDraft,
        {
            "sources": [
                {
                    "url": "https://example.com/a",
                    "authority_score": 0.8,
                    "recency_score": 0.8,
                    "relevance_score": 0.8,
                    "rationale": "Synthetic.",
                }
            ]
        },
    ),
    (
        "fact_checker",
        ClaimsDraft,
        {
            "claims": [
                {
                    "text": "Synthetic claim.",
                    "source_urls": ["https://example.com/a"],
                }
            ]
        },
    ),
    (
        "fact_checker",
        ClaimVerdictDraft,
        {
            "verdict": "insufficient_evidence",
            "confidence": 0.3,
            "evidence": [],
            "contradictions": [],
        },
    ),
    (
        "synthesizer",
        ReportDraft,
        {
            "executive_summary": "Synthetic summary.",
            "uncertainty_notes": "Limited.",
            "sections": [
                {
                    "title": "Evidence",
                    "body": "Synthetic evidence.",
                    "source_urls": ["https://example.com/a"],
                }
            ],
        },
    ),
    (
        "critic",
        CritiqueDraft,
        {
            "score": 7,
            "gaps": [],
            "unsupported_claims": [],
            "recommended_queries": [],
            "rationale": "Synthetic.",
        },
    ),
] + [
    (
        agent,
        ReActDecision,
        {
            "thought": "Synthetic decision.",
            "action": "use_tool",
            "tool_name": "web_search",
            "tool_input_json": '{"query":"synthetic"}',
            "final_answer": None,
        },
    )
    for agent in ("planner", "researcher", "fact_checker", "critic")
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("agent", "schema", "payload"), AGENT_SCHEMA_CASES)
async def test_actual_agent_schema_uses_selected_factory_transport(
    agent,
    schema,
    payload,
    monkeypatch,
):
    from deep_research.providers import build_chat_provider

    completions = RecordingCompletions(
        tool_call_response(
            arguments=json.dumps(payload),
            name=schema.__name__,
        )
    )
    client = FakeDeepSeekClient(completions)
    monkeypatch.setattr(deepseek_module, "_build_client", lambda *a, **kw: client)
    tracker = local_tracker()
    provider = build_chat_provider(
        deepseek_config(structured_transport="tool_call"),
        tracker,
    )
    async with tracker.session_span("session-1", "synthetic question"):
        result = await provider.complete_structured(
            [ChatMessage(role="user", content="synthetic task")],
            schema,
            agent_name=agent,
        )
    assert result == schema.model_validate(payload)
    call = completions.calls[0]
    function = call["tools"][0]["function"]
    assert function["name"] == schema.__name__
    assert function["parameters"] == schema.model_json_schema()
    if schema is ReActDecision:
        assert isinstance(result.tool_input_json, str)
        assert json.loads(result.tool_input_json) == {"query": "synthetic"}
    assert not client.responses.calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("thinking", "effort"),
    [
        ("enabled", "high"),
        ("enabled", "max"),
        ("disabled", "none"),
    ],
)
async def test_tool_transport_preserves_effective_agent_request_settings(
    thinking, effort
):
    completions = RecordingCompletions(
        tool_call_response(
            arguments='{"answer":"yes","confidence":9}',
        )
    )
    await direct_tool_attempt(
        completions,
        config=deepseek_config(
            structured_transport="tool_call",
            model_overrides={
                "critic": {
                    "model": "deepseek-v4-pro",
                    "thinking_mode": thinking,
                    "reasoning_effort": effort,
                }
            },
        ),
    )
    call = completions.calls[0]
    assert call["model"] == "deepseek-v4-pro"
    assert call["extra_body"]["thinking"]["type"] == thinking
    if thinking == "enabled":
        assert call["reasoning_effort"] == effort
        assert "temperature" not in call
    else:
        assert "reasoning_effort" not in call
        assert call["temperature"] == deepseek_config().temperature
```

Move the imports into the file's import block and let Ruff order them. These tests assert complete parameter schemas, not a fabricated judge schema given a target label. The `deepseek-v4-pro` case verifies request propagation offline; it does not claim live capability for an unconfigured model.

- [ ] **Step 2: Assert the real assembly passes the selected provider to every agent**

Append to `tests/test_runtime/test_assembly.py`. Its existing `_recording_agent_class` calls the real constructors, so validation of each agent's toolset is retained.

```python
def test_all_six_agents_share_the_selected_structured_provider(tracker, monkeypatch):
    received = []
    for class_name in (
        "PlannerAgent",
        "ResearcherAgent",
        "SourceEvaluatorAgent",
        "FactCheckerAgent",
        "SynthesizerAgent",
        "CriticAgent",
    ):
        monkeypatch.setattr(
            assembly,
            class_name,
            _recording_agent_class(
                getattr(assembly, class_name),
                kwarg="provider",
                captured=received,
            ),
        )
    provider = RecordingProvider()
    settings = ConfigSettings(llm=LLMConfig(structured_transport="tool_call"))
    tools = build_tools(
        settings,
        tracker=tracker,
        memory=build_bridge(),
        search_client=FakeSearchClient(),
    )
    build_agents(
        settings,
        tracker=tracker,
        provider=provider,
        tools=tools,
        session_id="session-1",
        reputation=None,
    )
    assert len(received) == 6
    assert all(item is provider for item in received)
```

Run the real agent tests alongside these boundary checks; their existing fake-driven runs cover domain parsing and fallback behavior. Do not add tests that search source strings for a field name: that does not prove dispatch or behavior.

- [ ] **Step 3: Run the focused gate and one meaningful mutation**

```powershell
& $transportPython -m pytest tests/test_deepseek_provider.py tests/test_runtime/test_assembly.py tests/test_agents -q -p no:cacheprovider
```

Expected: all pass. To check sensitivity, temporarily make **only** `DeepSeekSchemaChatProvider._structured_transport` return `'json_schema'` unconditionally. Run the 11 `actual_agent_schema_uses_selected_factory_transport` cases and the public tool repair cases; they must fail because the supplied Chat Completions results are never consumed. Restore that exact method, then rerun those cases and confirm PASS. Keep a copy of the file and restore it in `finally`; do not use a broad checkout/reset that discards other work. Flipping the field default while tests explicitly set a transport is not a valid mutation and is removed from this plan.

- [ ] **Step 4: Commit the passing coverage**

```powershell
& $transportPython -m ruff check tests/test_deepseek_provider.py tests/test_runtime/test_assembly.py
git diff --check
git add tests/test_deepseek_provider.py tests/test_runtime/test_assembly.py
git commit -m "test(provider): cover real agent schemas and shared provider wiring"
```

---

### Task 5: Add a transport-neutral Critic format reminder

**Files:** `src/deep_research/agents/prompts.py`, `tests/test_agents/test_critic.py`.

**Interfaces:** the existing `CRITIQUE_INSTRUCTION`; no change to `CritiqueDraft`, scoring/domain logic, or `critique_messages`' sections. The provider supplies the concrete schema and response channel for every agent.

- [ ] **Step 1: Add the prompt tests and observe RED on the format reminder**

```python
def test_review_format_reminder_uses_the_provider_selected_channel():
    body = critique_messages(
        _task(),
        ReActRun(agent_name="critic", stop_reason="finished"),
        report_chars=6000,
        claim_digest=40,
    )[1].content
    assert "Use the structured response channel specified by the provider" in body
    assert "Do not add Markdown or prose outside the structured result" in body
    assert "The first character of your reply" not in body
    assert '"score": 8' not in body


def test_review_format_reminder_preserves_the_scoring_contract():
    body = critique_messages(
        _task(),
        ReActRun(agent_name="critic", stop_reason="finished"),
        report_chars=6000,
        claim_digest=40,
    )[1].content
    for requirement in (
        "an integer from 1 to 10",
        "list a gap only when closing it would materially change the",
        "an empty list when the report is materially complete",
        "Do not decide whether research continues",
    ):
        assert requirement in body
```

Run `& $transportPython -m pytest tests/test_agents/test_critic.py -q -k format_reminder -p no:cacheprovider`. The new format test fails; the preserved scoring contract should already pass.

- [ ] **Step 2: Append the reminder and verify GREEN**

Append to the end of the existing `CRITIQUE_INSTRUCTION` tuple, preserving every existing sentence:

```python
    '\nUse the structured response channel specified by the provider. '
    'Supply the score, gaps, unsupported_claims, recommended_queries, and '
    'rationale in that result. Do not add Markdown or prose outside the '
    'structured result.'
```

No literal first/last character rule and no example numeric score is added. In tool mode the JSON is inside arguments; in Responses mode it is the response object. The scoring scale and continuation policy remain unchanged. This changes the target prompt fingerprint; Task 7 records it and uses the identical prompt for contemporaneous controls.

```powershell
& $transportPython -m pytest tests/test_agents/test_critic.py -q -p no:cacheprovider
& $transportPython -m ruff check src/deep_research/agents/prompts.py tests/test_agents/test_critic.py
git diff --check
git add src/deep_research/agents/prompts.py tests/test_agents/test_critic.py
git commit -m "fix(critic): align format reminder with structured response channel"
```

---

### Task 6: Run and record the offline regression gate

**Files:** fix log only after tests; source changes require rerunning affected checks.

**Interfaces:** consumes Tasks 0–5; produces the offline gate required by **Task 7**. Do not call this gate passed when an unexplained test fails.

- [ ] **Step 1: Compare the exact selected tests and baseline failures**

Save baseline selected node IDs before implementation using `pytest --collect-only -q` at the reviewed commit, and candidate IDs at this task. Use a unique output directory so old results are not overwritten. The new tests include parametrized cases; count the collected IDs, not the number of `def test_` declarations. The original 1374-line plan described 16 new cases, not 13; this revision intentionally adds more boundary coverage and must use its actual collected count.

```powershell
& $transportPython -m pytest --collect-only -q -p no:cacheprovider
& $transportPython -m pytest -q -p no:cacheprovider
```

Expected: no additional failing test identities or new failure reasons relative to the three documented baseline failures. Fewer failures are acceptable if explained by environment or a verified pre-existing fix; do not force the number to remain three. Confirm total selected tests increased by the actual new cases and none were accidentally deselected. The default `pyproject.toml` marker excludes live tests; do not override it.

- [ ] **Step 2: Check lint, whitespace, and protected settings**

```powershell
& $transportPython -m ruff check src tests
git diff --check
git diff a24c62e -- config.yaml
git diff a24c62e -- src/deep_research/agents src/deep_research/evaluation
```

Only the transport selection/comment and additive Critic reminder may differ in those protected surfaces. Budgets, thinking/effort, judge path, fallback/domain behavior, rubrics, weights, thresholds and frozen cases must be unchanged. Confirm the five Responses-specific target tests are pinned and the judge suite still passes with explicit target `tool_call`.

- [ ] **Step 3: Record the exact gate and commit**

Append the branch SHA, import path, Python/SDK versions, selected test count and delta, pass/fail/deselected counts, exact failing node IDs and reasons, Ruff result, and sensitivity-check result to the numbered fix log. Also record that no live calls have been made since Task 0 and that the automatic selection remains `json_schema`.

```powershell
git diff --check
git add docs/superpowers/2026-09-08-cross-agent-planner-fix-parity-fix-log.md
git commit -m "docs: record exact structured transport offline gate"
```

---

### Task 7: Pre-register the canary and gate the all-agent rollout

**Files:** `docs/superpowers/2026-09-11-tool-call-transport-canary.md`, fix log, and only after successful evidence the automatic selector/comment and its default tests.

**Interfaces:** consumes Task 6 and Task 0's verified settings; produces evidence and an explicit rollout decision. OpenAI remains on `responses.parse` and the judge remains on Responses regardless of target outcome.

- [ ] **Step 1: Obtain authorization for the actual campaign**

Four successes do not distinguish a 25% failure probability from zero: under independent identical trials, `P(0 failures | p=0.25) = 0.75^4 = 0.3164`; for three it is `0.4219`. Eleven zero-failure trials bring that probability below 5% (`0.04224`), but the one-sided 95% upper bound is still about 23.84%. No finite clean canary proves zero failures; 59 clean trials would be needed for that upper bound to fall below 5%. The historical rate is an approximate observation across a configuration family, not a known constant.

Pre-register **11 candidate Critic repetitions for each distinct production/evaluation effort** and one contemporaneous `json_schema` control for each. For the current config that is 22 tool candidates plus 2 controls (`high` and `max`). Then, conditional on the Critic gate, run each other agent's live case once for each distinct production/evaluation setting: Planner high/max, Researcher high, Source Evaluator high, Fact Checker high/max, Synthesizer high/max = **8** further invocations. Total ceiling: **32 evaluation invocations**, each with its existing single live repetition and bounded internal requests. This uses DeepSeek target and judge, LangSmith, and the existing live tools; it is not 32 single model calls.

Show that count and scope and obtain explicit authorization before starting. If the user authorizes only a four-run pilot, run only that pilot, label it preliminary, and keep `auto` on `json_schema`. If effective models or overrides differ from this inventory, recompute the scope before authorization. Do not automatically run a suite. A stopping rule limits spending; it does not justify dropping failed trials or restarting the success count.

- [ ] **Step 2: Record the candidate and execute sequentially without placeholders**

Check tracked diff and staged diff are empty. Inventory and preserve untracked files; clean tracked state does not imply a completely empty `git status`, and the harness may truthfully report `git_dirty=true` for preserved untracked files. Record the complete candidate SHA and configuration/prompt fingerprints. Use a unique output root per campaign and per invocation, and restore the environment override in `finally`.

Create a fresh output directory and save this result guard as `$transportResultGuard`. It validates typed artifacts and stops the loop without printing raw content. It does not make any provider call.

```powershell
$transportGuardDirectory = Join-Path 'output/transport-probes' ([guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $transportGuardDirectory | Out-Null
$transportResultGuard = Join-Path $transportGuardDirectory 'assert_canary.py'
```

```python
from __future__ import annotations

import sys
from pathlib import Path

from deep_research.evaluation.models import ExperimentResult


def assert_canary(root, commit, transport, agent, effort):
    paths = list(Path(root).rglob("results.json"))
    if len(paths) != 1:
        raise SystemExit("Expected exactly one result artifact for this invocation")
    try:
        result = ExperimentResult.model_validate_json(
            paths[0].read_text(encoding="utf-8")
        )
    except Exception:
        raise SystemExit("Result artifact could not be validated") from None
    model_config = result.metadata.get("target_model_configuration", {})
    if not isinstance(model_config, dict):
        raise SystemExit("Missing effective target configuration")
    if (
        result.metadata.get("git_commit") != commit
        or model_config.get("provider") != "deepseek"
        or model_config.get("structured_transport") != transport
        or model_config.get("reasoning_effort") != effort
        or model_config.get("thinking_mode") != "enabled"
        or result.metadata.get("judge_structured_transport")
        != "deepseek_responses_json_schema_v1"
        or result.agent_name != agent.replace("-", "_")
        or result.tier != "live"
        or result.status in {"FAILED", "INFRASTRUCTURE FAILURE"}
        or result.errors
        or len(result.cases) != 1
    ):
        raise SystemExit("Candidate provenance or experiment gate failed")
    case = result.cases[0]
    if not case.passed or len(case.repetitions) != 1:
        raise SystemExit("Case gate or single-repetition contract failed")
    repetition = case.repetitions[0]
    gates = {gate.gate_id: gate.passed for gate in repetition.gates.results}
    judge = repetition.judge
    if (
        not repetition.completed
        or not gates
        or not all(gates.values())
        or repetition.errors
        or repetition.prohibited_call_count
        or repetition.fallback_provider_diagnostic is not None
        or repetition.react_stop_reason == "provider_error"
        or repetition.aggregate_quality is None
        or repetition.aggregate_quality < 0.75
        or judge is None
        or judge.status != "scored"
        or judge.diagnostics
        or (result.agent_name == "critic" and not gates.get("review_produced"))
    ):
        raise SystemExit("Typed target, quality, or judge gate failed")
    print("Typed canary gate passed for one invocation")


if __name__ == "__main__":
    assert_canary(*sys.argv[1:])
```

```powershell
$transportCandidate = (git rev-parse HEAD).Trim()
$transportShortSha = (git rev-parse --short HEAD).Trim()
$transportCampaign = 'toolcall-' + $transportShortSha + '-' + [guid]::NewGuid().ToString('N')
$transportOutput = Join-Path 'output/evaluations' $transportCampaign
$transportPreviousSelection = [Environment]::GetEnvironmentVariable('LLM_STRUCTURED_TRANSPORT', 'Process')
$transportLauncher = '.superpowers\sdd\2026-09-08-cross-agent-planner-fix-parity\run_with_repo_env.py'
$transportEnvFile = 'C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.env'
if (-not (Test-Path -LiteralPath $transportResultGuard)) { throw 'Missing result guard' }
git diff --quiet
if ($LASTEXITCODE -ne 0) { throw 'Tracked worktree changes are present' }
git diff --cached --quiet
if ($LASTEXITCODE -ne 0) { throw 'Staged changes are present' }
try {
  foreach ($transportEffort in @('high', 'max')) {
    foreach ($transportSelection in @('json_schema', 'tool_call')) {
      $transportCount = if ($transportSelection -eq 'tool_call') { 11 } else { 1 }
      $env:LLM_STRUCTURED_TRANSPORT = $transportSelection
      foreach ($transportRun in 1..$transportCount) {
        if ((git rev-parse HEAD).Trim() -ne $transportCandidate) { throw 'Candidate changed' }
        $transportLabel = "$transportSelection-$transportEffort-r$transportRun"
        $transportRunOutput = Join-Path $transportOutput $transportLabel
        & $transportPython $transportLauncher $transportEnvFile $transportPython -m deep_research.evaluation agent critic --case critic-live-review --tier live --reasoning-effort $transportEffort --config config.yaml --output-directory $transportRunOutput --experiment-prefix "$transportCampaign-$transportLabel"
        if ($LASTEXITCODE -ne 0) { throw "Stopped at $transportLabel; preserve this result" }
        & $transportPython $transportResultGuard $transportRunOutput $transportCandidate $transportSelection critic $transportEffort
        if ($LASTEXITCODE -ne 0) { throw "Typed gate stopped at $transportLabel" }
      }
    }
  }
} finally {
  [Environment]::SetEnvironmentVariable('LLM_STRUCTURED_TRANSPORT', $transportPreviousSelection, 'Process')
}
```

The guard checks **each** artifact before the next invocation. Exit code zero alone is insufficient: it stops on target transport/schema fallback, missing review, judge failure, hard-gate failure or a score below the frozen threshold. A failed control must be recorded and classified; a control failure caused by the historical target defect is comparison evidence, while judge/infrastructure failure makes the experiment inconclusive. Do not rerun a failed invocation under the same label. If a baseline target failure is reviewed and classified, continue only the remaining pre-authorized candidate invocations with fresh labels and include the failed control in the report. Record failed ordinal positions before resuming so the 32-invocation authorization ceiling is never reset.

- [ ] **Step 3: Inspect the Critic evidence, then run the eight other-agent smoke invocations**

Require all 22 candidate Critic invocations to produce a review, no target provider fallback in **any** structured operation (including ReAct), all hard gates passing, scored judges without diagnostics, and aggregate quality at least `0.75`. Count structured attempt-1 failures repaired on attempt 2 separately from exhausted calls. Record telemetry from both transports, configured and served models, effective effort, prompt and configuration fingerprints, artifact paths and hashes. Any excluded infrastructure result stays in the denominator/report as inconclusive, never silently disappears. The two controls are descriptive; they are not a powered A/B test of quality or a proof that transport alone caused improvement.

Only after that Critic gate, use the remaining authorized scope:

```powershell
$transportSmokeRuns = @(
  @('planner', 'high'), @('planner', 'max'),
  @('researcher', 'high'), @('source-evaluator', 'high'),
  @('fact-checker', 'high'), @('fact-checker', 'max'),
  @('synthesizer', 'high'), @('synthesizer', 'max')
)
$transportPreviousSelection = [Environment]::GetEnvironmentVariable('LLM_STRUCTURED_TRANSPORT', 'Process')
try {
  $env:LLM_STRUCTURED_TRANSPORT = 'tool_call'
  foreach ($transportSmoke in $transportSmokeRuns) {
    if ((git rev-parse HEAD).Trim() -ne $transportCandidate) { throw 'Candidate changed' }
    $transportAgent = $transportSmoke[0]
    $transportEffort = $transportSmoke[1]
    $transportLabel = "$transportAgent-$transportEffort"
    $transportRunOutput = Join-Path $transportOutput $transportLabel
    & $transportPython $transportLauncher $transportEnvFile $transportPython -m deep_research.evaluation agent $transportAgent --tier live --reasoning-effort $transportEffort --config config.yaml --output-directory $transportRunOutput --experiment-prefix "$transportCampaign-$transportLabel"
    if ($LASTEXITCODE -ne 0) { throw "Stopped at $transportLabel; preserve this result" }
    & $transportPython $transportResultGuard $transportRunOutput $transportCandidate tool_call $transportAgent $transportEffort
    if ($LASTEXITCODE -ne 0) { throw "Typed gate stopped at $transportLabel" }
  }
} finally {
  [Environment]::SetEnvironmentVariable('LLM_STRUCTURED_TRANSPORT', $transportPreviousSelection, 'Process')
}
Get-ChildItem -LiteralPath $transportOutput -Recurse -File -Filter results.json |
  Get-FileHash -Algorithm SHA256
```

Inspect typed outcomes between these invocations too. All must have scored judges, passing quality/hard gates, and no target provider fallback. Eight smoke runs establish basic cross-agent compatibility, not each agent's statistical reliability. A global default cannot be promoted from Critic-only evidence.

- [ ] **Step 4: Write the evidence and the provider-specific decision**

The canary note contains Scope, Pre-registered stopping rule/sample size, Candidate and configuration, Evidence with SHA-256 values, one typed result row per invocation (including failures), attempt-level diagnostics, Quality, Statistical limits, and Rollout decision. Use direct LangSmith UI links when available without exposing credentials. Compare the historical family and current controls explicitly; Task 5 changed the target prompt, so older scores do not isolate transport. Keep the judge fingerprint and transport unchanged.

OpenAI decision: **unchanged and not validated by this campaign**. A DeepSeek success cannot justify migrating `OpenAIChatProvider`. Any OpenAI change needs OpenAI-specific failure evidence, a separate adapter review and separately authorized tests. Do not infer an OpenAI defect from the rejected configuration value.

- [ ] **Step 5: Promote automatic selection only if the entire gate passes**

If any required target setting or agent remains unverified, preserve `auto -> json_schema`, record the reason, and finish with the experiment's actual outcome. If all gates pass during authorized implementation of this plan, make the conditional default promotion below; no additional permission prompt is needed for that scoped code change. Change the `auto` branch of `LLMConfig.effective_structured_transport` to:

```python
        if self.structured_transport == "auto":
            return "tool_call" if self.provider == "deepseek" else "json_schema"
```

Update `test_auto_transport_preserves_the_existing_default` to assert `tool_call` for DeepSeek and `json_schema` for OpenAI, and rename it `test_auto_transport_selects_the_validated_provider_default`. Update the `'auto'` expected case in `test_deepseek_transport_resolves_explicit_selection` to `'tool_call'`. Replace the first YAML transport comment with `# auto selects the validated DeepSeek tool transport or OpenAI Responses.` Explicit `json_schema` remains the rollback override; the judge hook is unchanged. Record which effective models/modes were validated; new model/effort combinations need their own capability check and canary before rollout claims.

Add this default-routing regression to `tests/test_deepseek_provider.py`:

```python
@pytest.mark.asyncio
async def test_promoted_default_routes_target_to_tools():
    completions = RecordingCompletions(
        tool_call_response(
            arguments='{"answer":"yes","confidence":9}',
        )
    )
    client = FakeDeepSeekClient(completions)
    tracker = local_tracker()
    provider = deepseek_module.DeepSeekSchemaChatProvider(
        deepseek_config(),
        tracker,
        client=client,
    )
    async with tracker.session_span("session-1", "synthetic question"):
        await provider.complete_structured(
            [ChatMessage(role="user", content="decide")],
            TinyAnswer,
        )
    assert len(completions.calls) == 1
    assert not client.responses.calls
```

Rerun the Task 6 gate after this default change and verify the effective DeepSeek request equals the explicit candidate request. Do not claim production deployment from a branch commit.

- [ ] **Step 6: Commit the actual outcome**

```powershell
git diff --check
git add docs/superpowers/2026-09-11-tool-call-transport-canary.md docs/superpowers/2026-09-08-cross-agent-planner-fix-parity-fix-log.md
git commit -m "docs: record structured transport experiment and rollout decision"
```

For an approved promotion, first stage and commit exactly `src/deep_research/utils/config.py`, `config.yaml`, `tests/test_config.py`, and `tests/test_deepseek_provider.py` after their checks, then record both the evaluated candidate SHA and promotion SHA in the evidence commit. If the gate fails, there is no promotion commit. The plan's success is evidence and a safe decision; agent reliability remains unproven if the hypothesis fails.

---

## Execution self-review

- Task 0 tests the actual thinking modes and both production/evaluation effort settings, separates controls from candidates, and has a genuine stop before production changes.
- Tasks 1–3 preserve OpenAI defaults and judge isolation, validate the synthetic function envelope, retain one repair and existing HTTP retries, and protect the final exception graph without adding a caller frame.
- Task 4 uses real agent schemas and production assembly; source-string absence and misleading default flips are removed.
- Task 5 leaves domain scoring and the output schema intact. Task 6 records exact collected tests and failure identities, with no invented pass count.
- Task 7 uses computed SHAs, an explicit paid scope, proper denominators and uncertainty, and requires cross-agent compatibility before changing the DeepSeek automatic selection. No paid calls, application changes, or rollout are implied by committing this plan.

## Verification performed during this plan review

- The original worktree's configuration, DeepSeek/OpenAI provider, and provider-factory tests passed: **278 passed**, one dependency deprecation warning.
- The code fences were applied in a disposable copy outside the repository to check the proposed interfaces and examples. The focused provider/configuration/evaluation/assembly suite plus all agent tests passed **872** tests before promotion, and **873** with the proposed promotion and its additional default-routing regression. These are offline rehearsal results, not changes applied to the application branch or a replacement for Task 6's full gate.
- Collection compared **2089** baseline selected node IDs with **2143** candidate IDs: **54 added, none removed**. Promotion adds one more test. The live marker remained excluded. The historical `3 failed, 2086 passed` result remains historical; a full execution gate must reproduce and compare the actual failure reasons.
- The deliberate wrong-dispatch mutation failed all **11** real-schema cases; restoring dispatch passed all **20** selected schema/repair/judge cases. The canary artifact guard accepted a synthetic valid result and rejected seven bad or incomplete outcomes, including fallback, missing judge, failed quality, and wrong transport.
- The probe dry-run enumerated **15** requests with explicit enabled/high, enabled/max, and disabled settings at the configured 32768 cap. No paid request was made. Python fences were parsed, the rehearsal's touched Python files passed Ruff, and the plan passed whitespace validation.
