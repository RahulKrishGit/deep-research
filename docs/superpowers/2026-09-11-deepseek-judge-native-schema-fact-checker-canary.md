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
