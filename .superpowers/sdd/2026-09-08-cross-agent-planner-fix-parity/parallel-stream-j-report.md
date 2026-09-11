# Stream J — Shared DeepSeek Judge Adaptive Repair Report

Status: PASS

## Scope

- Added category-specific static repair guidance in
  `src/deep_research/providers/deepseek_provider.py`.
- Added offline TDD coverage in `tests/test_deepseek_provider.py` for root
  `extra_forbidden`, `string_bounds` at `rationale` for empty and 2,001-byte
  values, and preservation of both diagnostics after the single repair.
- Strengthened the typed no-score regression in
  `tests/test_evaluation/test_judging.py`.

No models, validation, judging production code, prompts, cases, scoring,
configuration, retry behavior, or token budgets were changed.

## TDD evidence

- RED: 3 guidance assertions failed for the missing production behavior; the
  preservation and judge no-score regressions retained their existing green
  behavior.
- GREEN: 4 new provider cases passed, including both parameterized string
  bounds cases; the strengthened judge regression passed.

## Invariant checks

- Structured output still performs exactly two attempts, with no third attempt
  or programmatic payload sanitization.
- `extra_forbidden` diagnostics remain `($,)`; `string_bounds` diagnostics at
  `rationale` remain bounded and typed.
- Repair text contains only the bounded category/path summary and static
  category guidance; synthetic provider keys, markers, and invalid values are
  not echoed.
- `extra="forbid"`, rationale bounds `1..2000`, and global `max_tokens=4096`
  remain unchanged.
- Failed judge execution remains `judge_not_run` with
  `judge_schema_failure`, `judge_quality is None`, and exactly one
  `run_judge()` provider invocation.

## Verification

- `python -m pytest tests/test_deepseek_provider.py -q` — 103 passed.
- `python -m pytest tests/test_evaluation/test_judging.py -q` — 19 passed.
- Combined scoped run — 122 passed.
- Ruff check for all three scoped source/test files — passed.
- `git diff --check` — passed.

All tests used injected fakes; no live provider, LangSmith, or suite command
was run.
