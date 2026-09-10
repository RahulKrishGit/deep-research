# Cross-Agent Planner Parity Live Evaluation

Date: 2026-09-10  
Candidate: `d697ff66cd0f42d4c1e8554622075a0ec40e1aff`  
Branch: `codex/cross-agent-planner-fix-parity`  
Tier: `live`, one repetition per agent  
Authorization: explicit user request after Task 17 offline review  

This is a post-plan evidence amendment. The original implementation plan prohibited live-tier calls while Tasks 1–17 were being repaired; the user explicitly authorized this six-agent live wave afterward. The original offline and controlled artifacts remain immutable. No source, case, rubric, threshold, prompt, budget, or fallback contract was changed for this run.

## Safe preflight

- The repository-root `.env` was loaded only through the ignored `run_with_repo_env.py` launcher; no values were printed or copied into the worktree.
- The campaign `src` directory was pinned with `PYTHONPATH`; the shared editable-install pointer to another worktree was not used.
- Target and judge model: `deepseek-v4-flash`; judge effort: `max`.
- Target effort: `max` for Planner, Fact Checker, Synthesizer, and Critic; `high` for Researcher and Source Evaluator.
- Global `llm.max_tokens`: `4096`; live repetitions: `1`; live threshold: `0.75`; concurrency: `1`.
- Every artifact records candidate SHA `d697ff6`. `git_dirty=true` in artifact metadata reflects the preserved ignored `.deepseek-runs/` evidence directory, not tracked source changes.

## Results

| Agent | Live case | Gates | Deterministic | Judge | Aggregate | Status | Interpretation |
| --- | --- | ---: | ---: | ---: | ---: | --- | --- |
| Planner | `planner-live-scope` | 16/16 | 1.00 | 0.7875 | 0.8725 | `REVIEW REQUIRED` | Control passed. |
| Researcher | `researcher-live-evidence` | 12/14 | 0.70 | 0.6115 | 0.6469 | `FAILED` | `citations_known` and `no_invented_sources` failed; `react_stop_reason=max_iterations`. |
| Source Evaluator | `source-evaluator-live-ranking` | 13/14 | 0.80 | n/a | n/a | `FAILED` | `low_confidence_flagged` failed; judge was unscorable after typed schema failures at `$` and `rationale`. |
| Fact Checker | `fact-checker-live-verification` | 15/15 | 1.00 | 0.4850 | 0.6910 | `FAILED` | All deterministic gates passed; scored quality remained below the 0.75 live threshold. A fallback diagnostic recorded `output_limit` for `react_decision`; this was not a top-level typed target failure. |
| Synthesizer | `synthesizer-live-report` | 15/15 | 0.75 | n/a | n/a | `INFRASTRUCTURE FAILURE` | All deterministic gates passed; both judge attempts had typed `schema_output` failures at `$`, with no fabricated score. |
| Critic | `critic-live-review` | 14/14 | 0.80 | 0.1425 | 0.4055 | `FAILED` | All deterministic gates passed, including `route_consistent` and `no_prohibited_calls`; judge quality was low. A fallback diagnostic recorded `schema_output` for `critic_report_review`. |

## Did the fixes help?

Yes for the shared contracts and failure interpretation:

- `required_fields_present` passed for all six agents, including the four production cross-channel cases repaired offline.
- Critic passed `route_consistent` despite its typed provider-fallback diagnostic and passed `no_prohibited_calls`, supporting the fallback-routing and context-wiring repairs.
- Synthesizer’s all-gates-passing judge failure became `INFRASTRUCTURE FAILURE`, preserving the no-fabricated-score rule. Source Evaluator’s mixed deterministic-plus-judge failure remained ordinary `FAILED`, preserving deterministic failure precedence.
- No live run produced a prohibited-call count, tracker transport failure, target contract failure, or target-side typed `output_limit` failure that would justify an operation-specific budget.

No broad agent-quality pass can be claimed:

- Researcher still has citation/source-grounding failures.
- Fact Checker and Critic have all deterministic gates passing but low judge-scored quality.
- Source Evaluator and Synthesizer still expose a shared judge structured-output boundary failure in live evaluation. The typed diagnostics make that boundary visible; they do not prove a target-agent defect.
- Task 16’s scenario-miss separation is a controlled-harness contract and cannot be directly measured by a live run against real dependencies. The live wave showed no prohibited-call regression, but the controlled RED/GREEN evidence remains the proof for that repair.

The evidence does not authorize increasing the global or any target operation token budget. The Fact Checker `fallback_provider_diagnostic` is intentionally distinct from a typed target failure, and the global cap remains `4096`.

## LangSmith experiment links

- Planner: https://eu.smith.langchain.com/o/dec92fa1-b347-483c-8a51-9cd727ee089c/projects/p/13e4b795-40dc-4445-a352-58f4ebb341d8
- Researcher: https://eu.smith.langchain.com/o/dec92fa1-b347-483c-8a51-9cd727ee089c/projects/p/bbe3dfa8-2154-42a0-8b9c-146a768d3267
- Source Evaluator: https://eu.smith.langchain.com/o/dec92fa1-b347-483c-8a51-9cd727ee089c/projects/p/59ccc129-70d4-4a0d-b724-77b32417d3f8
- Fact Checker: https://eu.smith.langchain.com/o/dec92fa1-b347-483c-8a51-9cd727ee089c/projects/p/a0279a2a-25ed-4915-baab-372bf21c6a5c
- Synthesizer: https://eu.smith.langchain.com/o/dec92fa1-b347-483c-8a51-9cd727ee089c/projects/p/f2176787-ac5a-4280-a4bf-996b6c9fda6b
- Critic: https://eu.smith.langchain.com/o/dec92fa1-b347-483c-8a51-9cd727ee089c/projects/p/15b7d8a3-c86c-4168-8870-167dec3f805c

## Local artifact paths and SHA-256

The result files are ignored evaluation artifacts and remain local evidence only:

- `output/evaluations/planner/cross-agent-planner-fix-parity-live-20260910-planner-planner-live-20260910T191110Z-d697ff6/results.json` — `A8AFABCEDEDC477D211258B910387FE801F855B09BFD7398D8A6E8ED9AD53609`
- `output/evaluations/researcher/cross-agent-planner-fix-parity-live-20260910-researcher-researcher-live-20260910T191215Z-d697ff6/results.json` — `AB4CE2A7ABB542726D33220E42CDB378A866E434EB9F5A23C17BA949D200B53E`
- `output/evaluations/source-evaluator/cross-agent-planner-fix-parity-live-20260910-source-evaluator-source-evaluator-live-20260910T191308Z-d697ff6/results.json` — `247524D8DB9C848E95AAED7050256559A6A9247061203CA9AE1EBE3F03E29E25`
- `output/evaluations/fact-checker/cross-agent-planner-fix-parity-live-20260910-fact-checker-fact-checker-live-20260910T191351Z-d697ff6/results.json` — `0330194212B219D133DFF39DA0A13E33518111FF39183A3F92D6E27997B744AA`
- `output/evaluations/synthesizer/cross-agent-planner-fix-parity-live-20260910-synthesizer-synthesizer-live-20260910T191500Z-d697ff6/results.json` — `78EC7057DEF1C330C591CB69DB9C670263F08EBC80AE4E63D6732DF5D026ADB7`
- `output/evaluations/critic/cross-agent-planner-fix-parity-live-20260910-critic-critic-live-20260910T191555Z-d697ff6/results.json` — `5B2D1E8A341578AA331A2096AE05472977E7947937AC6348BEF977D7BD889F00`
