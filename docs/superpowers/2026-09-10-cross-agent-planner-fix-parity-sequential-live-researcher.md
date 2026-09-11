# Sequential Live-Agent Evidence: Researcher

Date: 2026-09-10  
Candidate: `c368849ac1da0280f5d1ea8428ad8b3974929fce`  
Branch: `codex/cross-agent-planner-fix-parity`  
Tier: `live`, one repetition  
Phase: Task 19 sequential diagnosis/repair loop

## Scope and safety

This was the first user-authorized sequential live-agent run. Only Researcher
was executed. No source, test, case, prompt, rubric, threshold, budget,
fallback contract, or Git history was changed by the evaluation. No retry was
performed. The local dotenv source was loaded only by the campaign launcher;
credentials and environment values are not recorded here.

## Evidence

- Experiment: `https://eu.smith.langchain.com/o/dec92fa1-b347-483c-8a51-9cd727ee089c/projects/p/d4bf71bf-e11b-4fba-bb7b-7b2a13070d3c`
- Repository-relative artifact: `output/evaluations/sequential-live-20260910/researcher/cross-agent-planner-fix-parity-sequential-live-20260910-researcher-researcher-live-20260910T232024Z-c368849/results.json`
- Artifact SHA-256: `715B268D060617907021CB0283258A64AEAFB0A49D90B44F9A8CA158722754A0`
- Exit code: `1` (`FAILED`)
- Case/repetition: `researcher-live-evidence`, repetition `1/1`
- Hard gates: `12/14` passed
- Deterministic quality: `0.70`
- Judge: unscorable; typed `judge_schema_failure` diagnostics at `rationale` (attempt 1) and `$` (attempt 2)
- `react_stop_reason`: `max_iterations`
- `prohibited_call_count`: `0`

Failed gates were `citations_known` and `no_invented_sources`. The deterministic
metric `sources_are_real_urls` was `0.0`; subtopic coverage, source diversity,
budget respect, required fields, error typing, trace availability, and
prohibited-call isolation passed. The artifact contains the authoritative
bounded gate details; this report intentionally does not copy provider output,
prompts, evaluator inputs, or individual source URLs.

## Current diagnosis boundary

This is a Researcher target-quality/trajectory signal, not yet a confirmed
source-code defect. The evidence does not establish whether the failures came
from prompt/context wiring, source-validation behavior, live retrieval
conditions, or another boundary. The judge failure is independent typed
judge/provider evidence and does not supply a Researcher quality score.

No target-side typed `output_limit` was recorded, so no token-budget change is
authorized. The global `llm.max_tokens == 4096` remains unchanged.

## Focused confirmation after Task 20

- Candidate: `9319024` on `codex/cross-agent-planner-fix-parity`; exactly one Researcher live repetition ran after the Task 20 fix and the scoped Sol High review.
- Frozen execution: existing `config.yaml`, repository dotenv launcher, no CLI effort or budget overrides, global `llm.max_tokens == 4096`, and a fresh output namespace. No retry, suite run, or other agent ran in this confirmation.
- LangSmith experiment: `https://eu.smith.langchain.com/o/dec92fa1-b347-483c-8a51-9cd727ee089c/projects/p/45f357c5-ff0d-46f4-89c8-2248115c6b34`
- Repository-relative artifact: `output/evaluations/task20-researcher-confirmation-9319024/researcher/task20-researcher-confirmation-9319024-researcher-live-20260911T011711Z-9319024/results.json`
- Artifact SHA-256: `F4FC7B4ADD79E95D094BAF2CE6AD3A2159EEC3B1074D7CAD5EEF6C549E33B6B8`
- Exit/status: exit `1`, `INFRASTRUCTURE FAILURE`; `14/14` hard gates passed; deterministic quality `1.00`; mean score `n/a`.
- Judge boundary: `judge_not_run` because typed `judge_schema_failure` diagnostics occurred at `rationale` on attempt 1 and attempt 2. No target-side typed `output_limit` appeared.

Interpretation: the focused run produced no target-gate failure after the
provenance repair, so it does not justify a further Researcher prompt,
iteration, budget, or provider change. It is not a full Researcher quality
pass because the judge was unscorable. The result is preserved as separate
judge-infrastructure evidence, and the sequential loop can advance to Source
Evaluator without treating the Researcher target as repaired by score.

## Required next gate

Provide this report, the current remote branch, and the relevant Researcher
source/tests to Sol High in the existing browser session. Do not implement a
repair until Sol distinguishes a confirmed target defect from a shared
judge/provider failure or live-environment limitation. After any approved
minimal repair, run the required offline RED/GREEN verification and one
focused Researcher live confirmation before advancing to Source Evaluator.
