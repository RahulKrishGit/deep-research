# Cross-Agent Planner Parity Live Evidence Rerun

Date: 2026-09-10
Candidate: `160c334e0a5aabc32716f574dd2420eb32da3157`
Compared with: `d697ff66cd0f42d4c1e8554622075a0ec40e1aff`
Branch: `codex/cross-agent-planner-fix-parity`
Tier: `live`, one repetition per agent

## Scope and safety

This was an authorized evidence-only rerun. No source, test, case, prompt, rubric, threshold, budget, fallback contract, or Git history was changed. The existing `.env` was loaded only by the ignored campaign launcher; `PYTHONPATH` was pinned to this worktree's `src`. The six calls ran sequentially, with no retries and no browser chats. Existing `.deepseek-runs/` and prior ignored artifacts were preserved.

Preflight confirmed the local branch and `origin/codex/cross-agent-planner-fix-parity` both resolved to the candidate SHA. The fresh output namespace did not exist before the first call. The effective non-secret configuration probe reported: target and judge `deepseek-v4-flash`; target effort `max` by default with `researcher` and `source_evaluator` overrides `high`; judge effort `max`; global `llm.max_tokens=4096`; live repetitions `1`; live threshold `0.75`; concurrency `1`.

## Exact commands and exit codes

Working directory for every command was the campaign worktree. Before each command, the process environment had:

```powershell
$env:PYTHONPATH = 'src'
```

The launcher and repository-root environment source were used exactly as follows; `<repo-root>/.env` denotes the local untracked environment file and is intentionally not embedded in this report. No CLI effort overrides were supplied because the frozen per-agent values were already in `config.yaml`.

```text
python .superpowers\sdd\2026-09-08-cross-agent-planner-fix-parity\run_with_repo_env.py <repo-root>/.env python -m deep_research.evaluation agent planner --tier live --config config.yaml --output-directory output/evaluations/live-rerun-160c334 --experiment-prefix cross-agent-planner-fix-parity-live-rerun-160c334-planner --verbose
exit_code=0

python .superpowers\sdd\2026-09-08-cross-agent-planner-fix-parity\run_with_repo_env.py <repo-root>/.env python -m deep_research.evaluation agent researcher --tier live --config config.yaml --output-directory output/evaluations/live-rerun-160c334 --experiment-prefix cross-agent-planner-fix-parity-live-rerun-160c334-researcher --verbose
exit_code=1

python .superpowers\sdd\2026-09-08-cross-agent-planner-fix-parity\run_with_repo_env.py <repo-root>/.env python -m deep_research.evaluation agent source-evaluator --tier live --config config.yaml --output-directory output/evaluations/live-rerun-160c334 --experiment-prefix cross-agent-planner-fix-parity-live-rerun-160c334-source-evaluator --verbose
exit_code=1

python .superpowers\sdd\2026-09-08-cross-agent-planner-fix-parity\run_with_repo_env.py <repo-root>/.env python -m deep_research.evaluation agent fact-checker --tier live --config config.yaml --output-directory output/evaluations/live-rerun-160c334 --experiment-prefix cross-agent-planner-fix-parity-live-rerun-160c334-fact-checker --verbose
exit_code=3

python .superpowers\sdd\2026-09-08-cross-agent-planner-fix-parity\run_with_repo_env.py <repo-root>/.env python -m deep_research.evaluation agent synthesizer --tier live --config config.yaml --output-directory output/evaluations/live-rerun-160c334 --experiment-prefix cross-agent-planner-fix-parity-live-rerun-160c334-synthesizer --verbose
exit_code=3

python .superpowers\sdd\2026-09-08-cross-agent-planner-fix-parity\run_with_repo_env.py <repo-root>/.env python -m deep_research.evaluation agent critic --tier live --config config.yaml --output-directory output/evaluations/live-rerun-160c334 --experiment-prefix cross-agent-planner-fix-parity-live-rerun-160c334-critic --verbose
exit_code=1
```

Exit code meanings are the harness meanings: `0` review-required/pass threshold, `1` deterministic or quality failure, and `3` infrastructure failure.

## Results compared with `d697ff6`

| Agent | Prior `d697ff6` | Rerun `160c334` | Typed change and interpretation |
| --- | --- | --- | --- |
| Planner | `REVIEW REQUIRED`; 16/16; deterministic 1.00; judge 0.7875; aggregate 0.8725 | `REVIEW REQUIRED`; 16/16; deterministic 1.00; judge 0.8785; aggregate 0.9271 | Same deterministic pass and status. The higher single repetition score is noisy evidence, not a quality claim. |
| Researcher | `FAILED`; 12/14; deterministic 0.70; judge 0.6115; aggregate 0.6469; failed `citations_known`, `no_invented_sources`; ReAct `max_iterations` | `FAILED`; 12/14; deterministic 0.70; judge not run; failed `citations_known`, `no_invented_sources`; ReAct `max_iterations`; judge `output_limit`, attempt 1 | Target-side deterministic failure repeated exactly. Judge-side output exhaustion prevented a comparable score. |
| Source Evaluator | `FAILED`; 13/14; deterministic 0.80; judge unscorable after typed schema failures at `$` and `rationale`; failed `low_confidence_flagged` | `FAILED`; 13/14; deterministic 0.80; judge 0.795; aggregate 0.797; failed `low_confidence_flagged` | Target deterministic result repeated. The judge boundary was transiently scorable in this repetition; status remains `FAILED` because the deterministic gate failed. |
| Fact Checker | `FAILED`; 15/15; deterministic 1.00; judge 0.4850; aggregate 0.6910; fallback `output_limit` at `react_decision` | `INFRASTRUCTURE FAILURE`; 15/15; deterministic 1.00; judge not run; typed judge schema failures at `rationale` (attempt 1) and `$` (attempt 2); fallback `output_limit` at `react_decision` | Fallback diagnostic repeated, but it remains distinct from a top-level target failure. Judge status changed from scored-low to infrastructure-blocked. |
| Synthesizer | `INFRASTRUCTURE FAILURE`; 15/15; deterministic 0.75; judge schema failures at `$` on both attempts | `INFRASTRUCTURE FAILURE`; 15/15; deterministic 0.75; judge schema failures at `$` (attempt 1) and `rationale` (attempt 2) | Same all-gates-passing judge infrastructure failure family; field location varied. |
| Critic | `FAILED`; 14/14; deterministic 0.80; judge 0.1425; aggregate 0.4055; fallback `schema_output` at `critic_report_review` | `FAILED`; 14/14; deterministic 0.75; judge 0.5925; aggregate 0.6555; no fallback diagnostic; `route_consistent=1.0`; prohibited calls `0` | Routing/context and prohibited-call gates still pass. Judge score rose and the deterministic score fell, but aggregate remains below 0.75 and no quality improvement is established. |

### Failure classification

- Target-side deterministic failures: Researcher (`citations_known`, `no_invented_sources`) and Source Evaluator (`low_confidence_flagged`). Critic's `no_spurious_gaps` metric was `0.0`, but its hard gates still passed.
- Judge/provider infrastructure failures: Researcher judge `output_limit`; Fact Checker judge `schema_output` at `rationale` and `$`; Synthesizer judge `schema_output` at `$` and `rationale`. These are not target-agent quality scores.
- Fallback diagnostics: Fact Checker again recorded the bounded typed pair `{kind: output_limit, operation: react_decision}`. This is a fallback snapshot, not a typed top-level target `output_limit` result. Critic had no fallback diagnostic in the rerun.
- Missing/blocked artifacts: none. All six commands completed and produced a `results.json`; no command/preflight failure required an early stop.

## Artifacts and hashes

All paths are relative to the campaign worktree and all six artifacts record candidate commit `160c334e0a5aabc32716f574dd2420eb32da3157`. `git_dirty=true` in artifact metadata reflects the preserved ignored `.deepseek-runs/` directory, not tracked source changes.

- Planner: `output/evaluations/live-rerun-160c334/planner/cross-agent-planner-fix-parity-live-rerun-160c334-planner-planner-live-20260910T222425Z-160c334/results.json` — SHA-256 `7ADCBF0065813B6098C9749D8031606727196C49ABE0A9C0D7BAADC8CEFB7781`
- Researcher: `output/evaluations/live-rerun-160c334/researcher/cross-agent-planner-fix-parity-live-rerun-160c334-researcher-researcher-live-20260910T222533Z-160c334/results.json` — SHA-256 `BA6549AD23F40B171CCF8C58589177DD343938240731FF6F3EF2CB04DCBDE969`
- Source Evaluator: `output/evaluations/live-rerun-160c334/source-evaluator/cross-agent-planner-fix-parity-live-rerun-160c334-source-evaluator-source-evaluator-live-20260910T222657Z-160c334/results.json` — SHA-256 `77FE09B49FB5C151D47B5FD167C221418E6FF07B8E3615A30F2C050C16F31F1C`
- Fact Checker: `output/evaluations/live-rerun-160c334/fact-checker/cross-agent-planner-fix-parity-live-rerun-160c334-fact-checker-fact-checker-live-20260910T222742Z-160c334/results.json` — SHA-256 `F6F944C96AAF51E9F660FD60990289FA297BEBA68FD293A1614DB12DBE6FE042`
- Synthesizer: `output/evaluations/live-rerun-160c334/synthesizer/cross-agent-planner-fix-parity-live-rerun-160c334-synthesizer-synthesizer-live-20260910T222902Z-160c334/results.json` — SHA-256 `663F64D3AEB73721866F43A4F0AF7DAD85D154FF8E882B30BE133869808978D5`
- Critic: `output/evaluations/live-rerun-160c334/critic/cross-agent-planner-fix-parity-live-rerun-160c334-critic-critic-live-20260910T223008Z-160c334/results.json` — SHA-256 `64AC116A2CE34B8B9D8D0A58244FB4AD9A0EF763F8649D21C1ADD2AC4971BB48`

## LangSmith links

- [Planner experiment](https://eu.smith.langchain.com/o/dec92fa1-b347-483c-8a51-9cd727ee089c/projects/p/3783cce4-1ca6-4716-b77e-fd839ab8e400) · [repetition trace](https://eu.smith.langchain.com/o/dec92fa1-b347-483c-8a51-9cd727ee089c/projects/p/3783cce4-1ca6-4716-b77e-fd839ab8e400/r/01a08d6c-2c21-7422-a75d-f4df67f8d12f?poll=true)
- [Researcher experiment](https://eu.smith.langchain.com/o/dec92fa1-b347-483c-8a51-9cd727ee089c/projects/p/1e553968-d0c3-480a-8b44-8caa7fa339f2) · [repetition trace](https://eu.smith.langchain.com/o/dec92fa1-b347-483c-8a51-9cd727ee089c/projects/p/1e553968-d0c3-480a-8b44-8caa7fa339f2/r/01a08d6d-3550-7f12-91ef-a1922dc11476?poll=true)
- [Source Evaluator experiment](https://eu.smith.langchain.com/o/dec92fa1-b347-483c-8a51-9cd727ee089c/projects/p/d2103d73-5894-40c6-9c9c-a5e15ced610e) · [repetition trace](https://eu.smith.langchain.com/o/dec92fa1-b347-483c-8a51-9cd727ee089c/projects/p/d2103d73-5894-40c6-9c9c-a5e15ced610e/r/01a08d6e-7cdf-7a61-accf-cbfea604049c?poll=true)
- [Fact Checker experiment](https://eu.smith.langchain.com/o/dec92fa1-b347-483c-8a51-9cd727ee089c/projects/p/a88dc17a-948d-4a4d-a976-85db3b5fb913) · [repetition trace](https://eu.smith.langchain.com/o/dec92fa1-b347-483c-8a51-9cd727ee089c/projects/p/a88dc17a-948d-4a4d-a976-85db3b5fb913/r/01a08d6f-2df8-7f33-a3ec-774d1c59371a?poll=true)
- [Synthesizer experiment](https://eu.smith.langchain.com/o/dec92fa1-b347-483c-8a51-9cd727ee089c/projects/p/835e2571-8938-4e67-8f27-52189fa10edd) · [repetition trace](https://eu.smith.langchain.com/o/dec92fa1-b347-483c-8a51-9cd727ee089c/projects/p/835e2571-8938-4e67-8f27-52189fa10edd/r/01a08d70-6265-7042-91ce-e9aec1542ec9?poll=true)
- [Critic experiment](https://eu.smith.langchain.com/o/dec92fa1-b347-483c-8a51-9cd727ee089c/projects/p/7e8fda80-7039-43a6-b135-9d7c9fa345b9) · [repetition trace](https://eu.smith.langchain.com/o/dec92fa1-b347-483c-8a51-9cd727ee089c/projects/p/7e8fda80-7039-43a6-b135-9d7c9fa345b9/r/01a08d71-63cb-7c82-b112-dba7b15808ac?poll=true)

## Conclusion and authorization ruling

This single live repetition does not change the campaign conclusions:

- Required fields remain supported: no new `required_fields_present` failure appeared, and the deterministic failure IDs remain the previously observed agent-specific gates.
- Critic routing/context remains supported: `route_consistent=1.0`, all 14/14 hard gates passed, and prohibited-call count remained `0`. This does not establish broad Critic quality.
- Judge infrastructure remains unresolved and noisy: judge-only output-limit/schema failures persisted for multiple agents, while Source Evaluator and Critic were scorable in this repetition. The field-path variation is not evidence of a target-agent repair opportunity.
- No target-side typed `output_limit` was observed. Researcher's `output_limit` was judge-side; Fact Checker's `react_decision` output-limit was only a typed fallback diagnostic. Therefore no operation-specific budget repair is justified.
- The global `llm.max_tokens=4096` remains authorized and unchanged. No global increase, target-operation increase, retry, or suite run was performed or authorized by this evidence. Only this secret-safe report and its bookkeeping may be committed; no source or evaluation-input change is justified.

The rerun therefore adds current evidence about judge instability and confirms the prior contract/routing conclusions, but it does not support a claim of quality improvement from one noisy repetition.
