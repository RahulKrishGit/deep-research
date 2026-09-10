# Task 16 Brief — Controlled Scenario-Miss Semantics

## Objective

Repair the controlled evaluation contract so an injected in-memory dependency double distinguishes an unscripted fake-query miss from a true prohibited dependency/tool access. Preserve fail-closed real-service isolation and preserve all existing v1 evidence.

## Starting state

- Repository branch: `codex/cross-agent-planner-fix-parity`
- Integration target: the campaign branch/worktree, currently based on commit `2f623ad`.
- Read this brief as the exact task requirements. Do not inspect or modify unrelated worktrees.

## Required behavior

1. Controlled mode must continue to inject only in-memory/fake dependencies. Do not re-enable Tavily, HTTP, Chroma, LangSmith, or any live service.
2. Keep `real_services_used` empty in controlled mode.
3. Keep `no_prohibited_calls` as the hard security/isolation gate for an actual forbidden dependency/tool access. A query sent to the injected fake that has no scripted response is a scenario miss, not proof that a real service was attempted.
4. Add bounded typed telemetry for scenario misses/unscripted queries. It may retain only finite tool identity and bounded query identity needed to diagnose the frozen scenario; it must not retain secrets, prompts, provider responses, evaluator input blobs, raw exceptions, or unbounded payloads.
5. Any changed controlled case/scenario/evaluator contract must be versioned. Preserve v1 cases, v1 inputs, v1 artifacts, and v1 inventories as immutable evidence; never rewrite or silently reinterpret them.
6. Pin the real registered Critic strong case to a compatible planned-query/scripted-query relationship. The production instruction requiring an applicable planned query verbatim must not make a registered case fail merely because its scenario key is unrelated.
7. Correct the stale Critic case-test comment. Do not perform unrelated provider or prompt cleanup unless a safe shared constant is already the correct existing interface.
8. Do not change target prompts, agent budgets, thresholds, scoring weights, provider token limits, `llm.max_tokens == 4096`, fallback semantics, or live dependencies.

## TDD and verification

- Add a RED regression using the real registered case/scenario path that demonstrates the current unscripted fake query is classified as `prohibited_calls`.
- Add GREEN coverage proving the miss is represented in the new bounded telemetry and does not fail `no_prohibited_calls`, while actual forbidden dependency access still fails the security gate.
- Add tests for the registered Critic strong/gappy/budget scenario relationships, and update the existing stale comment.
- Run focused dependency/case/evaluator tests, then the full offline test suite, Ruff, and `git diff --check` with the campaign worktree source bound explicitly.
- Keep all tests fake-driven and network-zero. Do not run controlled, live, suite, or paid commands.

## Reporting and integration

- Work in an isolated Luna-max task worktree created from the campaign branch.
- Commit only the cohesive Task 16 implementation/tests/docs needed for this task.
- Write a report at `docs/superpowers/plans/2026-09-08-cross-agent-planner-fix-parity-task-16-report.md` containing status, root cause, files changed, RED/GREEN evidence, commit SHA, remaining concerns, and explicit confirmation that no secrets/provider content/live calls were recorded.
- Return only status, commit, one-line test summary, and concerns to the controller. The controller will review and integrate the commit into the campaign branch.
