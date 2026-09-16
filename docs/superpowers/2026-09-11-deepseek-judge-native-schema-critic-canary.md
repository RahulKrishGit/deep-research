# Critic Judge-Transport Canary

Date: 2026-09-11

## Scope

Exactly one Critic live repetition ran after the clean Synthesizer canary. The
run was sequential and used the frozen configuration, repository dotenv
launcher, and a fresh artifact namespace. No retry, parallel run, second agent,
suite, prompt change, budget change, or provider change was made.

Candidate: `2e8b25f5988ae222b74aee4d5272e5590cae2644` on
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

`output/evaluations/critic/cross-agent-planner-fix-parity-judge-native-schema-critic-canary-2e8b25f-critic-live-20260911T191641Z-2e8b25f/results.json`

SHA-256:

`7E441C4DAAD0DDE3B501A1CEE137D8ADA1611524BE9C79A74C3E254ACBE96E0C`

LangSmith experiment:

`https://eu.smith.langchain.com/o/dec92fa1-b347-483c-8a51-9cd727ee089c/projects/p/c53343aa-6fb3-4918-be14-3e377c428b07`

## Typed result

| Signal | Result |
| --- | --- |
| Runner exit | `1` |
| Runner status | `FAILED` |
| Case | `critic-live-review`, version `1` |
| Target hard gates | `14/14` passed |
| Deterministic quality | `0.80` |
| Judge status | `scored` |
| Judge not-run reason | `null` |
| Judge diagnostics | empty |
| Judge quality | `0.2525` |
| Aggregate quality | `0.4715` |
| Prohibited calls | `0` |
| Target errors | none |
| Fallback provider kind | `schema_output` |
| Fallback provider operation | `critic_report_review` |
| ReAct stop reason | `provider_error` |
| Target-side output-limit evidence | none |
| Judge prompt fingerprint | `93edb1729cbb` |
| Judge configuration fingerprint | `924caf47aa0d` |

The deterministic metric map was `no_spurious_gaps=1.0`,
`rationale_present=0.0`, `route_consistent=1.0`, and `score_bounded=1.0`.
All hard gates passed, but the typed Critic report-review fallback and low
scored quality caused the case to fail its quality threshold.

## Diagnosis boundary and next step

The judge transport is not the failing boundary here: the judge was scored and
had no diagnostics. The current evidence instead exposes a target-side Critic
`critic_report_review` structured-output fallback. This is provisional
target/provider evidence, not permission to tune the Critic prompt or budget.
The fallback is not an `output_limit`, and no target budget amendment is
authorized.

The sequential loop stops here. The next action is a task-scoped diagnosis in
the existing Sol High browser conversation. Only a concrete offline RED and a
reviewed minimal repair or explicit no-change ruling can unblock Fact Checker.

## Sol High diagnosis

Sol High classified this as the expected Critic target/provider structured-output
fallback and issued a **NO-CHANGE** ruling. The target-side request exhausted
the existing initial structured attempt plus one repair on the normal DeepSeek
Chat path, then Critic intentionally returned its `provider_unavailable`
fallback. The typed `{kind: schema_output, operation: critic_report_review}`
projection and `provider_error` stop reason are the designed evidence for that
path.

The empty compact repetition error list is not a lost error: the evaluator
keeps a completed fallback result and agent-level typed error separate from a
top-level `TargetOutput.failure`. The live judge was independently scored with
no diagnostics, so its low score evaluated the fallback critique rather than
causing the fallback.

Sol found no deterministic RED proving a Critic context, contract, routing,
serialization, or evaluator defect. The existing Critic and evaluator tests
already characterize the planned spot-check context, provider fallback stop,
typed operation, and fallback `rationale_present=0.0` versus grounded
`rationale_present=1.0` behavior. No prompt, token, retry, schema, threshold,
or production code change is justified.

Fact Checker remains deferred until this no-change ruling is recorded. It is
the next sequential candidate and still requires separate authorization for its
paid live repetition.
