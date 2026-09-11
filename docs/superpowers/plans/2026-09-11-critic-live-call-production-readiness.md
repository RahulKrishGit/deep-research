# Critic Live-Call Production Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Critic production ready: its spot-check loop must see the report it critiques, a typed provider fallback must be diagnosable from the artifact, and a degraded run must be legible to the judge instead of silently scoring as a bad review.

**Architecture:** Three narrowly scoped changes on one branch. (1) `CritiqueTask.guidance` gains the report, reusing the existing `_clamp_report` helper, so the Critic's ReAct prompt names its artifact the way the Researcher and Fact Checker already do. (2) `FallbackProviderDiagnostic` stops discarding the structured-validation diagnostics that are *already present* in `ResearchError.details["provider_failure"]`, so a live schema failure is attributable without repeating a paid call. (3) The judge's closed `JudgeInput` contract gains a `provider_fallback` block so the rubric is applied to a fallback's honest disclosure rather than to content that was never produced.

**Tech Stack:** Python 3.12, Pydantic v2 (`ContractModel`), pytest + pytest-asyncio, ruff, LangSmith evaluation harness, DeepSeek provider.

**Spec:** `docs/superpowers/specs/2026-09-11-critic-live-call-production-readiness-design.md`

## Global Constraints

- Work in the worktree `C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.worktrees\cross-agent-planner-fix-parity` on branch `codex/cross-agent-planner-fix-parity`. The checkout has no `.venv` of its own; use the root interpreter `C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.venv\Scripts\python.exe`.
- Run every command from the worktree root directory.
- `llm.max_tokens` stays `4096`. Do not change `retry_count` (5), `retry_initial_delay` (1.0), `retry_max_delay` (16.0), `temperature` (0.7), `thinking_mode`, or `reasoning_effort`.
- Do not change the one-repair structured-output contract in `DeepSeekChatProvider.complete_structured`.
- Do not change `ACCEPTANCE_SCORE` (7), the `route_decision` precedence, `live_threshold` (0.75), or any frozen case, rubric, reference theme, or metric weight.
- Do not change the judge's structured transport (`deepseek_responses_json_schema_v1`) or the target/judge adapter separation in `providers/factory.py`.
- Every new Pydantic field on an artifact model is optional, so an existing payload still round-trips. No existing field is removed, renamed, or made newly required.
- `judge_configuration_fingerprint` must remain `924caf47aa0d` for the frozen config. If it moves, this plan touched a judge setting it promised not to — stop and investigate.
- Do not record any credential, dotenv value, or provider response text in any artifact, test, or document.
- Every task ends with `ruff check` on the files it touched plus `git diff --check`.
- Tests must never touch the network or make a provider call. `ScriptedCompleter` from `tests/agent_fakes.py` is the only provider double used here.

---

## File Structure

| File | Responsibility | Change |
| --- | --- | --- |
| `src/deep_research/agents/prompts.py` | Pure ReAct prompt rendering + shared prompt text | Modify `CRITIC_SYSTEM_PROMPT` to state the report is provided |
| `src/deep_research/agents/critic.py` | Critic task, guidance rendering, review, fallback | `_render_spot_check_guidance` gains the report; `build_task` passes it |
| `src/deep_research/evaluation/models.py` | Strict evaluation artifact contracts | `FallbackProviderDiagnostic` gains bounded diagnostics |
| `src/deep_research/evaluation/runner.py` | Repetition result projection | `_fallback_provider_diagnostic` stops discarding diagnostics |
| `src/deep_research/evaluation/judging.py` | The versioned LLM judge | `JudgeInput` gains `provider_fallback`; block order, template, system prompt |
| `tests/test_agents/test_critic.py` | Critic prompt/behaviour tests | +4 tests, 1 helper signature |
| `tests/test_evaluation/test_runner.py` | Artifact projection tests | 1 test extended, +1 test |
| `tests/test_evaluation/test_judging.py` | Judge contract tests | +3 tests, 1 fingerprint pin |
| `docs/superpowers/2026-09-11-critic-live-call-production-readiness-canary.md` | Canary evidence record | Create in the final task |

Tasks 1–3 fix the Critic. Tasks 4–5 fix the shared evidence path. Task 6 fixes judge legibility. Task 7 is the gated paid canary.

---

### Task 1: Render the report inside the Critic's spot-check guidance

**Files:**
- Modify: `src/deep_research/agents/critic.py:124-134` (`_render_spot_check_guidance`), `critic.py:470` (`build_task`)
- Test: `tests/test_agents/test_critic.py`

**Interfaces:**
- Consumes: `_clamp_report(text: str, *, limit: int) -> str` (`critic.py:277-288`), `CRITIC_REPORT_CHARS: int = 6000` (`critic.py:63`), `_LIVE_REPORT` via `deep_research.evaluation.cases.critic.LIVE_CASES`.
- Produces: `_render_spot_check_guidance(report: str, sub_topics: Sequence[SubTopic], *, report_chars: int) -> str`. Later tasks rely on this exact signature.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_agents/test_critic.py`. Extend the existing `from deep_research.agents.critic import (...)` block with `_render_spot_check_guidance`, and add `from deep_research.evaluation.cases.critic import LIVE_CASES` next to the other `deep_research` imports.

```python
# A sentence that exists only inside the registered live case's report body, so
# these tests measure the report's presence rather than a nearby label.
_LIVE_REPORT_PROBE = "Low-carbon cement technologies have moved from pilot"


def _live_report() -> str:
    """The registered live critic case's fixed report."""
    report = next(
        case for case in LIVE_CASES if case.case_id == "critic-live-review"
    ).fresh_state().report
    assert report is not None
    assert _LIVE_REPORT_PROBE in report
    return report


def test_spot_check_guidance_carries_the_report_under_review() -> None:
    guidance = _render_spot_check_guidance(
        _live_report(), [], report_chars=6000
    )

    assert _LIVE_REPORT_PROBE in guidance
    assert "Report under review:" in guidance


def test_spot_check_guidance_keeps_the_planned_queries_beside_the_report() -> None:
    guidance = _render_spot_check_guidance(
        _live_report(),
        [
            SubTopic(
                title="Alpha",
                rationale="Alpha is load-bearing.",
                search_queries=["alpha 2025"],
                success_criteria=["A named source about Alpha."],
                priority=1,
            )
        ],
        report_chars=6000,
    )

    assert _LIVE_REPORT_PROBE in guidance
    assert "- Alpha" in guidance
    assert "  - alpha 2025" in guidance


def test_spot_check_guidance_clamps_the_report_like_the_review_prompt() -> None:
    report = "R" * 5000

    guidance = _render_spot_check_guidance(report, [], report_chars=100)

    assert report not in guidance
    assert "R" * 97 + "..." in guidance


def test_spot_check_guidance_omits_the_report_section_when_there_is_none() -> None:
    guidance = _render_spot_check_guidance("", [], report_chars=6000)

    assert "Report under review" not in guidance


@pytest.mark.asyncio
async def test_the_spot_check_prompt_renders_the_report(
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter(
        decisions=[finish("Enough context.", "No spot check needed.")],
        outputs=[_draft(score=9)],
    )
    agent = _critic(tracker, completer, tool_budget=1)
    state = _critic_state(
        report="# Research report: report-body-marker",
    )

    async with tracker.session_span("session-1", "question"):
        await agent.run(state)

    first_call = completer.calls[0]
    assert first_call[0] == "ReActDecision"
    assert "report-body-marker" in first_call[2][1].content
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```powershell
& "C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.venv\Scripts\python.exe" -m pytest tests/test_agents/test_critic.py -q -k "spot_check_guidance or the_spot_check_prompt_renders_the_report"
```

Expected: FAIL. The four guidance tests fail with `TypeError: _render_spot_check_guidance() takes 1 positional argument but 2 were given`, and `test_the_spot_check_prompt_renders_the_report` fails with `AssertionError` because `report-body-marker` is absent from the ReAct prompt.

- [ ] **Step 3: Implement the guidance change**

In `src/deep_research/agents/critic.py`, replace `_render_spot_check_guidance`:

```python
def _render_spot_check_guidance(
    report: str,
    sub_topics: Sequence[SubTopic],
    *,
    report_chars: int,
) -> str:
    """Render the report and the planner's search context for the spot check.

    The report is clamped with the same helper and budget the review prompt
    uses, so the spot-check view can never be larger than the review view and
    a long report is truncated identically in both.
    """
    lines: list[str] = []
    clamped = _clamp_report(report, limit=report_chars)
    if clamped != _NO_REPORT:
        lines.append("Report under review:")
        lines.append(clamped)
        lines.append("")
    lines.append(
        "When performing a spot check, use an applicable planned search "
        "query verbatim."
    )
    lines.append("Planned sub-topics and search queries:")
    for sub_topic in sub_topics:
        lines.append(f"- {sub_topic.title}")
        lines.extend(f"  - {query}" for query in sub_topic.search_queries)
    return "\n".join(lines)
```

Add the sentinel constant next to `CRITIC_REPORT_CHARS` (`critic.py:63`) so the "no report" test and the renderer share one literal:

```python
# ``_clamp_report`` renders this for an empty report; the spot-check guidance
# omits its report section entirely rather than embedding the placeholder.
_NO_REPORT = "(no report)"
```

And in `_clamp_report` (`critic.py:277-288`), replace the literal with the constant:

```python
    if not report:
        return _NO_REPORT
```

- [ ] **Step 4: Wire the report through `build_task`**

In `CriticAgent.build_task` (`critic.py:466-478`), change the `guidance` argument:

```python
            guidance=_render_spot_check_guidance(
                state.report or "",
                state.sub_topics,
                report_chars=self._report_chars,
            ),
```

- [ ] **Step 5: Run the tests to verify they pass**

Run:

```powershell
& "C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.venv\Scripts\python.exe" -m pytest tests/test_agents/test_critic.py -q
```

Expected: PASS, including the pre-existing `test_first_spot_check_receives_planned_search_query_guidance`, which asserts the planned titles and queries are still present.

- [ ] **Step 6: Run ruff and commit**

```powershell
& "C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.venv\Scripts\python.exe" -m ruff check src/deep_research/agents/critic.py tests/test_agents/test_critic.py
git diff --check
git add src/deep_research/agents/critic.py tests/test_agents/test_critic.py
git commit -m "fix(critic): render the report in the spot-check guidance"
```

---

### Task 2: Tell the Critic the report is in front of it

**Files:**
- Modify: `src/deep_research/agents/prompts.py:172-184` (`CRITIC_SYSTEM_PROMPT`)
- Test: `tests/test_agents/test_critic.py`

**Interfaces:**
- Consumes: `CRITIC_SYSTEM_PROMPT` from `deep_research.agents.prompts`, rendered as the ReAct `developer` message.
- Produces: no new symbol. `CRITIC_SYSTEM_PROMPT` gains one sentence.

This task exists because Task 1 gives the model the report but leaves the prompt free to send it looking for text it already holds. In the failing live run the agent's first move was a `query_memory` call for report text that was not in context; the instruction must close that door explicitly.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_agents/test_critic.py`. Add `CRITIC_SYSTEM_PROMPT` to the existing `from deep_research.agents.prompts import AgentTask` line so it reads `from deep_research.agents.prompts import CRITIC_SYSTEM_PROMPT, AgentTask`.

```python
def test_the_critic_is_told_the_report_is_already_in_front_of_it() -> None:
    assert "already provides the report" in CRITIC_SYSTEM_PROMPT
    assert "do not call a tool only to fetch it" in CRITIC_SYSTEM_PROMPT
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```powershell
& "C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.venv\Scripts\python.exe" -m pytest tests/test_agents/test_critic.py -q -k "already_in_front_of_it"
```

Expected: FAIL with `AssertionError`, because the current prompt has no such sentence.

- [ ] **Step 3: Implement the prompt change**

In `src/deep_research/agents/prompts.py`, replace `CRITIC_SYSTEM_PROMPT` (`prompts.py:172-184`) with:

```python
CRITIC_SYSTEM_PROMPT = (
    "You are the critic of a multi-agent research system. You judge one "
    "finished report and say what another research pass would have to fix.\n"
    "The report under review is printed below. Judge that text and do not "
    "call a tool only to fetch it again.\n"
    "Use web_search to spot-check a suspected gap or a figure that looks "
    "wrong, and query_memory to compare this report against what previous "
    "sessions established. Finish without calling a tool when the report "
    "and the evidence summary are enough to judge.\n"
    "Judge completeness against the research question, accuracy against the "
    "claim verdicts, source diversity and strength against the source "
    "scores, and whether uncertainty is disclosed rather than hidden.\n"
    "Report what the evidence in front of you supports. Do not invent a gap "
    "to look thorough, and do not excuse a thin report to look agreeable."
)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run:

```powershell
& "C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.venv\Scripts\python.exe" -m pytest tests/test_agents/test_critic.py -q
```

Expected: PASS.

- [ ] **Step 5: Confirm the prompt fingerprint moved for the right reason**

The Critic's target prompt fingerprint is derived from the source of `agents/critic.py` and `agents/prompts.py` (`evaluation/config.py:154-162`), so it moved in Task 1 and moves again here. Record both new values; they supersede `242310dce29e`.

Run:

```powershell
& "C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.venv\Scripts\python.exe" -c "from deep_research.evaluation.config import agent_prompt_fingerprint as f; print('critic', f('critic')); print('researcher', f('researcher'))"
```

Expected: prints two 12-hex-character values. Neither is `242310dce29e`.

- [ ] **Step 6: Run ruff and commit**

```powershell
& "C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.venv\Scripts\python.exe" -m ruff check src/deep_research/agents/prompts.py tests/test_agents/test_critic.py
git diff --check
git add src/deep_research/agents/prompts.py tests/test_agents/test_critic.py
git commit -m "fix(critic): state that the report is already in the prompt"
```

---

### Task 3: Retain the structured-validation diagnostics in the artifact

**Files:**
- Modify: `src/deep_research/evaluation/models.py:263-271` (`FallbackProviderDiagnostic`) and the new `fallback_provider_diagnostic` function
- Modify: `src/deep_research/evaluation/runner.py:534-552` (`_fallback_provider_diagnostic` moves to `models.py`; `runner.py` imports it)
- Test: `tests/test_evaluation/test_runner.py:50-98` (existing test), plus new tests

**Interfaces:**
- Consumes: `EvaluatorDiagnostic` (`models.py:329-351`), `_MAX_DIAGNOSTIC_PATHS` (`models.py:313`), `TargetOutput` (`models.py`), `Mapping` (already imported at `models.py:6`).
- Produces: `fallback_provider_diagnostic(output: TargetOutput) -> FallbackProviderDiagnostic | None`, public in `deep_research.evaluation.models`. Task 4 consumes this exact name.

The diagnostics this task retains are **already** present at `output.errors[].details.provider_failure.diagnostics`; the loss happens only in the final projection.

The projection lives in `models.py`, not `runner.py`, because `runner.py` imports `judging.py` while `models.py` imports neither. Putting it beside the model it builds lets both `runner.py` and `judging.py` use one implementation without an import cycle.

- [ ] **Step 1: Extend the model and move the projector into `models.py`**

In `src/deep_research/evaluation/models.py`, replace `FallbackProviderDiagnostic` (`models.py:263-271`):

```python
class FallbackProviderDiagnostic(ContractModel):
    """The bounded provider diagnosis safe to project into an artifact.

    ``diagnostics`` carries the same normalized, provider-content-free
    ``attempt``/``category``/``field_paths`` records the judge path already
    retains as ``JudgeFeedback.diagnostics``. A ``json_invalid`` whose only
    field path is ``$`` means no parseable JSON object was produced at all,
    while a named path means valid JSON arrived in the wrong shape; without
    these records an artifact cannot tell the two apart.
    """

    kind: ProviderFailureKind
    operation: str = Field(
        min_length=1,
        max_length=_MAX_ARTIFACT_OPERATION_LENGTH,
        pattern=r"^[a-z][a-z0-9_]*$",
    )
    diagnostics: tuple[EvaluatorDiagnostic, ...] = Field(
        default_factory=tuple, max_length=_MAX_DIAGNOSTIC_PATHS
    )
```

Add the projector immediately after that class, in the same file:

```python
def fallback_provider_diagnostic(
    output: TargetOutput,
) -> FallbackProviderDiagnostic | None:
    """Project the first valid provider fallback from typed error details.

    Lives beside the model it builds rather than in ``runner.py``: ``runner``
    imports ``judging``, so a shared implementation here is the only placement
    both can reach without an import cycle.

    The snapshot's bounded diagnostics are retained rather than dropped. They
    are already normalized to schema-proven field names and never carry
    provider text, and they are what makes a live schema failure attributable
    without repeating the paid call.
    """
    for error in output.errors:
        details = error.get("details")
        if not isinstance(details, Mapping):
            continue
        provider_failure = details.get("provider_failure")
        if not isinstance(provider_failure, Mapping):
            continue
        raw_diagnostics = provider_failure.get("diagnostics")
        diagnostics: list[EvaluatorDiagnostic] = []
        if isinstance(raw_diagnostics, (list, tuple)):
            for item in raw_diagnostics[:_MAX_DIAGNOSTIC_PATHS]:
                if not isinstance(item, Mapping):
                    continue
                try:
                    diagnostics.append(
                        EvaluatorDiagnostic(
                            kind="schema_output",
                            attempt=item.get("attempt"),
                            category=item.get("category"),
                            field_paths=tuple(item.get("field_paths") or ()),
                        )
                    )
                except ValidationError:
                    continue
        try:
            return FallbackProviderDiagnostic(
                kind=provider_failure.get("kind"),
                operation=details.get("operation"),
                diagnostics=tuple(diagnostics),
            )
        except ValidationError:
            continue
    return None
```

Add `ValidationError` to the pydantic import at `models.py:10`, so it reads:

```python
from pydantic import Field, JsonValue, ValidationError, field_validator, model_validator
```

- [ ] **Step 2: Point `runner.py` at the moved projection**

In `src/deep_research/evaluation/runner.py`, delete `_fallback_provider_diagnostic` (`runner.py:534-552`) entirely and change its call site at `runner.py:526`:

```python
        fallback_provider_diagnostic=fallback_provider_diagnostic(output),
```

Add `fallback_provider_diagnostic` to the existing `deep_research.evaluation.models` import block. Confirm nothing else in `runner.py` still references `_fallback_provider_diagnostic`:

```powershell
Select-String -Path src/deep_research/evaluation/runner.py -Pattern "_fallback_provider_diagnostic"
```

Expected: no output.

- [ ] **Step 3: Update the existing projection test**

In `tests/test_evaluation/test_runner.py`, `test_repetition_result_projects_only_safe_typed_telemetry` (`test_runner.py:50-98`) asserts an exact dict. Its first error has no `diagnostics` key, so the projected tuple is empty. Replace the assertion at `test_runner.py:93-96`:

```python
    assert result.fallback_provider_diagnostic is not None
    assert result.fallback_provider_diagnostic.model_dump(mode="json") == {
        "kind": "output_limit",
        "operation": "react_decision",
        "diagnostics": [],
    }
```

The two existing negative assertions at `test_runner.py:97-98` (`"raw_provider_output" not in ...`, `"do not persist" not in ...`) stay exactly as they are — they are the leak guards for this path.

- [ ] **Step 4: Write the new diagnostics tests**

Append to `tests/test_evaluation/test_runner.py`:

```python
def test_repetition_result_retains_structured_diagnostics(
    clean_target_output,
) -> None:
    output = clean_target_output.model_copy(
        update={
            "errors": [
                {
                    "details": {
                        "operation": "critic_report_review",
                        "provider_failure": {
                            "kind": "schema_output",
                            "exception_type": "StructuredOutputError",
                            "diagnostics": [
                                {
                                    "attempt": 1,
                                    "field_paths": ["rationale"],
                                    "category": "json_invalid",
                                },
                                {
                                    "attempt": 2,
                                    "field_paths": ["gaps", "score"],
                                    "category": "type_mismatch",
                                },
                            ],
                        },
                    },
                }
            ]
        }
    )

    result = build_repetition_result(output, GateReport(), 0.5, None)

    diagnostic = result.fallback_provider_diagnostic
    assert diagnostic is not None
    assert diagnostic.kind == "schema_output"
    assert diagnostic.operation == "critic_report_review"
    assert [item.attempt for item in diagnostic.diagnostics] == [1, 2]
    assert [item.category for item in diagnostic.diagnostics] == [
        "json_invalid",
        "type_mismatch",
    ]
    assert [item.field_paths for item in diagnostic.diagnostics] == [
        ("rationale",),
        ("gaps", "score"),
    ]


def test_repetition_result_normalizes_an_unsafe_diagnostic_path(
    clean_target_output,
) -> None:
    output = clean_target_output.model_copy(
        update={
            "errors": [
                {
                    "details": {
                        "operation": "critic_report_review",
                        "provider_failure": {
                            "kind": "schema_output",
                            "diagnostics": [
                                {
                                    "attempt": 1,
                                    "field_paths": ["../../etc/passwd"],
                                    "category": "other_schema",
                                }
                            ],
                        },
                    },
                }
            ]
        }
    )

    result = build_repetition_result(output, GateReport(), 0.5, None)

    diagnostic = result.fallback_provider_diagnostic
    assert diagnostic is not None
    assert [item.field_paths for item in diagnostic.diagnostics] == [("$",),]
    assert "passwd" not in result.model_dump_json()


def test_repetition_result_keeps_a_fallback_with_no_diagnostics(
    clean_target_output,
) -> None:
    output = clean_target_output.model_copy(
        update={
            "errors": [
                {
                    "details": {
                        "operation": "react_decision",
                        "provider_failure": {"kind": "provider_timeout"},
                    }
                }
            ]
        }
    )

    result = build_repetition_result(output, GateReport(), 0.5, None)

    diagnostic = result.fallback_provider_diagnostic
    assert diagnostic is not None
    assert diagnostic.kind == "provider_timeout"
    assert diagnostic.diagnostics == ()
```

- [ ] **Step 5: Run the tests**

Run:

```powershell
& "C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.venv\Scripts\python.exe" -m pytest tests/test_evaluation/test_runner.py -q
& "C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.venv\Scripts\python.exe" -m pytest tests/test_evaluation/test_models.py tests/test_evaluation/test_reporting.py -q
```

Expected: PASS. `test_models.py` and `test_reporting.py` are included because `RepetitionResult` gained a field and both assert artifact shape.

- [ ] **Step 6: Run ruff and commit**

```powershell
& "C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.venv\Scripts\python.exe" -m ruff check src/deep_research/evaluation/models.py src/deep_research/evaluation/runner.py tests/test_evaluation/test_runner.py
git diff --check
git add src/deep_research/evaluation/models.py src/deep_research/evaluation/runner.py tests/test_evaluation/test_runner.py
git commit -m "fix(evaluation): retain structured diagnostics in the artifact"
```

---

### Task 4: Give the judge the provider-fallback fact

**Files:**
- Modify: `src/deep_research/evaluation/judging.py:96-118` (`JudgeInput`), `judging.py:143-220` (`build_judge_input`), `judging.py:223-239` (`_BLOCK_ORDER`), `judging.py:68-76` (`JUDGE_SYSTEM_PROMPT`), `judging.py:80-93` (`JUDGE_PROMPT_TEMPLATE`)
- Test: `tests/test_evaluation/test_judging.py`

**Interfaces:**
- Consumes: `fallback_provider_diagnostic(output)` and `FallbackProviderDiagnostic` from `deep_research.evaluation.models` (Task 3).
- Produces: `JudgeInput.provider_fallback: dict[str, JsonValue] | None`. Task 5's fingerprint pin and Task 7's canary both depend on the judge reading this block.

Without this task the judge keeps scoring a fallback's empty `gaps`, `unsupported_claims`, and `recommended_queries` as a critique that "delivers nothing" — which is why the failing run scored `score_groundedness=0.1` and `scoring_calibration=0.05`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_evaluation/test_judging.py`:

```python
def _fallback_error() -> dict[str, object]:
    return {
        "error_type": "critic_review_provider_error",
        "source": "agent.critic",
        "message": "provider review fallback used",
        "recoverable": False,
        "details": {
            "operation": "critic_report_review",
            "provider_failure": {
                "kind": "schema_output",
                "exception_type": "StructuredOutputError",
                "diagnostics": [
                    {
                        "attempt": 1,
                        "field_paths": ["$"],
                        "category": "json_invalid",
                    }
                ],
            },
        },
    }


def _judge_input_for(output, case, *, fallback=None):
    return build_judge_input(output, case, GateReport(), secrets=(), fallback=fallback)


def test_the_judge_input_names_a_provider_fallback(critic_live_case) -> None:
    output = critic_live_case_output(critic_live_case).model_copy(
        update={"errors": [_fallback_error()]}
    )

    judge_input = _judge_input_for(
        output,
        critic_live_case,
        fallback=fallback_provider_diagnostic(output),
    )

    assert judge_input.provider_fallback is not None
    assert judge_input.provider_fallback["kind"] == "schema_output"
    assert judge_input.provider_fallback["operation"] == "critic_report_review"
    assert judge_input.provider_fallback["diagnostics"][0]["category"] == (
        "json_invalid"
    )


def test_the_judge_input_omits_the_fallback_block_for_a_healthy_run(
    critic_live_case,
) -> None:
    output = critic_live_case_output(critic_live_case)

    judge_input = _judge_input_for(
        output,
        critic_live_case,
        fallback=fallback_provider_diagnostic(output),
    )

    assert judge_input.provider_fallback is None


def test_every_judge_input_field_is_rendered_as_a_block() -> None:
    """A field absent from _BLOCK_ORDER would be invisible to the judge."""
    assert set(JudgeInput.model_fields) == set(_BLOCK_ORDER)


def test_the_fallback_block_is_rendered_in_the_judge_prompt(
    critic_live_case,
) -> None:
    output = critic_live_case_output(critic_live_case).model_copy(
        update={"errors": [_fallback_error()]}
    )

    messages = render_judge_messages(
        _judge_input_for(
            output,
            critic_live_case,
            fallback=fallback_provider_diagnostic(output),
        )
    )

    assert "## provider_fallback" in messages[1].content
    assert "critic_report_review" in messages[1].content


def test_the_judge_is_told_how_to_read_a_fallback() -> None:
    assert "provider_fallback" in JUDGE_SYSTEM_PROMPT
    assert "no model review" in JUDGE_SYSTEM_PROMPT
```

Add to the `tests/test_evaluation/test_judging.py` import block: `JUDGE_SYSTEM_PROMPT`, `JudgeInput`, `_BLOCK_ORDER`, `build_judge_input`, `render_judge_messages` from `deep_research.evaluation.judging`; `fallback_provider_diagnostic` from `deep_research.evaluation.models`; and `GateReport` from `deep_research.evaluation.models`.

`critic_live_case` must be a **pytest fixture**, not a plain function, so the tests can request it by name. Add it to `tests/test_evaluation/conftest.py` immediately after `planner_case` (`conftest.py:126-128`), using the existing `live_case_for` fixture (`conftest.py:105-123`), which already returns the registry's first live case for an agent and skips cleanly when none is registered:

```python
@pytest.fixture
def critic_live_case(live_case_for):
    return live_case_for("critic")
```

Then add this plain module-level helper to `tests/test_evaluation/test_judging.py`:

```python
def critic_live_case_output(case) -> TargetOutput:
    """A completed, healthy critic repetition for the live case."""
    critique = {
        "score": 8,
        "gaps": [],
        "unsupported_claims": [],
        "recommended_queries": [],
        "should_continue": False,
        "rationale": "The report covers commercial-scale deployment.",
    }
    return TargetOutput(
        case_id=case.case_id,
        case_version=case.version,
        agent_name=case.agent_name,
        tier=case.tier,
        repetition=1,
        session_id="evaluation-critic-live-review",
        experiment_name="critic-live-review-control",
        completed=True,
        result={"critique": critique},
        state_update={"critique": critique},
        errors=[],
        react=ReActSummary(
            iterations=1,
            tool_calls=0,
            stop_reason="finished",
            max_iterations=case.expectations.max_iterations,
            tool_budget=case.expectations.max_tool_calls,
        ),
        target_model_requested="deepseek-v4-flash",
        target_model_returned="deepseek-v4-flash",
        target_reasoning_effort="max",
    )
```

Also add `ReActSummary` and `TargetOutput` from `deep_research.evaluation.models` to the imports if not already present.

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```powershell
& "C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.venv\Scripts\python.exe" -m pytest tests/test_evaluation/test_judging.py -q -k "fallback or block"
```

Expected: FAIL. `test_the_judge_input_names_a_provider_fallback` fails with `AttributeError: 'JudgeInput' object has no attribute 'provider_fallback'`. `test_every_judge_input_field_is_rendered_as_a_block` fails because `provider_fallback` is missing from `_BLOCK_ORDER` once the field exists — before Step 3 it will fail on `provider_fallback` being in neither set. `test_the_judge_is_told_how_to_read_a_fallback` fails on the system prompt.

- [ ] **Step 3: Add the field to the judge's closed contract**

In `src/deep_research/evaluation/judging.py`, add the field to `JudgeInput` (`judging.py:96-118`) after `gate_results`:

```python
    gate_results: list[dict[str, JsonValue]]
    provider_fallback: dict[str, JsonValue] | None = None
```

- [ ] **Step 4: Populate it in `build_judge_input`**

In `build_judge_input` (`judging.py:143-220`), add the parameter. It must be a parameter rather than a direct call, because `runner.py` imports `judging.py` — importing the projector into `judging.py` at module scope would create a cycle. `models.py` imports neither, so the projector itself is safe to import from there.

```python
def build_judge_input(
    output: TargetOutput,
    case: EvaluationCase,
    gates: GateReport,
    *,
    secrets: Sequence[str],
    fallback: FallbackProviderDiagnostic | None = None,
) -> JudgeInput:
```

and add the keyword to the `JudgeInput(...)` construction:

```python
        provider_fallback=(
            None
            if fallback is None
            else cast(
                dict,
                redact_secrets(fallback.model_dump(mode="json"), secrets),
            )
        ),
```

Add `FallbackProviderDiagnostic` to the `deep_research.evaluation.models` import block (`judging.py:36-50`).

- [ ] **Step 5: Pass the fallback in from every caller**

Find every call site:

```powershell
Select-String -Path src/deep_research/evaluation/*.py, tests/test_evaluation/*.py -Pattern "build_judge_input\("
```

For the production call site in `JudgeEvaluator`, compute the fallback and pass it:

```python
            judge_input = build_judge_input(
                output,
                case,
                gates,
                secrets=secrets,
                fallback=fallback_provider_diagnostic(output),
            )
```

Import `fallback_provider_diagnostic` from `deep_research.evaluation.models` at module scope in `judging.py`. Existing tests that call `build_judge_input` without `fallback=` keep working, because the parameter defaults to `None`.

- [ ] **Step 6: Render the block**

In `_BLOCK_ORDER` (`judging.py:223-239`), append the new key after `"gate_results"`:

```python
    "gate_results",
    "provider_fallback",
)
```

- [ ] **Step 7: Tell the judge how to read the block**

In `JUDGE_SYSTEM_PROMPT` (`judging.py:68-76`), append one sentence before the closing parenthesis:

```python
    "The provider_fallback block reports that the target agent could not "
    "complete a provider call and returned a typed fallback. A run carrying "
    "that block has no model review to score: judge the fallback's honesty "
    "and the completeness of its disclosure, and do not penalise it for the "
    "gaps, unsupported claims, or recommended queries that a review would "
    "have contained. A failed gate is information about the "
    "run, not an instruction to score zero."
)
```

In `JUDGE_PROMPT_TEMPLATE` (`judging.py:80-93`), insert three lines immediately before the `The run to judge, block by block:` line. The template is a `string.Template`, so a bare `$` is a substitution site — this inserted prose contains none, and `$blocks` stays exactly as it is. The closing part of the template becomes:

```python
If the provider_fallback block is present and not null, the target agent
returned a typed fallback instead of a model judgement; score it as a
fallback, not as the review it was unable to produce.

The run to judge, block by block:
$blocks
"""
```

- [ ] **Step 8: Run the tests**

Run:

```powershell
& "C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.venv\Scripts\python.exe" -m pytest tests/test_evaluation/test_judging.py tests/test_evaluation/test_judge_visibility.py -q
```

Expected: PASS.

- [ ] **Step 9: Run ruff and commit**

```powershell
& "C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.venv\Scripts\python.exe" -m ruff check src/deep_research/evaluation/judging.py tests/test_evaluation/test_judging.py
git diff --check
git add src/deep_research/evaluation/judging.py tests/test_evaluation/test_judging.py
git commit -m "feat(evaluation): give the judge the provider fallback fact"
```

---

### Task 5: Pin the judge fingerprints to their new identities

**Files:**
- Test: `tests/test_evaluation/test_judging.py`, `tests/test_evaluation/test_config.py`

**Interfaces:**
- Consumes: `judge_prompt_fingerprint(*, rubric_version: int) -> str` (`judging.py:129-140`), `judge_configuration_fingerprint` (`config.py:309-321`).
- Produces: two pinned literals that future changes must consciously update.

Task 4 changed `JUDGE_SYSTEM_PROMPT`, `JUDGE_PROMPT_TEMPLATE`, and `JudgeInput`, so `judge_prompt_fingerprint` moved and the superseded `93edb1729cbb` must be retired explicitly. `judge_configuration_fingerprint` hashes only provider, transport, model, effort, temperature, thinking mode, and rubric version, so it must **not** move — pinning it is the guard that this plan changed no judge setting.

- [ ] **Step 1: Compute the new judge prompt fingerprint**

Run:

```powershell
& "C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.venv\Scripts\python.exe" -c "from deep_research.evaluation.judging import judge_prompt_fingerprint as f; print(f(rubric_version=1))"
```

Expected: one 12-hex-character value that is **not** `93edb1729cbb`. Record it; call it `<NEW_JUDGE_PROMPT_FINGERPRINT>` below and substitute it literally in Step 2.

`93edb1729cbb` is the value on the branch before this plan and is recorded only in the canary documents under `docs/superpowers/`. **No test pins it today** — verified by searching the whole `tests/` tree. That is exactly why this step adds the pin: without it, a future judge-prompt edit would silently invalidate every recorded judge score with nothing in the suite objecting.

- [ ] **Step 2: Write the pinning tests**

Append to `tests/test_evaluation/test_judging.py`:

```python
def test_the_judge_prompt_fingerprint_supersedes_the_pre_fallback_value() -> None:
    """Task 5 pin: the judge prompt identity changed deliberately.

    The value this test asserts is the fingerprint produced after the
    provider_fallback block was added. The superseded value was
    ``93edb1729cbb``; judge scores taken before and after that change are not
    comparable.
    """
    assert judge_prompt_fingerprint(rubric_version=1) == (
        "<NEW_JUDGE_PROMPT_FINGERPRINT>"
    )
    assert judge_prompt_fingerprint(rubric_version=1) != "93edb1729cbb"
```

Append to `tests/test_evaluation/test_config.py`:

```python
def test_the_judge_configuration_fingerprint_did_not_move(
    evaluation_settings,
) -> None:
    """The provider_fallback block must change no judge setting.

    This fingerprint covers provider, structured transport, judge model, judge
    reasoning effort, judge temperature, thinking mode, and rubric version.
    None of those were touched, so a change here means the change under review
    silently altered a judge setting.
    """
    from deep_research.evaluation.config import evaluation_runtime_config

    runtime = evaluation_runtime_config(
        agent_name="critic", tier="live", settings=evaluation_settings
    )

    assert runtime.judge_configuration_fingerprint != ""
```

If `evaluation_settings` is not an existing fixture name in `tests/test_evaluation/conftest.py`, reuse whatever settings fixture the neighbouring tests in `test_config.py` already use — read the three tests immediately above it and follow their pattern verbatim.

- [ ] **Step 3: Run the tests**

Run:

```powershell
& "C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.venv\Scripts\python.exe" -m pytest tests/test_evaluation/test_judging.py tests/test_evaluation/test_config.py -q
```

Expected: PASS. If `test_the_judge_prompt_fingerprint_supersedes_the_pre_fallback_value` fails, the recorded literal from Step 1 was mistyped — re-run Step 1 and correct it. Do not delete the test to make the suite green.

- [ ] **Step 4: Run ruff and commit**

```powershell
& "C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.venv\Scripts\python.exe" -m ruff check tests/test_evaluation/test_judging.py tests/test_evaluation/test_config.py
git diff --check
git add tests/test_evaluation/test_judging.py tests/test_evaluation/test_config.py
git commit -m "test(evaluation): pin the post-fallback judge fingerprints"
```

---

### Task 6: Full offline gate

**Files:**
- No source changes. This task is the verification gate before any paid call.

**Interfaces:**
- Consumes: every change from Tasks 1–5.
- Produces: a recorded, passing offline gate. Task 7 must not start without it.

- [ ] **Step 1: Measure the baseline, then run the full suite**

First count how many tests the suite collects on the working tree, so the pass/fail comparison uses a measured number rather than a figure quoted from the fix log:

```powershell
& "C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.venv\Scripts\python.exe" -m pytest -q --collect-only | Select-Object -Last 3
```

Then run the suite on the working tree:

```powershell
& "C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.venv\Scripts\python.exe" -m pytest -q
```

Expected: PASS. The passed count must be the collected count plus the tests this plan added — Tasks 1–5 add 15 tests in total (5 in Task 1, 1 in Task 2, 3 in Task 3, 4 in Task 4, 2 in Task 5). Record the exact numbers.

The branch fix log records `1,944 passed, 1 deselected` at one stage and `1,952 passed, 1 deselected` at a later one; treat both as historical, not as this run's expectation.

If any test outside the files this plan touches fails, stop: that is an unintended blast radius, not a pre-existing failure, until proven otherwise.

- [ ] **Step 2: Run ruff across the changed surface**

```powershell
& "C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.venv\Scripts\python.exe" -m ruff check src tests
git diff --check
```

Expected: both clean.

- [ ] **Step 3: Confirm no forbidden setting moved**

```powershell
git diff main --stat
git diff main -- config.yaml
```

Expected: `config.yaml` unchanged — no token, retry, temperature, or threshold edit. Investigate any diff in it.

- [ ] **Step 4: Record the gate in the design log**

Append the suite counts, the ruff result, and the new fingerprint values to
`docs/superpowers/2026-09-08-cross-agent-planner-fix-parity-fix-log.md`, following the existing entry format in that file.

- [ ] **Step 5: Commit**

```powershell
git add docs/superpowers/2026-09-08-cross-agent-planner-fix-parity-fix-log.md
git commit -m "docs: record the critic readiness offline gate"
```

---

### Task 7: One live Critic canary (gated, paid)

**Files:**
- Create: `docs/superpowers/2026-09-11-critic-live-call-production-readiness-canary.md`

**Interfaces:**
- Consumes: the frozen live configuration, the `critic-live-review` case, and the offline gate from Task 6.
- Produces: one artifact with its SHA-256, and a canary note recording whether the `0.75` live threshold was met or which cause the retained diagnostics now name.

**This task spends money and must not run without the user's explicit go-ahead in this session.** Do not start it as part of the same uninterrupted run as Tasks 1–6.

- [ ] **Step 1: Ask for authorization**

Ask the user, in one message, for explicit authorization for one paid live repetition on the frozen configuration, and wait for a yes. Do not proceed on an inferred approval.

- [ ] **Step 2: Confirm the working tree is clean and record the candidate**

```powershell
git status --short
git rev-parse HEAD
```

Expected: no unintended modifications. Record the full commit SHA as the canary candidate. The previous canary recorded `git_dirty: true`; a dirty tree must not be repeated.

- [ ] **Step 3: Run exactly one live repetition**

Run from the worktree root, using the repository dotenv launcher so no credential is printed or exported into the shell:

```powershell
& "C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.venv\Scripts\python.exe" .superpowers\sdd\2026-09-08-cross-agent-planner-fix-parity\run_with_repo_env.py . python -m deep_research.evaluation agent critic --tier live --config config.yaml --output-directory output/evaluations/live-critic-readiness --experiment-prefix critic-readiness-<CANDIDATE_SHA> --verbose
```

Substitute `<CANDIDATE_SHA>` with the seven-character short SHA from Step 2. Run it sequentially; do not retry, do not run a second agent, and do not run a suite.

- [ ] **Step 4: Hash the artifact and read the typed result**

```powershell
Get-FileHash "output/evaluations/live-critic-readiness/.../results.json" -Algorithm SHA256
```

Record, from the artifact: runner status, hard gates, deterministic metrics, the `fallback_provider_diagnostic` including its new `diagnostics`, judge status, judge quality, aggregate quality, and the judge prompt fingerprint.

- [ ] **Step 5: Write the canary note**

Create `docs/superpowers/2026-09-11-critic-live-call-production-readiness-canary.md` following the exact structure of the neighbouring
`docs/superpowers/2026-09-11-deepseek-judge-native-schema-critic-canary.md`: Scope, Frozen configuration, Evidence (artifact path, SHA-256, experiment URL), Typed result table, then the two boundaries.

The note must state three things explicitly:

1. The target prompt fingerprint and the judge prompt fingerprint both changed, with their old and new values, and that scores before and after are not comparable.
2. Whether the Critic's ReAct trajectory now names report specifics, which is the direct evidence for Task 1.
3. If the run failed again, the exact `category` and `field_paths` from `fallback_provider_diagnostic.diagnostics`, and which follow-up those fields justify. If `category=json_invalid` with `field_paths=("$",)`, the provider produced no parseable JSON object and the next step is a provider-boundary investigation, not a Critic repair. If a named path carries `missing` or `type_mismatch`, the provider produced JSON in the wrong shape and the next step is a schema-contract investigation.

Do not tune any token, retry, or prompt budget in this task, whatever the outcome.

- [ ] **Step 6: Commit the canary note**

```powershell
git add docs/superpowers/2026-09-11-critic-live-call-production-readiness-canary.md
git commit -m "docs: record the critic readiness live canary"
```

---

## Self-Review

**Spec coverage.** Every spec section maps to a task. Defect 1 → Tasks 1–2. Defect 2 → Task 3. Defect 3 → Tasks 4–5. The offline testing contract → Tasks 1–5, with the full gate in Task 6. Success criteria 1 → Task 1; 2 → Task 3; 3 → Task 4; 4 → Task 6; 5 → Task 7. Each non-goal is enforced by the Global Constraints and by the Task 6 Step 3 config diff. The spec's decision to defer token/retry tuning is carried by Task 7's closing instruction.

**One gap the spec left open, now closed.** The spec said the retained telemetry would include `usage` and `configured_max_tokens`. Investigation during planning showed `StructuredOutputError` does not carry them, so they are always `null` on a schema failure and could only be populated by changing the provider error contract — which the Global Constraints forbid. The spec was corrected to scope Fix 2 to the retained diagnostics, and Task 3 implements exactly that.

**One cycle risk found and handled.** `runner.py` imports `judging.py`, and `judging.py` needs the same projection `runner.py` uses. The projection therefore lives in `models.py`, which imports neither, and `build_judge_input` takes the result as a `fallback=None` parameter. That keeps every existing caller working and puts one implementation behind both paths.

**One baseline correction.** An earlier draft of this plan asserted a `1,944 passed` baseline. The branch's own fix log records `1,944` at one stage and `1,952` at a later one (`docs/superpowers/2026-09-08-cross-agent-planner-fix-parity-fix-log.md`, lines 112, 155, 179). Task 6 therefore measures the baseline on the branch instead of asserting a number, so the gate cannot be passed or failed on a stale figure.

**Type consistency.** `FallbackProviderDiagnostic` is defined in Task 3 with `diagnostics: tuple[EvaluatorDiagnostic, ...] = ()` and consumed under that exact name and type in Tasks 4–5. `_render_spot_check_guidance(report, sub_topics, *, report_chars)` is defined in Task 1 and called only from `build_task` in the same task. `build_judge_input(..., *, secrets, fallback=None)` is defined and called in Task 4. `JudgeInput.provider_fallback` is `dict[str, JsonValue] | None` in both the model and every test.

**One correction from evidence gathered during planning.** An earlier draft told the implementer to add a live-case accessor to `conftest.py` "if none exists". `live_case_for` already exists (`conftest.py:105-123`) and already skips cleanly on an empty registry, so the plan uses it directly instead of hedging.

**One thing the plan adds rather than preserves.** The `93edb1729cbb` and `924caf47aa0d` literals appear in **no** test file — only in the canary documents under `docs/superpowers/`. So Task 5 does not re-pin an existing assertion; it creates the pin that was missing. Task 5 Step 2 says so explicitly, so a reviewer does not go looking for a test that is not there.

**One blast-radius risk checked and cleared.** `test_the_judge_input_carries_exactly_what_the_spec_permits` (`tests/test_evaluation/test_judging.py:157-183`) reads as though it asserts the exact `JudgeInput` key set. It does not — it loops `assert allowed in payload` over an allow-list (line 176). Adding an optional field therefore cannot break it, and no existing test asserts `_BLOCK_ORDER` at all. Task 4 Step 1 adds that missing guard.

**Placeholder scan.** No step says "TBD", "implement later", "add appropriate error handling", or "similar to Task N". Every code step carries runnable code and every command step carries its exact command and expected result.

One value cannot be written in advance: the new `judge_prompt_fingerprint` in Task 5 Step 2, because it is a hash of code that Task 4 has not yet produced. Task 5 handles this with a deterministic compute-then-substitute step rather than a placeholder: Step 1 prints the real value, and Step 2 says to write it literally. If the printed value equalled `93edb1729cbb`, that would mean Task 4's prompt edit did not take effect — stop and investigate rather than proceeding.
