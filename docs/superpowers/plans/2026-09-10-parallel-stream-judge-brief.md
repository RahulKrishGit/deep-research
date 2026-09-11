# Stream J — Shared DeepSeek Judge Adaptive Repair

Read the parent plan `2026-09-10-cross-agent-planner-fix-parity-parallel-repair.md`
first. This is the only stream authorized to change production code.

## Scope

Start from the coordinator's verified branch tip. Own only:

- `src/deep_research/providers/deepseek_provider.py`
- `tests/test_deepseek_provider.py`
- `tests/test_evaluation/test_judging.py`

Do not edit models, `ContractModel`, judging production code, validation,
failure taxonomy, OpenAI, config, scoring, retries, budgets, cases, or shared
documentation. Do not run live/provider/LangSmith/suite commands.

## Required TDD

1. Add and run RED tests before production code:
   - root `extra_forbidden` with a synthetic extra key and unique marker; the
     second response is valid. Assert exactly two calls, existing
     `category=extra_forbidden` and `field_paths=$`, plus repair text
     containing `only properties declared`, `undeclared properties`, and
     `re-check every retained value`; assert the synthetic key and marker are
     not echoed.
   - `string_bounds` at `rationale`, parameterized with empty and 2001-char
     synthetic invalid values; valid second response. Assert exactly two calls,
     `category=string_bounds`, `field_paths=rationale`, and repair text
     containing `every string constraint`, `minLength`, `maxLength`, and
     `pattern`; do not echo the invalid value.
2. Add the preservation regression: attempt 1 synthetic root extra,
   attempt 2 invalid rationale, terminal `StructuredOutputError`, diagnostics
   exactly `(extra_forbidden, $)` then `(string_bounds, rationale)`, and
   exactly two provider calls. No third attempt or programmatic sanitization.
3. Strengthen the judge no-score regression with those diagnostics:
   `judge_not_run`, `judge_schema_failure`, `judge_quality is None`, and
   one `run_judge()` provider invocation.
4. Implement only a small private category-guidance helper adjacent to
   `_validation_summary` and append its static guidance to the existing single
   repair message. `extra_forbidden` must instruct the model to return only
   declared properties and re-check retained values. `string_bounds` must tell
   it to satisfy every declared string constraint, including minLength,
   maxLength, and pattern. Keep the existing bounded summary and canonical
   schema.
5. Preserve exactly two structured attempts, `extra="forbid"`, rationale
   bounds 1..2000, bounded/secret-safe diagnostics, no fabricated scores, and
   global max tokens 4096. If a new local repair-guidance variable survives to
   the terminal raise, clear it with the existing provider-bearing locals.

## Verification and report

Run RED first, then:

    python -m pytest tests/test_deepseek_provider.py -q
    python -m pytest tests/test_evaluation/test_judging.py -q
    python -m pytest tests/test_deepseek_provider.py tests/test_evaluation/test_judging.py -q
    python -m ruff check src/deep_research/providers/deepseek_provider.py tests/test_deepseek_provider.py tests/test_evaluation/test_judging.py
    git diff --check

Write a report to
`.superpowers/sdd/2026-09-08-cross-agent-planner-fix-parity/parallel-stream-j-report.md`
and return its status, commit SHA, RED/GREEN counts, and invariant checks.
Stop and report instead of implementing if the test requires raw provider
content, field-name guessing, schema weakening, truncation/padding, a third
attempt, a retry, or a token change.

