# Stream C — Critic Quality and Evaluator Diagnosis

Read the parent parallel-repair plan first. This is an offline diagnosis
stream. Do not infer the failing metric from deterministic `0.75` or from
judge `0.5925`/aggregate `0.6555`.

## Scope

Own only:

- read: `src/deep_research/agents/critic.py`,
  `src/deep_research/evaluation/cases/critic.py`
- tests: `tests/test_agents/test_critic.py`,
  `tests/test_evaluation/test_cases_critic.py`

Do not edit evaluators, prompts, shared ReAct/provider files, shared docs,
config, cases/rubrics/weights/thresholds, or other streams. No live/provider/
LangSmith/suite commands.

## Diagnosis gate

Read the latest Critic artifact's typed per-metric result and identify the exact
failing metric. Keep the earlier `critic_report_review` fallback separate: it
is not present in the latest rerun. If typed metric detail is unavailable,
stop with insufficient evidence.

If the metric is `no_spurious_gaps`, add both tests to
`tests/test_evaluation/test_cases_critic.py`:

- `test_live_spurious_gap_control_rejects_a_gap_for_evidence_the_report_fully_contains`
- `test_live_spurious_gap_diagnosis_distinguishes_a_report_acknowledged_unresolved_limitation`

Use the fixed live report and case data. The second case must use an expressly
acknowledged unresolved limitation (for example missing/accumulated long-term
durability evidence) and test whether keyword overlap alone is incorrectly
treated as a spurious gap. The live rubric requires gaps to be real, material,
specific, and actionable. If the controls behave coherently, make no code
change. If the acknowledged limitation is rejected solely due to keyword
overlap, return `SHARED EVALUATOR DEFECT CANDIDATE`; do not edit
`evaluators.py` here. For any other metric, add exactly one analogous offline
reproduction and stop if it requires guessing model intent.

Do not weaken route decisions, add searches/budget, alter planned context, or
tune Critic behavior merely because one live output named a gap.

## Verification and report

    python -m pytest tests/test_agents/test_critic.py -q
    python -m pytest tests/test_evaluation/test_cases_critic.py -q
    python -m pytest tests/test_agents/test_critic.py tests/test_evaluation/test_cases_critic.py -q
    python -m ruff check src/deep_research/agents/critic.py tests/test_agents/test_critic.py tests/test_evaluation/test_cases_critic.py
    git diff --check

Write
`.superpowers/sdd/2026-09-08-cross-agent-planner-fix-parity/parallel-stream-critic-report.md`
with the typed metric, controls, RED/GREEN result, and any serialized
evaluator follow-up. Do not edit shared documentation.

