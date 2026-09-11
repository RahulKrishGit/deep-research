# Parallel Repair Wave — Sol High Plan

Base branch: `codex/cross-agent-planner-fix-parity`.

The six-agent live evidence set is complete. This wave is offline-only and
must start from the branch tip recorded by the coordinator. It uses four
disjoint worker streams: one approved shared-judge implementation and three
diagnosis-first agent streams. Workers must not run live/provider/LangSmith or
suite evaluations, edit shared documentation, or change budgets, prompts,
rubrics, cases, thresholds, scoring, retries, or `llm.max_tokens=4096`.

## Evidence classification

- Planner: 16/16 gates, deterministic `1.00`, judge `0.8785`, aggregate
  `0.9271`, `REVIEW REQUIRED`; no code change.
- Researcher: latest confirmation 14/14 and deterministic `1.00`; remaining
  failure is judge schema validation at `rationale` on both attempts; no
  target change.
- Source Evaluator: v2 confirmation 14/14 and deterministic `1.00`; judge
  failed with `extra_forbidden/$` then `string_bounds/rationale`; no target
  or case change.
- Fact Checker: 15/15 and deterministic `1.00`; all deterministic metrics
  `1.00`; judge `0.3675`, aggregate `0.6205`; fallback diagnostic
  `{kind: output_limit, operation: react_decision}` is not a top-level target
  failure or token-budget authorization. Diagnose only.
- Synthesizer: 15/15, deterministic `0.75`; judge unscorable with `$` then
  `rationale`; diagnose the typed per-metric result only.
- Critic: 14/14, deterministic `0.75`, judge `0.5925`, aggregate
  `0.6555`, `route_consistent=1.0`, prohibited calls `0`, and no fallback
  in the latest rerun; diagnose the typed per-metric result only.

## Stream ownership

| Stream | Writable files | Decision |
|---|---|---|
| J — shared judge | `src/deep_research/providers/deepseek_provider.py`, `tests/test_deepseek_provider.py`, `tests/test_evaluation/test_judging.py` | Implement approved adaptive repair guidance with TDD. |
| F — Fact Checker | `tests/test_agents/test_fact_checker.py`, `tests/test_evaluation/test_cases_fact_checker.py`; `src/deep_research/agents/fact_checker.py` only after typed RED | Diagnose fallback/quality; production change is conditional. |
| S — Synthesizer | `tests/test_agents/test_synthesizer.py`, `tests/test_evaluation/test_cases_synthesizer.py` | Diagnose exact typed metric; no production change at start. |
| C — Critic | `tests/test_agents/test_critic.py`, `tests/test_evaluation/test_cases_critic.py` | Diagnose exact typed metric; no production/evaluator change at start. |

Reserved from every worker: `evaluators.py`, `prompts.py`, shared docs,
configuration, live cases/rubrics/weights/thresholds, provider files outside
Stream J, and all live/provider commands. If a diagnosis points at a shared
file, return a finding; do not edit it.

After all workers return: task-scoped Luna Max reviews; coordinator integrates
only approved commits; one consolidated offline gate; one scoped Sol High
review of the integrated range; then at most one live confirmation per affected
agent. Planner receives no new live run. No live suite, retry loop, token or
budget increase, prompt tuning, or judge schema relaxation is allowed.

