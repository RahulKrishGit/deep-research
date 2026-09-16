# Task 16 Report — Controlled Scenario-Miss Contract Repair

## Status

Complete for the approved Task 16 scope in the isolated campaign worktree.

- Worktree: `.worktrees/cross-agent-planner-fix-parity`
- Branch: `codex/cross-agent-planner-fix-parity`
- Implementation commit: `d1ee97d1ed6d7dff6a47e4efbadebba8d541e419`
- Task 17 was not started. Whole-branch review, live-provider evaluation, paid evaluation, and suite evaluation were not run.

## Root cause

The controlled `_ScriptedSearchClient` treated an unknown query as a
`ProhibitedDependencyError` and added it to `prohibited_calls`. A missing exact
key means that the injected fake had no scripted response; it does not show
that Tavily or another real service was reached. This conflated a scenario
miss with a genuine prohibited dependency access and caused the
`no_prohibited_calls` gate to report the wrong failure.

The Critic strong controlled scenario also used a search-response key that was
not one of the case's planned queries. That made the fail-closed fake expose
scenario-key drift instead of exercising the intended planned-query path.

## Repair and contract boundary

- Added additive, bounded `DependencyLedger.scenario_misses` telemetry and a
  `scenario_contract_version` marker. Legacy ledger payloads remain valid with
  the default v1 marker; new controlled bundles use contract version 2.
- An unknown controlled search records a normalized, bounded query identity in
  `scenario_misses`, returns a failed tool result with typed
  `ScenarioMissError`, and does not populate `prohibited_calls`.
- The telemetry is capped at 16 entries of at most 256 characters each. It
  contains only tool/query identity; no provider response, prompt, evaluator
  input blob, credential, or raw exception text is stored.
- The controlled bundle remains fully injected and offline. `real_services_used`
  remains empty. An unscripted controlled HTTP fetch continues to use the
  genuine prohibited-access path, so `no_prohibited_calls` still fails for
  actual prohibited access.
- The gate implementation continues to inspect only `prohibited_calls`; tests
  now pin that a scenario miss alone passes and a prohibited call still fails.
- The Critic strong scenario now keys its response by the exact applicable
  planned query. Tests pin the strong, gappy, and budget scenario mappings to
  planned queries and pin the repaired controlled contract version. Case and
  reference semantics remain v1.

## RED evidence

Before the production repair, the injected unknown-search regression failed:
the ledger contained a prohibited call and had no separate scenario-miss
telemetry. The newly added bounded-ledger, typed-error, gate, and Critic
mapping tests also failed before their corresponding implementation changes.

## GREEN evidence

- Focused Task 16 tests: `27 passed`, `1 warning`.
- Relevant evaluation, case, model, target, and runner tests: `277 passed`,
  `1 warning`.
- Evaluation package plus Critic agent tests: `748 passed`, `1 warning`.
- Full offline suite: `1,952 passed, 1 deselected, 2 warnings`.
- `python -m ruff check src tests`: passed.
- `git diff --check`: passed.

The two warnings are existing dependency deprecations from LangSmith and
Starlette/httpx; they are not Task 16 failures.

## Changed files

Implementation and tests in commit
`d1ee97d1ed6d7dff6a47e4efbadebba8d541e419`:

- `src/deep_research/evaluation/dependencies.py`
- `src/deep_research/evaluation/models.py`
- `tests/test_evaluation/test_cases_critic.py`
- `tests/test_evaluation/test_cases_fact_checker.py`
- `tests/test_evaluation/test_dependencies_controlled.py`
- `tests/test_evaluation/test_evaluators_general.py`
- `tests/test_evaluation/test_models.py`

This report and the append-only fix-log entry are the documentation record for
the implementation commit. The pre-existing `.deepseek-runs/` evidence
directory was preserved and not staged.

## Remaining concerns

- Task 17's finite structured-validation and judge-status work remains
  untouched.
- The controlled harness is offline-only; no claim is made about live-provider
  quality or paid evaluation results.
- The HTTP/page scenario-miss behavior intentionally remains prohibited until
  a separate task defines whether missing scripted pages should have the same
  diagnostic classification. Changing it here would weaken the existing
  true-prohibited-access regression boundary.
- The deferred whole-branch review remains deferred by instruction.
