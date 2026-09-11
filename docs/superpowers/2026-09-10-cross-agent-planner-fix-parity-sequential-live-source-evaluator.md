# Sequential Live-Agent Evidence: Source Evaluator

Date: 2026-09-10  
Candidate: `ce3a706c2722e2b571262fc58f3a4ecb2a88a829`  
Branch: `codex/cross-agent-planner-fix-parity`  
Tier: `live`, one repetition  
Phase: Task 19 sequential diagnosis/repair loop

## Scope and safety

This was the next user-authorized sequential live-agent run after the
Researcher confirmation. Only Source Evaluator was executed. The existing
`config.yaml` and repository dotenv launcher were used without CLI effort or
budget overrides; global `llm.max_tokens == 4096` remains unchanged. No retry,
other agent, suite command, source/test/case/prompt/config change, or repair
was performed. Credentials and environment values are not recorded here.

## Evidence

- Experiment: `https://eu.smith.langchain.com/o/dec92fa1-b347-483c-8a51-9cd727ee089c/projects/p/71a8f884-597c-4902-b1f2-b4d8f1722a68`
- Repository-relative artifact: `output/evaluations/task19-source-evaluator-ce3a706/source-evaluator/task19-source-evaluator-ce3a706-source-evaluator-live-20260911T012237Z-ce3a706/results.json`
- Artifact SHA-256: `C90E38811DD0BAF616A9A9B409B14E8E6AFC4412952AA9BE7499C2CFA2DA74F9`
- Exit code: `1` (`FAILED`)
- Case/repetition: `source-evaluator-live-ranking`, repetition `1/1`
- Hard gates: `13/14` passed
- Deterministic quality: `0.80`
- Failed gate: `low_confidence_flagged`
- Judge: unscorable; typed `judge_schema_failure` diagnostics at `$` (attempt 1) and `$` plus `rationale` (attempt 2)

The result contains no target-side typed `output_limit` evidence. The failed
gate and bounded gate detail remain in the artifact; this report intentionally
does not copy provider output, prompts, evaluator inputs, or individual source
URLs.

## Diagnosis boundary

The `low_confidence_flagged` failure is a target/evaluator signal requiring
typed diagnosis. It is not yet evidence for a Source Evaluator prompt, model,
budget, or provider repair. The judge schema failure is an independent shared
judge/provider boundary and supplies no quality score. Sol High review is the
next gate before any implementation decision.
