# Critic Live-Call Production Readiness — Final Canary

Date: 2026-09-11

## Outcome

One Critic live repetition passed. `Cases: 1/1 passed`, `Mean score: 0.77`,
runner exit `0`. This is the campaign's first passing live Critic run, after
four consecutive failing repetitions on the same case.

## The failure chain, and what each canary proved

Five paid repetitions ran in sequence. Each one is a distinct experiment, and
each one moved exactly one variable.

| # | Commit | Change | Aggregate | `rationale_present` | Failure |
| --- | --- | --- | --- | --- | --- |
| 1 | `fe434cf` | Retained structured diagnostics in the artifact | `0.6995` | `0.0` | `json_invalid`, `json_invalid` |
| 2 | `2fe4e32` | Schema-enforced target structured transport | `0.73` | `0.0` | `json_invalid`, `extra_forbidden` |
| 3 | `750472b` | Critic review output budget `4096 -> 8192` | n/a | **`1.0`** | target fixed; `judge_output_limit` |
| 4 | `7f84378` | Judge output budget `4096 -> 8192` | n/a | `1.0` | still `judge_output_limit` |
| 5 | `96fe8a9` | Both large-call budgets `8192 -> 32768` | **`0.7725`** | `1.0` | **pass** |

### What canary 1 established

The artifact had been discarding the per-attempt structured-validation
diagnostics. Retaining them made a live schema failure attributable for the
first time: `category=json_invalid, field_paths=("$",)` on both the initial
attempt and the single repair. That result rules out output-limit truncation,
because `_structured_attempt` raises `ProviderOutputLimitError` on a `length`
finish reason before validation ever runs, and `_choice_text` rejects anything
but `stop` plus non-empty content. Both attempts therefore ended `stop` with
non-empty text that was not parseable JSON.

### What canary 2 established

Moving target structured output onto the provider's native `json_schema` (the
transport the judge already used successfully on the same model in the same
run) changed the repair attempt's failure shape from `json_invalid` to
`extra_forbidden`. That proves schema enforcement reached the request, but the
target still produced no review, so the transport was a contributing factor
rather than the cause.

### What canaries 3 and 4 established

With the review budget raised, the target side became fully healthy:
`deterministic 1.00`, `rationale_present 1.0`, `react_stop_reason=finished`,
and **no fallback diagnostic at all**. The only remaining failure moved to the
judge, which hit `output_limit` at its own global cap and returned no score —
an infrastructure failure rather than a quality result.

Canary 4 raised the judge budget to `8192` and the judge *still* hit
`output_limit` on attempt 1, with the resolved configuration independently
verified as `agents.judge_max_tokens = 8192`. That is the measurement that
settled the question: the judge's verdict — six common dimensions, the
agent-specific dimensions, and a rationale, over a `JudgeInput` that carries
the agent output, state update, evidence, trajectory, and gate results — does
not fit in the budget that the global cap had imposed.

### Canary 5 — the pass

| Signal | Result |
| --- | --- |
| Runner exit | `0` |
| Runner status | `REVIEW REQUIRED` |
| Case | `critic-live-review`, version `1` |
| Case passed | `True` |
| Repetitions | `1/1` completed |
| Average / aggregate quality | `0.7725` (live threshold `0.75`) |
| Deterministic quality | `0.75` |
| Deterministic metrics | `score_bounded=1.0`, `route_consistent=1.0`, `rationale_present=1.0`, `no_spurious_gaps=0.0` |
| Judge status | `scored` |
| Judge quality | `0.7875` |
| Judge diagnostics | empty |
| Target fallback diagnostic | **none** |
| ReAct stop reason | `finished` |
| Prohibited calls | `0` |
| Failures / errors | none |
| Hard gates failed | `0` of `14` |

## Evidence

Artifact:

`output/evaluations/live-critic-readiness/critic/critic-readiness-96fe8a9-critic-live-20260911T223836Z-96fe8a9/results.json`

SHA-256:

`5776717CEB3F8C99B00DBD4C6A923AE59748AEA733A2FFEF14F4C4C78C9FE91E`

LangSmith experiment:

`https://eu.smith.langchain.com/o/dec92fa1-b347-483c-8a51-9cd727ee089c/projects/p/08aad335-1979-469e-8865-67edd6afaf96`

Earlier artifacts in the same namespace carry the failures recorded above:
`...-fe434cf-...`, `...-2fe4e32-...`, `...-750472b-...`, `...-7f84378-...`.

## Configuration in force

| Setting | Value |
| --- | --- |
| Target and judge model | `deepseek-v4-flash` |
| Target / judge reasoning effort | `max` / `max` |
| Thinking mode | enabled |
| `llm.max_tokens` | `4096` (unchanged) |
| `agents.critic_review_max_tokens` | `32768` |
| `agents.judge_max_tokens` | `32768` |
| `agents.planner_final_max_tokens` | `4096` |
| `temperature` | `0.7` |
| `retry_count` | `2` — see the correction below |
| Judge structured transport | `deepseek_responses_json_schema_v1` |
| Live repetitions / concurrency | `1` / `1` |
| Live threshold | `0.75` |

### Correction carried forward

`.env` sets `LLM_RETRY_COUNT=2`, and environment overrides win over
`config.yaml`. The dotenv launcher is used for every canary, so `retry_count`
has been `2` for every recorded canary in this campaign, and the `5` quoted in
the earlier canary notes was never in force. The artifact records the true
value in `target_model_configuration.retry_count`.

## Superseded fingerprints

- Judge prompt fingerprint: `93edb1729cbb` -> **`77a0898f4267`**.
- Critic target prompt fingerprint: `242310dce29e` -> **`2c519fadb064`**
  (moved again by the operation-budget change).
- Configuration fingerprint: **`a218a7e14261`**. It moved because the new
  `agents.critic_review_max_tokens` and `agents.judge_max_tokens` values are
  part of the hashed settings, which is how the effective budgets become
  visible in safe configuration metadata with no schema change.
- `judge_configuration_fingerprint` remains **`924caf47aa0d`** — unchanged by
  every fix in this sequence, which is the intended guard that the transport,
  model, effort, temperature, thinking mode, and rubric version never moved.

The candidate commit was `96fe8a9ab65f322d739cb6580c838c92929b828c`. The
artifact reports `git_dirty=true`, which reflects only the preserved,
uninspected `.deepseek-runs/` runtime directory present since before this
session; the tracked and index tree was clean before the run, and the candidate
commit itself changed only `config.yaml`, `src/deep_research/utils/config.py`,
and `tests/test_config.py`.

## Residual quality signal — not a failure

`no_spurious_gaps = 0.0` is the one metric that did not reach `1.0` in the
passing run. This is a **quality observation, not a crash or an infrastructure
failure**: the Critic produced a critique whose listed gaps the evaluator
judged to be already covered by the report. The reference themes for the live
case are stated in the report's own text, so this metric is gradable and the
observation is real. It is recorded for later quality review and does not
justify a prompt or agent repair from one repetition.

## Why the budgets changed, in one paragraph

The global `llm.max_tokens = 4096` was applied uniformly to every call. The two
calls that render large inputs and must return a large structured object — the
Critic's report review and the judge's verdict — do not fit in it at
`reasoning_effort = max`. The repository had already accepted this reasoning
once, for the planner's final plan draft, and the campaign's own budget plan
(`docs/superpowers/plans/2026-09-08-cross-agent-planner-fix-parity.md:1481-1491`)
had already enumerated an operation-specific budget for `critic_report_review`
and every other agent operation; only the planner entry was ever implemented.
This work implements the Critic and judge entries. `llm.max_tokens` itself is
unchanged, ReAct decision calls remain at the global cap, and both new budgets
are configurable through `config.yaml` and
`AGENTS_CRITIC_REVIEW_MAX_TOKENS` / `AGENTS_JUDGE_MAX_TOKENS` so the values can
be tuned from benchmark results.

## Boundary

- `llm.max_tokens` remains `4096`. ReAct decisions and the planner-final call
  were not changed by this sequence.
- The judge's structured transport, model, reasoning effort, temperature,
  thinking mode, and rubric version are unchanged, proven by
  `judge_configuration_fingerprint` remaining `924caf47aa0d`.
- The one-repair structured-output contract, the retry policy, the fallback
  semantics, the frozen cases, the reference data, the metric weights, and the
  thresholds are all unchanged.
- No secret, dotenv value, prompt body, provider response text, or reasoning
  content appears in this note or in any artifact it cites.
- One passing repetition is not universal production sign-off. It is one
  repetition on a frozen case under a frozen configuration.
