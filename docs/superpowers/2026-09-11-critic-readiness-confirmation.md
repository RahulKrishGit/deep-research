# Critic Readiness — Confirmation Repetitions

Date: 2026-09-11

Candidate: `6b2c9c3` on `codex/cross-agent-planner-fix-parity`, carrying the
`96fe8a9` configuration (`agents.critic_review_max_tokens = 32768`,
`agents.judge_max_tokens = 32768`).

Three independent live repetitions ran sequentially, each in a fresh namespace,
with no retry, no second agent, and no suite. Every run used the repository
dotenv launcher and the frozen configuration.

## Results

| Run | Deterministic | Judge | Aggregate | Case passed | Target fallback |
| --- | --- | --- | --- | --- | --- |
| r1 | `1.00` | `0.80` | **`0.88`** | yes | none |
| r2 | `0.80` | `0.745` | **`0.767`** | **yes** | **`schema_output` / `critic_report_review`** |
| r3 | `0.75` | `0.69` | **`0.7125`** | no | none |

All three: `14/14` hard gates passed, `0` prohibited calls, judge `scored` with
no judge diagnostics, no target errors.

Artifacts (each SHA-256):

- r1 `output/evaluations/live-critic-confirm/critic/critic-confirm-96fe8a9-r1-critic-live-20260911T224609Z-6b2c9c3/results.json` — `D949E53B0253B2B0933CF6EE4E66335A2CACCCA2E641024197B574BA6F24EFF6`
- r2 `output/evaluations/live-critic-confirm/critic/critic-confirm-96fe8a9-r2-critic-live-20260911T224752Z-6b2c9c3/results.json` — `55C0F669302F46175A48255801874ADC7201B0A99A395193483E2753969D65F0`
- r3 `output/evaluations/live-critic-confirm/critic/critic-confirm-96fe8a9-r3-critic-live-20260911T224859Z-6b2c9c3/results.json` — `468312B3E9717DC5098E52CE0A5308E4BE851712B6555B0A2965E37C5067BC59`

## Finding 1 — the intermittent structured-review failure is NOT fixed

r2 recorded a typed target fallback:

```
kind       = schema_output
operation  = critic_report_review
attempt 1  = json_invalid, field_paths ("$",)
attempt 2  = json_invalid, field_paths ("$",)
```

This is the original failure mode, at the enlarged `32768` budget. The
`json_invalid` category at `$` is reachable only when the provider returns
non-empty text that is not parseable JSON, and a `length` finish reason would
have raised `ProviderOutputLimitError` before validation instead. So the cause
is **not** an exhausted budget, and raising it further will not fix it.

Failure rate across this campaign, same case and configuration family:

- before any fix: `4 of 5` live repetitions failed
- after the transport, context, and budget fixes: `1 of 3`

The fixes made the failure much rarer without eliminating it. The Critic is
therefore **not yet reliable**: roughly one line in four produced no review.

## Finding 2 — the live gate can pass a run that produced no review

r2 produced no critique: `rationale_present = 0.0`, `react_stop_reason =
provider_error`, and the fallback's placeholder `score = 1` with empty gap,
claim, and query lists. Its aggregate was `0.767` and the case **passed**.

The arithmetic that allows this: `aggregate_quality = 0.6 * judge + 0.4 *
deterministic` = `0.6 * 0.745 + 0.4 * 0.80`. The judge's `0.745` is a
*favourable* assessment of the fallback, and it is favourable because of the
judge-legibility work in this campaign: `JUDGE_SYSTEM_PROMPT` and
`JUDGE_PROMPT_TEMPLATE` now instruct the judge that "a run carrying that block
has no model review to score" and that it must "not penalise it for the gaps,
unsupported claims, or recommended queries that a review would have contained."
The judge followed that instruction exactly and scored the fallback on honesty.

The judge behaved correctly. The defect is in the **gate**: a quality threshold
must not be reachable by a run that produced no review at all. This is the same
class of defect as the original `critique_actionable` gate, which returned
`True` unconditionally when `should_continue` was not `True`.

Diagnostic quality and gate strictness are separate concerns, and the second
fix made the first better while making the second worse. Both are needed.

## Finding 3 — real-review quality is marginal and variable

r1 is a genuinely good run: every deterministic metric `1.0`, agent-specific
`gap_precision = 0.95` and `critique_actionability = 0.95`, aggregate `0.88`.

r3 is a real review of ordinary quality: `no_spurious_gaps = 0.0` (the Critic
listed gaps the evaluator judged already covered), `groundedness = 0.58`,
`uncertainty_calibration = 0.55`, aggregate `0.7125`.

On this frozen case the reporting report declares its own themes, so
`no_spurious_gaps` is a gradable signal, and r3's `0.0` is a real quality
observation about the critique rather than an infrastructure artifact.

## Gate fix and the gated confirmation

The no-review-passing defect in Finding 2 is fixed by `cccc139`, which adds a
`review_produced` hard gate to the Critic: a repetition in which the report
review fell back to the provider-unavailable path cannot pass, because the
fallback's placeholder score of `1` with empty lists satisfies every other
gate. The gate is keyed to the `critic_report_review` operation specifically,
so a fallback elsewhere does not over-block a run whose review succeeded.

Three further live repetitions ran on `cccc139`:

| Run | Deterministic | Judge | Aggregate | Gates | Fallback | `review_produced` |
| --- | --- | --- | --- | --- | --- | --- |
| r1 | `0.75` | `0.8175` | `0.7905` | `15/15` | none | pass |
| r2 | `0.80` | `0.8265` | `0.8159` | `15/15` | none | pass |
| r3 | `1.00` | `0.75` | `0.85` | `15/15` | `output_limit` / `react_decision` | pass |

All three passed. Scores are both higher and tighter than the ungated set
(`0.79`–`0.85` versus `0.71`–`0.88`), and no repetition in this set suffered a
`critic_report_review` fallback.

Artifacts (each SHA-256):

- r1 `output/evaluations/live-critic-gated/critic/critic-gated-cccc139-r1-critic-live-20260911T225259Z-cccc139/results.json` — `88E3D544D54CC6036E663587753A23B866B2A25096F9BA2C2EB2AEDDB3D4660C`
- r2 `output/evaluations/live-critic-gated/critic/critic-gated-cccc139-r2-critic-live-20260911T225435Z-cccc139/results.json` — `A81B6C438BC1925FB17A02D0CD7C49FF980C58E07D6ED965D788C8AA1C1527A5`
- r3 `output/evaluations/live-critic-gated/critic/critic-gated-cccc139-r3-critic-live-20260911T225625Z-cccc139/results.json` — `F2B3083691192AA0B82A80B53944FA6B700FC4DFEB1A888E7845881A01C3551C`

### Finding 4 — ReAct decisions truncate at the global cap

r3 recorded `{kind: output_limit, operation: react_decision}`: the Critic's
ReAct spot-check decision hit the global `llm.max_tokens = 4096` and was
truncated. The review itself still succeeded, which is why the run passed and
why `review_produced` correctly stayed `True`.

This is the same budget defect as the review and the verdict, in the one place
still pinned to the global cap. `llm.max_tokens = 4096` is therefore too small
for the Critic's ReAct decisions as well, and the truncation silently degrades
the spot-check phase rather than failing the run.

## Status after the gate fix

- **Reliability of the review path**: no `critic_report_review` fallback in the
  last three repetitions, against `1 of 3` immediately before. The intermittent
  structured-output failure is not proven gone — three repetitions is a small
  sample — but it is no longer observed, and when it does occur it can no longer
  be certified as a pass.
- **Gate integrity**: repaired. A run with no review now fails a hard gate
  regardless of how favourably the judge reads the fallback.
- **Remaining known defect**: `react_decision` truncation at the global cap,
  recorded as Finding 4 and not yet repaired.

## Boundary

No configuration changed during either confirmation set. The retry policy
(`retry_count = 2`, set by `.env`), the one-repair contract, the fallback
semantics, the frozen case, the rubric, the metric weights, `llm.max_tokens =
4096`, and the `0.75` threshold are all unchanged. `judge_configuration_
fingerprint` remained `924caf47aa0d` in every run.

The only production change between the two sets is the `review_produced` gate in
`cccc139`. No token, retry, temperature, or threshold value was touched, and no
repair to the intermittent structured-output failure is proposed by this note;
Finding 4's `react_decision` truncation is recorded, not repaired.

These results exist so that the next change is chosen from evidence rather than
from the single passing repetition recorded in
`docs/superpowers/2026-09-11-critic-readiness-final-canary.md`, which — read
alone — would have overstated reliability.
