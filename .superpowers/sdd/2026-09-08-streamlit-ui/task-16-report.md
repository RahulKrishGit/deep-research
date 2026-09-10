# Task 16 implementation report

## Changed files

- `src/deep_research/utils/claims.py`
  - Added the shared `normalize_claim_text()` and `latest_claims()` projection.
  - Claim identity is normalized text only; the last append-ordered record wins.
- `src/deep_research/agents/sources.py`
  - Added the shared latest-record projection for normalized scored-source URLs.
- `src/deep_research/ui/progress.py`
  - `fact_check_summary()` now consumes the effective claim projection.
  - Source summaries consume the shared latest normalized-URL source projection.
- `src/deep_research/agents/synthesizer.py`
  - Synthesis tasks, limitation reasons, provider claim digests, report composition,
    and memory selection now use effective latest claims and sources.
- `src/deep_research/agents/report.py`
  - Citation indexing, verified/uncertain claim rendering, and source appendix
    rendering use the effective latest claims/sources.
- `tests/test_ui/test_progress.py`
  - Extended the refinement regression to change `[A]/verified` into
    `[A,B]/contradicted`.
- `tests/test_agents/test_synthesizer.py`
  - Added offline regressions for append-only state, effective task projection,
    report sections, citations, memory selection, and claim counts.
- `tests/test_agents/test_report.py`
  - Updated an existing fixture so two distinct verdicts use distinct claims
    under the new stable-identity contract.
- `.superpowers/sdd/2026-09-08-streamlit-ui/task-16-report.md`
  - This report.

## Selected invariant

`ResearchState.verified_claims` remains append-only history. At presentation and
synthesis/report boundaries, `latest_claims()` normalizes each claim with
`" ".join(text.split()).casefold()` and retains the latest record for each
normalized claim text. Evidence URLs are mutable claim attributes, so they are
not part of claim identity. The selected record carries its latest verdict,
evidence, contradictions, confidence, and source URLs.

Scored sources retain the existing normalized-URL identity convention and use
the latest record per normalized URL for synthesis/report projections. No
provider prompts, graph reducers, or state contracts were changed.

## Red-green evidence

The required regressions were added before production changes.

Red run:

```text
python -m pytest tests/test_ui/test_progress.py::test_fact_check_summary_deduplicates_refinement_passes_and_keeps_latest_record tests/test_agents/test_synthesizer.py::test_build_task_projects_one_latest_claim_judgment_without_mutating_history tests/test_agents/test_synthesizer.py::test_report_uses_only_latest_claim_judgment_for_sections_memory_and_counts -q
FFF                                                                      [100%]
3 failed in 1.06s
```

The failures showed the old verified record remained counted, remained bound to
the synthesis task, and still reached report/memory output.

Green focused run:

```text
python -m pytest tests/test_ui/test_progress.py::test_fact_check_summary_deduplicates_refinement_passes_and_keeps_latest_record tests/test_agents/test_synthesizer.py::test_build_task_projects_one_latest_claim_judgment_without_mutating_history tests/test_agents/test_synthesizer.py::test_report_uses_only_latest_claim_judgment_for_sections_memory_and_counts -q
3 passed in 0.71s
```

Focused UI/agent/report run:

```text
python -m pytest tests/test_ui/test_progress.py tests/test_agents/test_synthesizer.py tests/test_agents/test_report.py tests/test_agents/test_synthesis_seam.py -q
81 passed in 0.80s

python -m ruff check src/deep_research/utils/claims.py src/deep_research/agents/sources.py src/deep_research/agents/report.py src/deep_research/agents/synthesizer.py src/deep_research/ui/progress.py tests/test_ui/test_progress.py tests/test_agents/test_synthesizer.py tests/test_agents/test_report.py
All checks passed!
```

Complete relevant offline UI/agent run:

```text
python -m pytest tests/test_ui tests/test_agents -q
590 passed, 2 skipped in 32.88s
```

Additional verification:

```text
git diff --check
Passed with no whitespace errors.
```

No live providers, network calls, or paid services were used.

## Commit IDs

- Base: `c5d0049328883f0dc4fbe597f5640cf07088925a`
- Implementation and tests: `ac0fec3c1ec6efc537f1d5cc7a875be11767b7a3`

## Remaining concerns

None identified for the scoped task. The pre-existing modification to
`.superpowers/sdd/2026-09-08-streamlit-ui/progress.md` was preserved and was
not included in the implementation commit.
