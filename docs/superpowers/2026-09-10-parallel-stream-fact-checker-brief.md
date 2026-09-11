# Stream F — Fact Checker Fallback and Quality Diagnosis

Read the parent parallel-repair plan first. This is an offline diagnosis
stream. Do not infer a fix from judge `0.3675`, aggregate `0.6205`, or the
fallback diagnostic alone.

## Scope

Own only:

- read: `src/deep_research/agents/fact_checker.py`,
  `src/deep_research/evaluation/cases/fact_checker.py`
- tests: `tests/test_agents/test_fact_checker.py`,
  `tests/test_evaluation/test_cases_fact_checker.py`

Do not edit React/shared provider, prompts, evaluators, config, budgets, cases,
rubrics, weights, thresholds, shared docs, or Stream J files. No live/provider/
LangSmith/suite commands.

## Diagnosis gate

Use only typed evidence from the preserved Fact Checker documentation/artifact:
the four deterministic metrics, verified claims, typed ReAct stop/iteration/tool
fields, typed errors, fallback `{kind: output_limit, operation: react_decision}`,
and typed judge components. Build a claim/verdict/confidence/independent-domain
and fallback trajectory matrix. If the artifact is unavailable in this
worktree, use the tracked typed evidence and report that limitation; do not
invent raw provider behavior.

Add and run a characterization test named
`test_react_decision_output_limit_remains_a_conservative_fallback` using the
existing `_output_limit_error()` helper and a synthetic claim. It must prove:
the ReAct failure remains typed; the affected claim is not verified; confidence
is not invented; the run records provider failure/fallback; later claims are
not blindly attempted after non-recoverable failure; and no max-token override
is introduced.

Only if the typed live evidence shows a concrete target contract violation and
the test suite has a genuine RED reproducing that exact violation may you edit
`src/deep_research/agents/fact_checker.py`. Otherwise stop with no production
change. Never turn the fallback into a top-level target output-limit failure,
increase tokens/retries/iterations, tune prompts, or weaken conservative
`insufficient_evidence` semantics.

## Verification and report

    python -m pytest tests/test_agents/test_fact_checker.py -q
    python -m pytest tests/test_evaluation/test_cases_fact_checker.py -q
    python -m pytest tests/test_agents/test_fact_checker.py tests/test_evaluation/test_cases_fact_checker.py -q
    python -m ruff check src/deep_research/agents/fact_checker.py tests/test_agents/test_fact_checker.py tests/test_evaluation/test_cases_fact_checker.py
    git diff --check

Write
`.superpowers/sdd/2026-09-08-cross-agent-planner-fix-parity/parallel-stream-fact-checker-report.md`
with the typed matrix, RED/GREEN result, no-change or conditional-fix
decision, and remaining blocker. Do not edit shared documentation.

