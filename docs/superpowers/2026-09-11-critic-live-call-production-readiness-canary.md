# Critic Live-Call Production Readiness Canary

Date: 2026-09-11

## Scope

Exactly one Critic live repetition ran, under explicit immediate authorization,
after the offline gate in section 77 of
`docs/superpowers/2026-09-08-cross-agent-planner-fix-parity-fix-log.md`. The run
was sequential and used the repository dotenv launcher and a fresh artifact
namespace. No retry, parallel run, second agent, suite, prompt change, budget
change, or provider change was made.

Candidate: `fe434cf0f303177c7bcddbaf4f9c36c579a5719e` on
`codex/cross-agent-planner-fix-parity` (`fe434cf`).

The candidate commit changed only
`docs/superpowers/2026-09-08-cross-agent-planner-fix-parity-fix-log.md`, and the
tracked and index tree was clean before the run. The artifact's
`git_dirty=true` reflects only the preserved, uninspected `.deepseek-runs/`
runtime directory, exactly as recorded for the previous Critic canary.

## Configuration actually resolved

The frozen values quoted in the earlier canary notes were checked against the
resolved configuration before this run, and one of them was **wrong**.

| Setting | Earlier notes recorded | Actually resolved | Verdict |
| --- | --- | --- | --- |
| `llm.max_tokens` | `4096` | `4096` | matches |
| `retry_count` | `5` | **`2`** | **does not match** |
| `retry_initial_delay` | `1.0` | `1.0` | matches |
| `retry_max_delay` | `16.0` | `16.0` | matches |
| `temperature` | `0.7` | `0.7` | matches |
| target / judge model | `deepseek-v4-flash` | `deepseek-v4-flash` | matches |
| thinking mode | enabled | `enabled` | matches |
| judge transport | `deepseek_responses_json_schema_v1` | `deepseek_responses_json_schema_v1` | matches |

`.env` sets `LLM_RETRY_COUNT=2`, and environment overrides win over
`config.yaml`'s `retry_count: 5`. Because the dotenv launcher is used for every
canary, **`retry_count` has been `2` for every recorded canary**, and the
documented value of `5` was never in force. The artifact never recorded the
retry policy, so nothing contradicted the incorrect claim until it was checked
directly.

No credential or dotenv value is recorded here.

## Evidence

Artifact:

`output/evaluations/live-critic-readiness/critic/critic-readiness-fe434cf-critic-live-20260911T212856Z-fe434cf/results.json`

SHA-256:

`5CA71085C768660179728B735A291AF93537F701AB335F241B34170B7C109372`

LangSmith experiment:

`https://eu.smith.langchain.com/o/dec92fa1-b347-483c-8a51-9cd727ee089c/projects/p/12ac1c6f-8458-4493-a168-351cd927ec54`

## Typed result

| Signal | Result |
| --- | --- |
| Runner exit | `1` |
| Runner status | `FAILED` |
| Case | `critic-live-review`, version `1` |
| Target hard gates | `14/14` passed |
| Deterministic quality | `0.80` |
| Deterministic metrics | `score_bounded=1.0`, `route_consistent=1.0`, `rationale_present=0.0`, `no_spurious_gaps=1.0` |
| Judge status | `scored`, diagnostics empty |
| Judge quality | `0.6325` (was `0.2525`) |
| Aggregate quality | `0.6995` (was `0.4715`); live threshold `0.75` |
| Fallback provider kind | `schema_output` |
| Fallback provider operation | `critic_report_review` |
| **Retained diagnostics** | attempt `1`: `category=json_invalid`, `field_paths=("$",)`; attempt `2`: `category=json_invalid`, `field_paths=("$",)` |
| ReAct stop reason | `provider_error` |
| Prohibited calls | `0` |
| Target errors | none |
| Judge prompt fingerprint | `77a0898f4267` |
| Judge configuration fingerprint | `924caf47aa0d` |
| Target prompt fingerprint | `e984bf2d3d23` |
| Configuration fingerprint | `fddbc542feff` |

## What changed, and what did not

**The retained diagnostics worked.** The artifact now names the per-attempt
`category` and `field_paths` for a live schema failure. For the first time in
this campaign, a failed live Critic repetition is attributable without repeating
the paid call. All three earlier Critic diagnoses were blocked on exactly this.

**The judge-legibility fix worked as designed.** Judge quality rose from
`0.2525` to `0.6325`, and the judge's rationale is explicit that it changed its
standard: "Provider fallback present ... so this is scored as a fallback, not as
a review ... Per fallback guidance I do not penalise empty
gaps/recommended_queries as if a review had been possible." The judge still
reports the honest residuals — `reasoning_quality=0.45`, `usefulness=0.5`,
`critique_actionability=0.2` — because the fallback offers nothing actionable to
the next research pass. That is a correct reading of a degraded run rather than
the earlier reading of it as a bad review.

**The Critic still produced no review.** `react_stop_reason=provider_error` and
`rationale_present=0.0` are unchanged, so defect 1's prompt fix did **not**
prevent the provider failure. The run remains below the `0.75` live threshold.

## What the diagnostics prove, and what they rule out

**Ruled out: output-limit truncation.** Both attempts report
`category=json_invalid`, and
`DeepSeekChatProvider._structured_attempt` raises `ProviderOutputLimitError`
before validation whenever the finish reason is `length`
(`deepseek_provider.py:715`) and `_choice_text` raises a
`ProviderResponseError` unless the finish reason is `stop` and the content is a
non-empty string (`deepseek_provider.py:443-451`). Since the failure surfaced as
a *validation* diagnostic rather than either of those, both attempts ended with
`finish_reason=stop` and produced **non-empty text that is not parseable JSON**.
The response was not truncated and was not empty.

**Therefore the cause is a provider/transport contract problem, not a context,
prompt, schema-shape, or budget problem.** The retention of a full 6,000-character
report in the spot-check prompt did not change the failure mode, and both the
initial attempt and the one repair attempt failed identically at the root with
the same category. A repair prompt that re-supplies the JSON Schema cannot help
when the model is not emitting JSON at all.

**Not yet provable from this artifact.** `field_paths=("$",)` is the root, so
the diagnostics deliberately carry no field detail, and the project's safety
contract forbids retaining raw provider text. The artifact cannot distinguish
markdown-fenced JSON, preamble prose around JSON, or reasoning text returned in
the content field.

## Evidence-backed hypothesis for the next step

The same repository already contains a working DeepSeek structured-output path
for the same model: the judge. `DeepSeekJudgeProvider._responses_structured_attempt`
(`deepseek_provider.py:825`) sends `response_format={"type": "json_schema",
"schema": ...}` (`deepseek_provider.py:859-861`) and succeeded in this very run,
scoring the verdict with zero diagnostics. The target path used by the Critic,
`DeepSeekChatProvider.complete_structured`, instead sends
`response_format={"type": "json_object"}` (`deepseek_provider.py:686`) plus a
JSON-Schema system instruction built by `_json_instruction`
(`deepseek_provider.py:134-148`).

So the measurable difference between a path that works and a path that fails
twice on the same model in the same run is **schema-enforced JSON mode versus
plain JSON mode**. The concrete next step is an offline, no-cost investigation
of that contract difference — not another paid repetition, and still not a
token, retry, or prompt-budget change.

`retry_count=2` is a secondary, independent finding: with two structured
attempts per call and two retries at the HTTP layer, the Critic's review call
has less headroom than the campaign believed. It is recorded here rather than
changed, because a retry increase would mask a deterministic failure mode that
the next investigation can identify directly.

## Boundary

No token, retry, repair-prompt, temperature, or budget change is authorized by
this artifact. No second paid repetition is authorized. The offline gate
recorded in fix-log section 77 remains in force, and no production behaviour was
changed by this canary.
