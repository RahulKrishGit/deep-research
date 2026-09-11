# Researcher Judge-Transport Canary

Date: 2026-09-11

## Scope

The user authorized exactly one paid live repetition after the Task 8 native
DeepSeek judge transport received a Sol High PASS. The run used the campaign
worktree on `codex/cross-agent-planner-fix-parity`, the repository dotenv
launcher, the frozen evaluation configuration, and a fresh artifact namespace.
No retry, second agent, suite, prompt change, budget change, or provider change
was made.

The executable code at candidate
`c13dd9d2701bf7491522ad4ac88c86be08800438` is identical to the reviewed code
at `e914c13030e49ca0678f6d2cd6ee2ab76ee57228`; the intervening tracked change
was documentation only.

## Frozen configuration

- Target and judge model: `deepseek-v4-flash`
- Target reasoning effort: `high`
- Judge reasoning effort: `max`
- Live repetitions: `1`; concurrency: `1`
- `llm.max_tokens=4096`; Planner final cap `4096`
- Retry count `5`; initial delay `1.0`; maximum delay `16.0`
- Native judge transport: `deepseek_responses_json_schema_v1`

Only typed configuration and presence metadata were retained. No credential or
dotenv value was recorded.

## Evidence

Artifact:

`output/evaluations/researcher/cross-agent-planner-fix-parity-judge-native-schema-researcher-canary-c13dd9d-researcher-live-20260911T185526Z-c13dd9d/results.json`

SHA-256:

`DD5C68C045C12B5388EF59959DD6818BED0C3C4109C741B0093C90F7E4DA2FB5`

LangSmith experiment:

`https://eu.smith.langchain.com/o/dec92fa1-b347-483c-8a51-9cd727ee089c/projects/p/f4dbc66f-7486-4181-9220-432e3468e71a`

## Typed result

| Signal | Result |
| --- | --- |
| Runner exit | `0` |
| Runner status | `REVIEW REQUIRED` |
| Target hard gates | `14/14` passed |
| Deterministic quality | `1.00` |
| Judge status | `scored` |
| Judge not-run reason | `null` |
| Judge diagnostics | empty |
| Judge quality | `0.6825` |
| Aggregate quality | `0.8095` |
| Prohibited calls | `0` |
| Target errors | none |
| Fallback provider diagnostic | none |
| Target-side output-limit evidence | none |
| Judge prompt fingerprint | `93edb1729cbb` |
| Judge configuration fingerprint | `924caf47aa0d` |

The judge fingerprint matches the pre-transport prompt fingerprint and the
reviewed native-schema configuration fingerprint. The `JudgeVerdict` validated
without a fabricated score or a repair-visible diagnostic.

## Boundary and next step

This is a successful judge-transport canary and evidence that the prior shared
judge structured-output boundary is resolved for Researcher. It is not a
universal production sign-off from one repetition. The one-call authorization
is exhausted; stop before the next agent. Continue sequentially with Source
Evaluator only after separate authorization, preserving this artifact and
classifying any target defect separately from judge/provider evidence.

The result metadata reports `git_dirty=true` only because the pre-existing
untracked `.deepseek-runs/` runtime directory is present. The tracked/index
tree was clean, and that directory was preserved and not inspected or changed.
