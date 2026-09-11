# Stream S — Synthesizer Deterministic-Quality Diagnosis

Read the parent parallel-repair plan first. This is an offline diagnosis
stream. The live deterministic value `0.75` is not enough to identify a fix.

## Scope

Own only:

- read: `src/deep_research/agents/synthesizer.py`,
  `src/deep_research/evaluation/cases/synthesizer.py`
- tests: `tests/test_agents/test_synthesizer.py`,
  `tests/test_evaluation/test_cases_synthesizer.py`

Do not edit evaluators, prompts, provider files, shared docs, rubrics, case
inputs, weights, thresholds, config, or any other stream. No live/provider/
LangSmith/suite commands.

## Diagnosis gate

Read the preserved artifact's typed per-metric result and identify the exact
failing metric ID/detail. If the typed artifact is unavailable, report
insufficient evidence and do not reverse-engineer the `0.75` from weights.

If the exact typed metric is coverage, add a focused test named
`test_live_report_messages_expose_every_required_coverage_topic` that builds
the live `SynthesisTask`, calls `report_messages`, and asserts every required
live subtopic title is visible to model input. If another metric is typed,
write the analogous single-metric reproduction. A passing reproduction means
there is no demonstrated target defect. A failing reproduction is a
`SHARED EVALUATOR DEFECT CANDIDATE`; return it for a later serialized Sol-
reviewed evaluator repair rather than editing `evaluators.py` here.

The Synthesizer has one structured draft call followed by deterministic local
composition/persistence. Do not tune prompts, insert rubric phrases into
reports, change persistence semantics, or derive target changes from an
unscorable judge.

## Verification and report

    python -m pytest tests/test_agents/test_synthesizer.py -q
    python -m pytest tests/test_evaluation/test_cases_synthesizer.py -q
    python -m pytest tests/test_agents/test_synthesizer.py tests/test_evaluation/test_cases_synthesizer.py -q
    python -m ruff check src/deep_research/agents/synthesizer.py tests/test_agents/test_synthesizer.py tests/test_evaluation/test_cases_synthesizer.py
    git diff --check

Write
`.superpowers/sdd/2026-09-08-cross-agent-planner-fix-parity/parallel-stream-synthesizer-report.md`
with the typed metric, RED/GREEN result, diagnosis, and any serialized
follow-up recommendation. No shared-doc edit.

