# Critic Live-Call Production Readiness — Design

Date: 2026-09-11

Branch: `codex/cross-agent-planner-fix-parity`

Spec: `docs/superpowers/specs/2026-09-11-critic-live-call-production-readiness-design.md`

Plan: `docs/superpowers/plans/2026-09-11-critic-live-call-production-readiness.md`

## Goal

Make the Critic agent production ready: a live Critic run must either produce a
grounded critique or fail in a way that is typed, diagnosable, and correctly
scored. No live Critic failure may present as a clean, schema-valid run.

## Background: the observed failure

The judge-transport Critic canary at
`2e8b25f5988ae222b74aee4d5272e5590cae2644` failed its live quality gate. The
artifact is
`output/evaluations/critic/cross-agent-planner-fix-parity-judge-native-schema-critic-canary-2e8b25f-critic-live-20260911T191641Z-2e8b25f/results.json`.

| Signal | Value |
| --- | --- |
| Runner status | `FAILED` |
| Target hard gates | `14/14` passed |
| Deterministic quality | `0.80` |
| Deterministic metrics | `score_bounded=1.0`, `route_consistent=1.0`, `rationale_present=0.0`, `no_spurious_gaps=1.0` |
| Judge status | `scored`, no diagnostics |
| Judge quality | `0.2525` |
| Aggregate quality | `0.4715` (live threshold `0.75`) |
| Fallback provider diagnostic | `{kind: schema_output, operation: critic_report_review}` |
| ReAct stop reason | `provider_error` |
| Prohibited calls | `0` |
| Target errors | none |

The judge's own rationale records the target-side cause: "The critic run ended
in a provider `StructuredOutputError` (two `json_invalid` attempts), and every
substantive field is an empty fallback: gaps `[]`, unsupported_claims `[]`,
recommended_queries `[]`, score `1`, should_continue `false`."

Three prior diagnoses of this artifact concluded NO-CHANGE. Those rulings were
reached on the evidence available in the artifact. This design shows that the
artifact was missing the decisive evidence, and that the Critic does carry a
real defect.

## Root cause

### Confirmed defect 1: the Critic never sees the report it is asked to spot-check

The Critic's ReAct spot-check loop builds its prompt through
`render_react_messages` (`src/deep_research/agents/prompts.py:237-267`), which
renders only `task.instruction` and `task.guidance`. `CritiqueTask` declares a
`report` field (`src/deep_research/agents/critic.py:115`) that
`CriticAgent.build_task` populates (`critic.py:471`), but
`_render_spot_check_guidance` (`critic.py:124-134`) emits only sub-topic titles
and planned queries. The report therefore never reaches the spot-check prompt.

Measured against the registered live case (`critic-live-review`):

| Rendered prompt | Length | Contains report text |
| --- | --- | --- |
| ReAct spot-check prompt | 1,199 chars | No |
| Structured review prompt | 4,811 chars | Yes |
| The report itself | 2,877 chars | — |

The spot-check prompt's only sections are `Task`, `Guidance`, `Tools`, `Notes so
far`, `Budget`, and `Response contract`. The model is instructed to spot-check a
report it has never been shown, so it cannot name a suspected gap or figure. In
the failing run this is exactly what the judge observed: "the agent first tried
`query_memory` (empty matches) and concluded no report text was in context."

This is the Critic's defect and not the project's design, because the other two
agents that run a ReAct loop both name their working artifact in the loop
prompt:

- `ResearcherAgent.sub_topic_task` (`researcher.py:694-717`) renders
  `render_sub_topic_guidance(sub_topic, existing_sources)` and instructs
  `Gather evidence for the sub-topic "<title>"`.
- `FactCheckerAgent.claim_task` (`fact_checker.py:656-683`) instructs
  `Verify this claim against independent sources: "<claim text>"` and renders
  the claim's own source URLs.

The Critic is the only ReAct agent whose loop prompt omits the object it reasons
about.

### Confirmed defect 2: the artifact discards the structured-output diagnostics

`_fallback_provider_diagnostic` (`src/deep_research/evaluation/runner.py:534-552`)
projects a `ProviderFailureSnapshot` into `FallbackProviderDiagnostic`
(`src/deep_research/evaluation/models.py:263-271`), which declares only `kind`
and `operation`. The snapshot's bounded `diagnostics` —
`StructuredValidationDiagnostic{attempt, field_paths, category}` from
`providers/contracts.py:95-116` — are dropped.

Those dropped fields are precisely what distinguishes the possible causes of a
`json_invalid` result: a truncated completion, an empty completion, and
preamble text around otherwise valid JSON are not separable without the
per-attempt `attempt` and `category` values, and no cause can be confirmed
without the completion's `usage` and `configured_max_tokens`.

The same repository already retains this exact data safely on the judge path:
`JudgeFeedback.diagnostics` (`models.py:518-520`) is a tuple of
`EvaluatorDiagnostic` (`models.py:329-351`), which carries `kind`, `attempt`,
`category`, and `field_paths`. The Fact Checker canary artifact records judge
diagnostics of this shape (`attempt 1, field "rationale"`). The target/fallback
path is the only one that discards it.

This defect is shared: every agent that can take a typed provider fallback is
affected, not the Critic alone.

### Confirmed defect 3: a degraded run is scored as a bad review

`critique_actionable_passes` (`evaluators.py:1328-1344`) returns `True`
unconditionally when `should_continue` is not `True`. Because
`fallback_critique` (`critic.py:228-274`) returns `should_continue=False`, the
gate passes on a wholly empty critique. Combined with `score_bounded=1.0` and
`route_consistent=1.0` on the fallback's clamped score of `1`, a degraded run
presents as `14/14` hard gates passed.

The judge has no way to tell an honest fallback from a real judgement. It scored
the fallback's empty `gaps`, `unsupported_claims`, and `recommended_queries` as
a critique that "delivers nothing", scoring `score_groundedness=0.1` and
`scoring_calibration=0.05`. Every one of those dimensions is measuring the
absence of content the fallback was never able to produce.

The judge identified the correct contract itself: "An ideal run would have
either retried, surfaced the report-not-in-context condition as an explicit
recoverable failure, or produced a grounded review of the fixed report."

## Non-goals

No change is made to any of the following. Each would be a guess at a cause that
defect 2 currently makes unobservable, and each is explicitly deferred until a
diagnosable canary exists:

- `llm.max_tokens` (stays `4096`) or any per-call token override.
- The retry policy (`retry_count=5`, `retry_initial_delay=1.0`,
  `retry_max_delay=16.0`).
- The one-repair structured-output contract in
  `DeepSeekChatProvider.complete_structured` (`deepseek_provider.py:739-819`).
- `temperature`, `thinking_mode`, or `reasoning_effort`.
- The Critic acceptance score (`ACCEPTANCE_SCORE = 7`) or the routing precedence
  in `route_decision` (`critic.py:158-185`).
- The live quality threshold (`live_threshold = 0.75`) or any frozen case,
  rubric, reference theme, or metric weight.
- The judge's native transport (`deepseek_responses_json_schema_v1`) and the
  target/judge adapter separation (`providers/factory.py:31-74`).

## Design

### Fix 1 — the Critic's spot-check loop sees the report

`CritiqueTask.guidance` gains the report, rendered through the existing
`_clamp_report` helper with the same `CRITIC_REPORT_CHARS` budget the review
prompt uses. Reusing that helper keeps one clamping rule for both prompts: a
long report is truncated identically in each, and the spot-check view can never
be larger than the review view.

The report reaches two separate provider calls — the ReAct loop and the review —
so the review prompt is unchanged and pays nothing. `CriticAgent.run`'s
`has_report and self.config.tool_budget > 0` gate (`critic.py:613`) is
unchanged: with no report the loop is still skipped and `missing_report`
semantics still apply.

`CRITIC_SYSTEM_PROMPT` gains one sentence stating that the report under review
is in front of the model and must not be re-fetched with a tool. Without it, the
model's first move can still be a memory query for text it has already been
given.

`_render_spot_check_guidance` keeps its current planned-query content and gains
the report above it, so the existing guidance contract is extended rather than
replaced.

### Fix 2 — retain the bounded structured-output diagnostics

`FallbackProviderDiagnostic` gains the bounded diagnostics it currently drops:
the per-attempt `attempt`, `category`, and `field_paths`, reusing the existing
`EvaluatorDiagnostic` shape that the judge path already stores.

`_fallback_provider_diagnostic` populates them from the snapshot's
`diagnostics`, translating `StructuredValidationDiagnostic` to
`EvaluatorDiagnostic`. Both models already normalize field paths to schema-proven
names (`_normalize_field_path` in `providers/contracts.py:52-65` and
`_normalize_diagnostic_path` in `models.py:317-326`), so no raw provider text
can enter the artifact through this field.

The bounded telemetry that defect 2 also needs in order to separate truncation
from malformed text — `configured_max_tokens`, `usage`, and the finish-reason
category — is added to the same projection. `ProviderFailureSnapshot` already
carries `configured_max_tokens`, `usage`, `request_attempt`, and
`structured_attempt` for the `output_limit` kind (`providers/contracts.py:209-242`);
they are surfaced onto the fallback projection so a schema failure is as
diagnosable as an output-limit failure already is. The finish-reason category is
not currently retained on any snapshot and is added to
`ProviderFailureSnapshot` for that reason alone.

Every added field is optional, because a snapshot that carries none of this
telemetry must still round-trip. This is a schema change, so `_MAX_ARTIFACT_*`
bounds and the existing artifact-shape tests are updated in the same commit, and
the change is purely additive: no existing field is removed, renamed, or made
newly required.

### Fix 3 — a degraded run is a legible fact, not a silent zero

The typed fallback becomes a first-class field of the judge's closed contract
`JudgeInput` (`judging.py:96-118`), populated from the same
`_fallback_provider_diagnostic` projection the runner already computes. The
judge prompt template gains one block explaining that a run carrying a provider
fallback has no model review to score, so the rubric is applied to the
fallback's honest disclosure rather than to content that was never produced.

Three consequences are handled explicitly:

1. `_BLOCK_ORDER` (`judging.py:223-239`) gains the new key, because the judge
   prompt renders blocks in that fixed order.
2. `judge_prompt_fingerprint` (`judging.py:129-140`) changes, because the
   template changed. The current stable value `93edb1729cbb` is superseded. This
   is intentional and must be recorded in the canary note, since it makes judge
   scores from before and after this change non-comparable.
3. `judge_configuration_fingerprint` (`924caf47aa0d`) is asserted by existing
   tests and must be re-pinned to its new value in the same commit that changes
   the template, never in a later one.

The deterministic layer is unchanged by Fix 3. `rationale_present=0.0` remains
the documented deterministic signature of a fallback and is already pinned by
`test_rationale_metric_distinguishes_grounded_review_from_fallback`
(`tests/test_evaluation/test_cases_critic.py:245-312`).

## Testing

Offline, deterministic, no network and no provider calls:

- **Fix 1 RED→GREEN.** A test asserting the rendered spot-check prompt for the
  registered `critic-live-review` case contains a distinctive sentence from
  `_LIVE_REPORT` fails before the change and passes after. A second test asserts
  the same prompt still contains the planned sub-topic titles and queries, so
  the existing guidance contract is proven preserved rather than replaced.
- **Fix 1 bounds.** A test asserting a report longer than `CRITIC_REPORT_CHARS`
  is clamped in the spot-check prompt by the same rule the review prompt uses.
- **Fix 1 missing report.** A test asserting no report is rendered and no
  provider call is made when `state.report` is empty.
- **Fix 2 round-trip.** A test building a `TargetOutput` carrying a
  `critic_review_provider_error` whose snapshot holds two diagnostics with
  `attempt`, `category`, and `field_paths`, asserting every value survives into
  the artifact projection. A companion test asserts a snapshot with no
  diagnostics still round-trips as `None`-safe empty.
- **Fix 2 no content leak.** A test asserting a diagnostic carrying an
  un-normalized field path is projected as `$`, so no provider text can reach
  the artifact through this field.
- **Fix 3 contract.** A test asserting `JudgeInput` carries the fallback fact
  for a fallback output and omits it for a healthy one, and that
  `_BLOCK_ORDER` contains every `JudgeInput` key, so a future field cannot be
  added without being rendered.
- **Full gate.** `pytest` across the repository with no deselections beyond the
  documented baseline, plus `ruff check` and `git diff --check`.

Then, and only then, one paid live Critic repetition on the frozen
configuration, sequentially, recorded with its artifact SHA-256.

## Success criteria

1. The Critic's spot-check prompt contains the report under review for every
   case that has one, and the fix is pinned by a test that fails without it.
2. A typed provider fallback in any agent's artifact carries bounded per-attempt
   diagnostics, so a `json_invalid` failure is attributable to a cause without
   repeating the paid call.
3. A degraded Critic run is legible to the judge as a degraded run, and its
   quality score reflects the absence of a review rather than the content of
   one.
4. The full offline suite, `ruff`, and `git diff --check` pass.
5. One live Critic repetition either meets the `0.75` live threshold or fails
   with diagnostics sufficient to name the provider-side cause.

## Decision log

| Decision | Rationale |
| --- | --- |
| Put the report in `CritiqueTask.guidance` rather than a new prompt section | Keeps `render_react_messages` shared and unchanged for the Researcher and Fact Checker, and follows the two agents that already name their artifact in `guidance`. |
| Reuse `_clamp_report` and `CRITIC_REPORT_CHARS` | One clamping rule for both Critic prompts; the spot-check prompt cannot exceed the review prompt. |
| Keep the typed fallback rather than halting or retrying | The fallback is correct behaviour — an outage says nothing about the report and must not buy a research cycle. It was the *observability* and *scoring* of the fallback that were wrong. |
| Retain diagnostics instead of adding raw response capture | Field paths, attempt numbers, categories, usage, and token caps are already normalized and provider-content-free. Raw text would introduce a leak surface the project's artifact redaction deliberately avoids. |
| Add the fallback fact to `JudgeInput` rather than to `gate_results` | `gate_results` are pass/fail observations; the fallback is context the judge needs in order to apply its rubric correctly. |
| Fix evidence retention for all agents, not only the Critic | The defect is in one shared projection. A Critic-only patch would leave the identical defect in every other fallback path and re-open the Fact Checker "UNSCORABLE" follow-up. |
| Defer all token, retry, and prompt-budget tuning | No cause can be confirmed from the current artifact, and defect 2 makes it confirmable. Tuning before then is guessing, which is what the three prior NO-CHANGE rulings correctly refused. |
