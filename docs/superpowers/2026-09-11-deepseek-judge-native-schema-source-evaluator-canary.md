# Source Evaluator Judge-Transport Canary

Date: 2026-09-11

## Scope

Exactly one Source Evaluator live repetition ran after the successful
Researcher judge-transport canary. The run was sequential and used the frozen
configuration, the repository dotenv launcher, and a fresh artifact namespace.
No retry, parallel run, second agent, suite, prompt change, budget change, or
provider change was made.

Candidate: `d902c579597d550f81b460982d614dbb0870b7d9` on
`codex/cross-agent-planner-fix-parity`.

## Frozen configuration

- Target and judge model: `deepseek-v4-flash`
- Target reasoning effort: `high`
- Judge reasoning effort: `max`
- Live repetitions: `1`; concurrency: `1`
- `llm.max_tokens=4096`; Planner final cap `4096`
- Retry count `5`; initial delay `1.0`; maximum delay `16.0`
- Native judge transport: `deepseek_responses_json_schema_v1`

No credential or dotenv value was recorded.

## Evidence

Artifact:

`output/evaluations/source-evaluator/cross-agent-planner-fix-parity-judge-native-schema-source-evaluator-canary-d902c57-source-evaluator-live-20260911T190947Z-d902c57/results.json`

SHA-256:

`F6A910D4F428F43B4FFC9164C1B989C4BE8B529D57C0D2FA7A7ADB909C3CEEF2`

LangSmith experiment:

`https://eu.smith.langchain.com/o/dec92fa1-b347-483c-8a51-9cd727ee089c/projects/p/6d92638c-900d-4ac1-a184-2fd3e0c2d464`

## Typed result

| Signal | Result |
| --- | --- |
| Runner exit | `0` |
| Runner status | `REVIEW REQUIRED` |
| Case | `source-evaluator-live-ranking`, version `2` |
| Target hard gates | `14/14` passed |
| Deterministic quality | `1.00` |
| Judge status | `scored` |
| Judge not-run reason | `null` |
| Judge diagnostics | empty |
| Judge quality | `0.88` |
| Aggregate quality | `0.928` |
| Prohibited calls | `0` |
| Target errors | none |
| Fallback provider diagnostic | none |
| Target-side output-limit evidence | none |
| Judge prompt fingerprint | `93edb1729cbb` |
| Judge configuration fingerprint | `924caf47aa0d` |

The unchanged judge prompt fingerprint and reviewed native-schema configuration
fingerprint were preserved. The typed artifact validates without a fabricated
score or judge diagnostic.

## Boundary and next step

This one-repetition Source Evaluator result is a clean judge-transport canary;
it supplies no target defect requiring repair. It is not universal production
sign-off from one repetition. The result is recorded and pushed, and the next
agent in the approved sequence is Synthesizer. Its paid command remains behind
the campaign's separate immediate-authorization gate.
