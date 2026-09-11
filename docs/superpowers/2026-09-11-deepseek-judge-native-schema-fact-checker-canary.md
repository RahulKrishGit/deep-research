# Fact Checker Judge-Transport Canary

Date: 2026-09-11

## Scope

Exactly one Fact Checker live repetition ran after the Critic no-change ruling.
The run was sequential and used the frozen configuration, repository dotenv
launcher, and a fresh artifact namespace. No retry, parallel run, second agent,
suite, prompt change, budget change, or provider change was made.

Candidate: `a503a7bca8203f5539c9cc0cb61117f741698bf0` on
`codex/cross-agent-planner-fix-parity`.

## Frozen configuration

- Target and judge model: `deepseek-v4-flash`
- Target reasoning effort: `max`
- Judge reasoning effort: `max`
- Live repetitions: `1`; concurrency: `1`
- `llm.max_tokens=4096`; Planner final cap `4096`
- Retry count `5`; initial delay `1.0`; maximum delay `16.0`
- Native judge transport: `deepseek_responses_json_schema_v1`

No credential or dotenv value was recorded.

## Evidence

Artifact:

`output/evaluations/fact-checker/cross-agent-planner-fix-parity-judge-native-schema-fact-checker-canary-a503a7b-fact-checker-live-20260911T193525Z-a503a7b/results.json`

SHA-256:

`53D77D8D3722DAD4F5F203F69E986D9C1C23D83DC404C791A0A0FFD67F38A20`

LangSmith experiment:

`https://eu.smith.langchain.com/o/dec92fa1-b347-483c-8a51-9cd727ee089c/projects/p/73585ecd-8099-4d44-8bec-e20dbd126b67`

## Typed result

| Signal | Result |
| --- | --- |
| Runner exit | `3` |
| Runner status | `INFRASTRUCTURE FAILURE` |
| Case | `fact-checker-live-verification`, version `1` |
| Target hard gates | `15/15` passed |
| Deterministic quality | `1.00` |
| Judge status | `judge_not_run` |
| Judge not-run reason | `judge_schema_failure` |
| Judge diagnostics | `schema_output`, attempt 1, field `rationale`; attempt 2, field `rationale` |
| Judge quality | unavailable |
| Aggregate quality | unavailable |
| Prohibited calls | `0` |
| Target errors | none |
| Target fallback provider kind | `output_limit` |
| Target fallback operation | `react_decision` |
| ReAct stop reason | `provider_error` |
| Judge prompt fingerprint | `93edb1729cbb` |
| Judge configuration fingerprint | `924caf47aa0d` |

The target fallback diagnostic is intentionally separate from the judge
failure. It is not top-level target failure evidence and does not justify a
Fact Checker token-budget change. Because the judge was unscorable, this run
does not establish Fact Checker quality.

## Boundary and next step

The native-schema judge can score several agents, but this repetition exposed a
typed judge-side schema failure at `rationale` on both allowed attempts. Do not
retry or fabricate a score. The exact field paths and attempts are preserved
for Sol High diagnosis; no Fact Checker production repair or budget change is
justified from this artifact alone.

## Sol High diagnosis

Sol High reviewed the pushed artifact at remote HEAD
`eaa572a29c8106edfcf41f1c9328a1fa68d79830` in the existing browser
conversation. The ruling is **provider/native-schema response conformance
failure at the judge boundary; insufficient evidence for a repository change**.

The typed evidence supports only that two completed native-schema judge
responses reached local `JudgeVerdict` validation and were rejected at
`rationale`. `src/deep_research/providers/deepseek_provider.py:825-922`
performs the Responses normalization, output extraction, and local validation;
the status mapping is at `deepseek_provider.py:367-381`, schema request at
`deepseek_provider.py:846-861`, and the local `rationale` bounds are at
`src/deep_research/evaluation/models.py:476-481`. The field path does not say
whether the value was missing, wrong-typed, out of bounds, or failed another
validation rule, so a guessed raw response would invent evidence.

The judge/evaluator contract is behaving correctly. The typed failure mapping
is in `src/deep_research/evaluation/judging.py:288-433` and
`src/deep_research/evaluation/failure_taxonomy.py:45-157`; the runner keeps
judge and aggregate quality unavailable and classifies the unscorable case as
infrastructure failure (`src/deep_research/evaluation/runner.py:489-525` and
`runner.py:640-704`). No score should be fabricated.

The repeated `rationale` path does **not** justify a production RED. Existing
coverage already exercises the native schema, local validation, exactly one
repair, and typed exhaustion (`tests/test_deepseek_provider.py:347-597` and
`tests/test_evaluation/test_judging.py:220-332`). An optional offline
characterization may inject the typed `StructuredOutputError` boundary with
two `("rationale",)` diagnostics and no invented categories, but it should
pass and must not change production behavior.

The Fact Checker target also requires no repair and no token increase. Its
`{kind=output_limit, operation=react_decision}` is fallback telemetry, not
target failure evidence; the conservative path is covered by
`src/deep_research/agents/react.py:69-281`,
`src/deep_research/agents/fact_checker.py:799-849`, and
`tests/test_agents/test_fact_checker.py:976-1045`. Keep the target/judge
adapter separation in `src/deep_research/providers/factory.py:31-74`.

### Final disposition

**NO-CHANGE. Fact Checker quality remains UNSCORABLE.** Do not retry this
paid repetition, loosen the judge schema, add retries, broaden prompts, or
increase a token budget. Preserve `llm.max_tokens=4096`, the existing retry
policy, exactly one structured repair, scoreless judge failures, native-schema
transport/fingerprints, frozen cases and rubric data, and the typed fallback
taxonomy. Reopen only if new safe evidence demonstrates a concrete
contract, extraction, repair, diagnostic-projection, or score-fabrication
defect. Sol High performed read-only diagnosis only and did not execute tests,
provider calls, LangSmith calls, live evaluation, or a suite run.
