# Synthesizer Judge-Transport Canary

Date: 2026-09-11

## Scope

Exactly one Synthesizer live repetition ran after the clean Source Evaluator
canary. The run was sequential and used the frozen configuration, repository
dotenv launcher, and a fresh artifact namespace. No retry, parallel run,
second agent, suite, prompt change, budget change, or provider change was made.

Candidate: `e1b8ae5baa3cfd13cdb11448dd2e74eb7b661b7c` on
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

`output/evaluations/synthesizer/cross-agent-planner-fix-parity-judge-native-schema-synthesizer-canary-e1b8ae5-synthesizer-live-20260911T191351Z-e1b8ae5/results.json`

SHA-256:

`D9C01DE6A6F5B91771FED39F710EEAF674D7BF885DC4694C267FC27239821516`

LangSmith experiment:

`https://eu.smith.langchain.com/o/dec92fa1-b347-483c-8a51-9cd727ee089c/projects/p/166d9a5a-6503-49cf-9751-e23ef0b1d6b9`

## Typed result

| Signal | Result |
| --- | --- |
| Runner exit | `0` |
| Runner status | `REVIEW REQUIRED` |
| Case | `synthesizer-live-report`, version `1` |
| Target hard gates | `15/15` passed |
| Deterministic quality | `0.75` |
| Judge status | `scored` |
| Judge not-run reason | `null` |
| Judge diagnostics | empty |
| Judge quality | `0.84` |
| Aggregate quality | `0.804` |
| Prohibited calls | `0` |
| Target errors | none |
| Fallback provider diagnostic | none |
| Target-side output-limit evidence | none |
| Judge prompt fingerprint | `93edb1729cbb` |
| Judge configuration fingerprint | `924caf47aa0d` |

The deterministic metric map was `coverage=0.0`, `citations_known=1.0`,
`limitations_present=1.0`, `persistence_truthful=1.0`, and `report_present=1.0`.
The resulting deterministic quality is the documented `0.75` level; the
coverage observation is preserved for later quality review and does not alone
justify a prompt or agent repair.

## Boundary and next step

This one-repetition Synthesizer result is a clean judge-transport canary with
stable typed metrics. It is not universal production sign-off from one
repetition. The result is recorded and pushed, and the next agent is Critic.
Its paid command remains behind the campaign's separate immediate-authorization
gate.

## Sol High diagnosis — NO-CHANGE

The typed deterministic metric was `coverage=0.0`. Its exact metric definition
is: `Every subtopic title appears in the report body.` The required context is
present in the real model message input: the characterization uses
`SynthesizerAgent.build_task(live_case.state)` and confirms that every live
subtopic title is present in the rendered `report_messages()` input, with the
task findings bound to the state's raw findings.

The offline characterization passed, including the frozen live-case check that
the non-empty set of declared subtopic titles equals the non-empty set of
`finding.related_sub_topic` values. The safe artifact cannot identify whether
the observed zero is lexical coverage (literal title matching) or semantic
coverage (the report addressing the topic without the literal title).

The judge was clean and scored (`judge.status=scored`, `judge_quality=0.84`,
and empty judge diagnostics). Sol High's final ruling is **NO-CHANGE**: the
coverage observation is real, but the required context is present and no
production defect is demonstrated.
