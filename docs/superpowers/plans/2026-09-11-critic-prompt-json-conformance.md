# Critic Prompt JSON-Conformance Plan

**Goal:** Eliminate the residual `critic_report_review` `json_invalid` failure by making the review request explicitly JSON-shaped, and measure the change with enough repetitions to distinguish a real improvement from luck.

**Architecture:** A bounded prompt change to `CRITIQUE_INSTRUCTION` plus one added section in `critique_messages`. No transport change, no budget change, no provider change. Because `json_invalid` depends only on the request shape, the failure rate is measured with a direct provider probe rather than through the evaluation harness, which makes a statistically useful sample affordable.

**Tech Stack:** Python 3.12, Pydantic v2, pytest + pytest-asyncio, Ruff, the OpenAI SDK against DeepSeek.

**References:** fix-log sections 78 and 79 (the closed enforced-JSON family), `docs/superpowers/2026-09-11-critic-readiness-confirmation.md` Finding 5, `docs/superpowers/plans/2026-09-11-deepseek-beta-strict-tools-probe.md`.

## Why this path, and why it is measured this way

Three levers have now been tried, and **none of them was independently measured**:

| Lever | Commit | Evidence it helped |
| --- | --- | --- |
| Evidence retention | `fe434cf` | Diagnostic only; not a reliability claim |
| Schema-enforced Responses transport | `2fe4e32` | Bundled; no baseline |
| Budgets 4096 → 32768 | `96fe8a9`, `035d3c5` | Confounded with the above |

Every failure rate quoted so far (4/5, 1/3, 3/4, 3/3) comes from a **different configuration**, so none of them is a baseline for the next change. This plan fixes that first.

**The measurement insight.** `json_invalid` at `field_paths` `("$",)` means the provider returned text that is not parseable JSON. That outcome is a function of the request the provider receives, and of nothing else — not the judge, not the harness, not LangSmith. So the failure rate can be measured by sending the **real rendered review prompt** to the provider N times and counting parse failures. That is roughly N cheap requests instead of N full evaluations (each of which costs a ReAct loop, judge call, tracing, and minutes).

**What the probe does not prove.** It does not run the agent, so it says nothing about `CritiqueDraft` *semantic* quality, spot-check behaviour, or the judge's score. It measures one thing — whether the structured call returns parseable JSON — which is exactly the defect. Semantic quality remains the evaluation harness's job, in a later gate.

## Vendor guidance (checked 2026-09-11)

DeepSeek's own [JSON Output guide](https://api-docs.deepseek.com/guides/json_mode/) states that to enable reliable JSON Output a caller should:

1. Set `response_format` to `{'type': 'json_object'}`.
2. **"Include the word \"json\" in the system or user prompt, and provide an example of the desired JSON format to guide the model in outputting valid JSON."**
3. "Set the `max_tokens` parameter reasonably to prevent the JSON string from being truncated midway."
4. **"When using the JSON Output feature, the API may occasionally return empty content. We are actively working on optimizing this issue. You can try modifying the prompt to mitigate such problems."**

Measured against the live-tested commit, the Critic review request satisfies requirement 1 and requirement 3 (a `32768` budget) and **violates requirement 2 outright** — the prompt contains zero JSON lines and never uses the word "json".

Two consequences shape this plan:

- **Prompting is the vendor-documented remedy for the residual defect, not a guess.** Requirement 2 is exactly what Task 2 adds.
- **Requirement 4 names an empty-content mode that this codebase cannot currently see.** `_responses_structured_attempt` accepts any `str`, including `""`, and passes it to `schema.model_validate_json(text)`, where it raises `JSONDecodeError` and is classified `json_invalid`. An empty response and a malformed one are therefore indistinguishable in the artifact today. Task 1b fixes that, because otherwise Task 3's measurement cannot say which mode it reduced.

**What the vendor guidance does not claim.** It does not promise elimination, and the empty-content issue is described as open. A prompt change is therefore expected to reduce the rate, not provably reach zero, and the plan's decision rules are written accordingly.

## The hypothesis

Measured on the live-tested commit (`1a20a5d`, `e8bed19`):

| | Critic review | Judge prompt |
| --- | --- | --- |
| chars | 5,658 | 7,367 |
| `##` sections | 13 | 16 |
| **JSON-ish lines** | **0** | **147** |
| **word "JSON"** | **0** | **0** |

The judge path has scored in every run with zero `json_invalid`. It shares the model, thinking mode, transport, and reasoning effort with the Critic, and it is **larger** — so size is not the differentiator. Its prompt is dense with JSON; the Critic's contains none, and `CRITIQUE_INSTRUCTION` does not use the word "JSON" at all. DeepSeek's own JSON-mode guidance requires the word JSON in the prompt and a sample output.

**Hypothesis:** a review request that names JSON and shows its exact output shape will reduce the `json_invalid` rate.

## Global constraints

- Work in `C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.worktrees\cross-agent-planner-fix-parity`, branch `codex/cross-agent-planner-fix-parity`. Verify branch and tracked status before edits.
- Each PowerShell session begins with:

  ```powershell
  $transportPython = 'C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.venv\Scripts\python.exe'
  $env:PYTHONPATH = Join-Path (Get-Location).Path 'src'
  & $transportPython -c "import deep_research; print(deep_research.__file__)"
  ```

- Baseline full gate, to be re-verified not assumed: `3 failed, 2086 passed, 1 deselected` with the three named pre-existing failures. Repro-duce and compare failure **reasons**. Never repair those three here.
- Preserve: all five `32768` budgets, retry policy, temperature, `thinking_mode: enabled`, reasoning effort, frozen cases, rubrics, weights, the `0.75` live threshold, fallback semantics, the `review_produced` gate, and judge isolation.
- Preserve the scoring contract verbatim: the 1–10 scale, "list a gap only when closing it would materially change the answer", the empty-list rules, and "Do not decide whether research continues".
- **Do not add a numeric example score.** An example like `"score": 8` anchors the model's score, which would change critique semantics while appearing to fix formatting. Any example uses a non-committal placeholder.
- Do not reintroduce the rejected first/last-character brace rule; it competes with the tool and schema channels and was removed for that reason.
- No paid DeepSeek request without explicit authorization stating the request count. Task 1 and Task 3 each need it.
- Never record raw provider bodies, prompts, secrets, or reasoning content.

---

### Task 1: Measure the pre-change failure rate (baseline)

**Files:** temporary probe under `output/transport-probes/<uuid>/`. Fix log entry after the run.

**Interfaces:** consumes the real rendered review prompt via `CriticAgent.build_task` and `critique_messages`; produces a counted baseline: N requests, parse failures, and non-`json_invalid` outcomes.

Without this, the change in Task 2 cannot be evaluated. This task exists because every previous lever lacked it.

- [ ] **Step 1: Write the baseline probe**

It must build the prompt from the live case exactly as the agent does, send it through the project's own provider stack (so the transport, budget, thinking mode, and effort are the production ones), and classify each response.

```python
"""Measure the Critic review call's json_invalid rate. Request-shape probe only."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from deep_research.agents.critic import (  # noqa: E402
    CRITIC_CLAIM_DIGEST,
    CRITIC_REPORT_CHARS,
    CriticAgent,
    CritiqueDraft,
    critique_messages,
)
from deep_research.agents.steps import ReActRun  # noqa: E402
from deep_research.evaluation.cases.critic import LIVE_CASES  # noqa: E402
from deep_research.memory.scratchpad import ScratchpadMemory  # noqa: E402
from deep_research.observability import LangSmithRuntimeConfig, Tracker  # noqa: E402
from deep_research.agents.errors import AgentConfigurationError  # noqa: E402
from deep_research.providers import ProviderError  # noqa: E402
from deep_research.providers.factory import build_chat_provider  # noqa: E402
from deep_research.utils.config import AgentRuntimeConfig, load_config  # noqa: E402


class _FakeTool:
    def __init__(self, name: str) -> None:
        self.name = name
        self.description = f"Synthetic {name}."
        self.input_schema: dict[str, object] = {"type": "object", "properties": {}}
        self.output_schema: dict[str, object] = {}


def build_prompt() -> list:
    case = LIVE_CASES[0]
    state = case.fresh_state()
    agent = CriticAgent(
        provider=object(),  # type: ignore[arg-type]
        tracker=Tracker(LangSmithRuntimeConfig(tracing_enabled=False)),  # type: ignore[arg-type]
        scratchpad=ScratchpadMemory(
            session_id="probe", agent_name="critic", max_entries=20
        ),
        tools=[_FakeTool("web_search"), _FakeTool("query_memory")],
        config=AgentRuntimeConfig(),
    )
    task = agent.build_task(state)
    return critique_messages(
        task,
        ReActRun(agent_name="critic", stop_reason="finished"),
        report_chars=CRITIC_REPORT_CHARS,
        claim_digest=CRITIC_CLAIM_DIGEST,
    )


async def main() -> None:
    runs = int(os.environ.get("PROBE_RUNS", "30"))
    settings = load_config(str(Path.cwd() / "config.yaml"), strict=False)
    tracker = Tracker(LangSmithRuntimeConfig(tracing_enabled=False))
    provider = build_chat_provider(settings.llm, tracker)
    messages = build_prompt()
    outcomes: dict[str, int] = {}
    async for _ in _iterate(runs, provider, messages, outcomes):
        pass
    print(json.dumps({"runs": runs, "outcomes": outcomes}, sort_keys=True))


async def _iterate(runs, provider, messages, outcomes):
    async with provider._tracker.session_span("probe", "critic baseline"):
        for index in range(runs):
            try:
                await provider.complete_structured(
                    messages, CritiqueDraft, agent_name="critic"
                )
            except ProviderError as error:
                name = type(error).__name__
                diagnostics = getattr(error, "diagnostics", ())
                category = (
                    diagnostics[0].category if diagnostics else None
                ) or "unknown"
                key = f"{name}:{category}"
            else:
                key = "ok"
            outcomes[key] = outcomes.get(key, 0) + 1
            print(json.dumps({"run": index + 1, "outcome": key}), flush=True)
            yield None


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Authorize and run N=30**

Ask for authorization for **30** DeepSeek requests, stating: the prompt is the production Critic review prompt, `max_tokens` is the configured `32768`, thinking is enabled at the configured effort, and no judge/LangSmith/search/evaluation harness is involved. Then run it through the repository dotenv launcher with `PROBE_RUNS=30`.

Expected shape: mostly `ok`, with some `StructuredOutputError:json_invalid`. If **zero** failures appear in 30, stop and reconsider: the defect may depend on the ReAct spot-check evidence, which this probe holds empty, and the plan must switch to harness-based measurement.

- [ ] **Step 3: Record the baseline**

Append the outcome counts, run count, candidate SHA, effective settings, and the exact per-run outcome list to the fix log as a new numbered section. This number is the comparison anchor; do not proceed without it.

- [ ] **Step 4: Commit the baseline record**

```powershell
git diff --check
git add docs/superpowers/2026-09-08-cross-agent-planner-fix-parity-fix-log.md
git commit -m "docs: record the critic review json_invalid baseline rate"
```

---

### Task 1b: Make an empty structured response visible

**Files:** `src/deep_research/providers/deepseek_provider.py`, `tests/test_deepseek_provider.py`.

**Interfaces:** `_StructuredValidationFailure` gains a distinct diagnostic category for an empty response, so the artifact can distinguish "the provider returned nothing" from "the provider returned malformed JSON". Both remain `StructuredOutputError` at the public boundary; only the bounded `category` changes.

**Why this comes before measurement.** Today both modes produce `category=json_invalid` at `field_paths=("$",)`. If a material share of the residual failures is vendor-documented empty content, Task 3 would measure a reduction without being able to attribute it, and a future reader would not know which mode remained.

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_an_empty_structured_response_is_its_own_category() -> None:
    """Empty output must not be reported as malformed JSON.

    DeepSeek documents that JSON Output "may occasionally return empty
    content". ``_responses_structured_attempt`` currently accepts any str,
    including "", and hands it to ``model_validate_json``, where it raises
    JSONDecodeError and is recorded as ``json_invalid``. The two modes are
    therefore indistinguishable in an artifact, which blocks attribution.
    """
    responses = RecordingResponses(
        responses_response(output_text=""),
        responses_response(output_text=""),
    )
    tracker = local_tracker()
    provider = deepseek_module.DeepSeekSchemaChatProvider(
        deepseek_config(structured_transport="json_schema"),
        tracker,
        client=FakeDeepSeekClient(responses=responses),
    )

    async with tracker.session_span("session-1", "question"):
        with pytest.raises(StructuredOutputError) as caught:
            await provider.complete_structured(
                [ChatMessage(role="user", content="decide")], TinyAnswer
            )

    assert [d.category for d in caught.value.diagnostics] == [
        "schema_output",
        "schema_output",
    ]
    assert [d.field_paths for d in caught.value.diagnostics] == [("$",), ("$",)]
    assert len(responses.calls) == 2
```

`category="schema_output"` is the honest label for an empty envelope: it reuses an existing member of `StructuredDiagnosticCategory` and never carries provider text. The distinguishing fact is the empty body itself.

- [ ] **Step 2: Run it and confirm the current behaviour**

```powershell
& $transportPython -m pytest tests/test_deepseek_provider.py -q -k empty_structured_response -p no:cacheprovider
```

Expected: FAIL, with the diagnostics reporting `json_invalid` — which is the point of the task.

- [ ] **Step 3: Guard the empty case before validation**

In `_responses_structured_attempt`, extend the existing text guard so an empty body is classified as an empty envelope rather than parsed:

```python
            text = getattr(response, "output_text", None)
            if not isinstance(text, str) or not text.strip():
                response = None
                raise _StructuredValidationFailure(
                    schema.__name__,
                    StructuredValidationDiagnostic(
                        attempt=attempt,
                        field_paths=("$",),
                        category="schema_output",
                    ),
                ) from None
```

This mirrors `_choice_text`'s existing non-empty requirement on the chat path, so both transports now agree that an empty body is an envelope failure. It does not add an attempt: an empty body still consumes one of the two structured attempts and still goes through the single repair.

- [ ] **Step 4: Verify GREEN, the whole provider module, and Ruff**

```powershell
& $transportPython -m pytest tests/test_deepseek_provider.py -q -p no:cacheprovider
& $transportPython -m ruff check src/deep_research/providers/deepseek_provider.py tests/test_deepseek_provider.py
git diff --check
git add src/deep_research/providers/deepseek_provider.py tests/test_deepseek_provider.py
git commit -m "fix(provider): report an empty structured response as its own category"
```

- [ ] **Step 5: Confirm the offline gate is unmoved**

```powershell
& $transportPython -m pytest -q -p no:cacheprovider
```

Expected: the three named pre-existing failures and no new ones. Record the counts; this change touches a shared provider path and must not disturb the judge suite.

---

### Task 2: Make the review request explicitly JSON-shaped

**Files:** `src/deep_research/agents/prompts.py`, `src/deep_research/agents/critic.py`, `tests/test_agents/test_critic.py`.

**Interfaces:** `CRITIQUE_INSTRUCTION` gains JSON-naming sentences; `critique_messages` gains one `## Reply format` section. Both additive; no field, scale, or threshold changes.

- [ ] **Step 1: Add the tests and observe RED**

```python
def test_the_review_request_names_json_and_shows_its_shape() -> None:
    """The request must say JSON, and show the object it wants.

    Measured on the live-tested commit: the review prompt contained zero JSON
    lines and never used the word JSON, while the judge prompt — which has never
    recorded a json_invalid failure on the same model, transport, and effort —
    contained 147 JSON lines. DeepSeek's JSON-mode guidance requires naming JSON
    and supplying a sample output.
    """
    body = critique_messages(
        _task(),
        ReActRun(agent_name="critic", stop_reason="finished"),
        report_chars=6000,
        claim_digest=40,
    )[1].content

    assert "JSON" in body
    assert "## Reply format" in body
    for field in ("score", "gaps", "unsupported_claims", "recommended_queries", "rationale"):
        assert f'"{field}"' in body


def test_the_reply_example_does_not_anchor_the_score() -> None:
    """No concrete score may appear: an example number would bias scoring."""
    body = critique_messages(
        _task(),
        ReActRun(agent_name="critic", stop_reason="finished"),
        report_chars=6000,
        claim_digest=40,
    )[1].content

    for anchored in ('"score": 8', '"score": 7', '"score": 9', '"score": 1'):
        assert anchored not in body


def test_the_json_demand_preserves_the_scoring_contract() -> None:
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

Run `& $transportPython -m pytest tests/test_agents/test_critic.py -q -k "json or shape or anchor or contract" -p no:cacheprovider`. Expect the first test to fail; the contract test should already pass.

- [ ] **Step 2: Extend the instruction**

Append to `CRITIQUE_INSTRUCTION`, preserving every existing sentence verbatim:

```python
    "\nReply with a single JSON object. Do not wrap it in Markdown code "
    "fences and do not write any prose before or after it. The object must "
    "carry exactly the fields described above."
```

- [ ] **Step 3: Add the reply-format section**

In `critique_messages`, append a final section to `sections`, after the response contract:

```python
        (
            "## Reply format\n"
            "Return one JSON object with these five fields and no others:\n"
            '{"score": <integer 1-10>, "gaps": [<string>, ...], '
            '"unsupported_claims": [<string>, ...], '
            '"recommended_queries": [<string>, ...], '
            '"rationale": "<string>"}'
        ),
```

The placeholders are deliberately non-committal so the example cannot anchor the score.

- [ ] **Step 4: Verify GREEN and the full critic module**

```powershell
& $transportPython -m pytest tests/test_agents/test_critic.py -q -p no:cacheprovider
& $transportPython -m ruff check src/deep_research/agents/prompts.py src/deep_research/agents/critic.py tests/test_agents/test_critic.py
git diff --check
git add src/deep_research/agents/prompts.py src/deep_research/agents/critic.py tests/test_agents/test_critic.py
git commit -m "fix(critic): make the review request explicitly JSON-shaped"
```

- [ ] **Step 5: Record the new target prompt fingerprint**

The Critic's target prompt fingerprint changes because `prompts.py` and `critic.py` changed. Record the new value in the fix log; scores before and after are not comparable on that axis.

---

### Task 3: Measure the post-change failure rate

**Files:** fix log only.

**Interfaces:** consumes Task 1's probe unchanged and Task 2's prompt; produces the comparison.

- [ ] **Step 1: Re-run the identical probe, N=30**

Ask for authorization for **30** further DeepSeek requests. Same probe file, same settings, same run count — the only difference is the prompt.

- [ ] **Step 2: Compare against Task 1 and state the uncertainty**

Record both outcome distributions and the difference. State the limitation explicitly: a probe measures the request-shape failure only, and the sample is small, so the result bounds the effect rather than proving a property.

Rough guide for reading it, which must be included in the record: at N=30 with a baseline rate near 25%, observing 0 failures would be strong evidence of improvement; observing 5–8 failures would suggest no meaningful change. Do not describe a single clean run as a fix.

- [ ] **Step 3: Decide the next gate**

- **Baseline rate measured, post-change rate materially lower** → proceed to a full evaluation-harness canary in a **separate** plan: 11 Critic live repetitions with a typed result guard, per the reviewed plan's Task 7 statistical framing, to confirm semantic quality held and the aggregate clears `0.75`.
- **No material difference** → the prompt is not the lever. Record it honestly, and choose between micro-decomposing the review into smaller structured calls, or accepting the `review_produced` gate as the containment and moving on.
- **Baseline shows zero failures in 30** → the probe is not exercising the real failure; switch to harness-based measurement and treat Task 2 as unvalidated.

- [ ] **Step 4: Commit the comparison**

```powershell
git diff --check
git add docs/superpowers/2026-09-08-cross-agent-planner-fix-parity-fix-log.md
git commit -m "docs: record the critic json_invalid before-and-after measurement"
```

---

## Execution self-review

- **Vendor-grounded, not guessed.** DeepSeek's JSON Output guide names prompting as the remedy for both malformed and empty structured output. Requirement 2 is what Task 2 implements; requirement 4 is what Task 1b makes visible. The plan does not claim elimination, because the vendor describes empty content as an open issue.
- **Attribution before measurement.** Task 1b lands before Task 3 so the comparison can report *which* failure mode changed. Measuring a rate without it would leave the result uninterpretable even if the number improved.
- **Measurement first.** Task 1 exists because all three prior levers were unmeasured and bundled. No claim of improvement is possible without it.
- **No anchoring.** The reply example uses placeholders, and a test forbids a concrete score, because an earlier draft's `"score": 8` would have altered critique semantics while appearing to fix formatting.
- **No dead levers.** The plan does not touch transport, budget, retry, endpoint, or strict mode: sections 78 and 79 closed that family with recorded evidence, and the vendor guidance now explains why prompting is the remaining instrument.
- **Honest stop conditions.** Zero baseline failures means the probe is wrong, not that the defect is fixed; no improvement means the prompt is not the lever. Both are stated outcomes.
- **Scope discipline.** Semantic quality, judge score, and the `0.75` threshold are explicitly out of this plan and belong to the follow-on harness canary.
- **One residual risk.** Task 1b changes a shared provider path used by the judge as well as the targets. Its Step 5 full-gate check exists specifically to prove the judge suite is unmoved.
