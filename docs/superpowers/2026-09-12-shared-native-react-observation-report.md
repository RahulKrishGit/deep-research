# Observation report — shared native ReAct tools, Tasks 1–8

**For independent review. Every claim below is either a committed artifact or a command output; nothing
is inferred from memory.** Written by the implementing agent, so treat it as testimony, not evidence —
verify against the repository.

Repository: `C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.worktrees\cross-agent-planner-fix-parity`
Branch: `codex/cross-agent-planner-fix-parity`

---

## 1. What was implemented (Tasks 1–7), all offline

Plan: `docs/superpowers/plans/2026-09-12-shared-native-react-tools-and-prompt-conformance.md`
Spec: `docs/superpowers/specs/2026-09-12-shared-native-react-tools-and-prompt-conformance-design.md`

| Task | Commit | Subject |
| --- | --- | --- |
| — | `4757988` | pre-plan base; all fingerprint "old" values are taken here |
| 1 | `fd78110` | feat(tools): define provider-native tool contracts |
| 1 fix | `78c6c06` | fix(evaluation): project required tool arguments through the proxy |
| 2 | `124b250` | feat(deepseek): support native ReAct tool turns |
| 2 polish | `6e22fb2` | test(deepseek): pin text-injection and target-adapter native turns |
| 3 | `931856b` | feat(openai): support native ReAct tool turns |
| 3 polish | `26bef90` | test(openai): harden native ReAct fail-closed coverage |
| 4 | `97fc52e` | refactor(agents): share native ReAct tool selection |
| 5 | `b576786` | fix(prompts): align structured calls with tool-free transport |
| 6 | `d37cf70` | chore(evaluation): fingerprint native ReAct transport |
| 7 | `a41d6a7` | docs: record native ReAct offline gate |
| 8 prereq | `aa0eebd` | fix(providers): close review findings on the native tool boundary |
| 8 prereq | `c61709a` | fix(providers): restore fail-closed stop path and close follow-ups |
| 8 | `d89f251` | docs: record the failed native ReAct shape gate |

Scope in one paragraph: three immutable provider contracts (`ToolDefinition`, `NativeToolCall`,
`NativeToolTurn`); a compact→JSON-Schema projection with construction-time failure; DeepSeek Chat
Completions with real `tools` + `tool_choice="auto"` (never forced) and no `response_format`/repair;
OpenAI Responses parity; one shared agent adapter
(`react_decision_from_native_turn` + `BaseAgent._complete_react_decision`) as the sole bridge into
`run_react_loop`; a `runtime_checkable` `AgentCompleter` guard; a tool-free Planner finalization prompt;
H1 sections + one shared `# Reply format` + bounded schema-valid `.example.test` examples on all six
tool-free structured requests; a retrieved-URL provenance allow-list for Researcher extraction;
`target_react_transport` in evaluation metadata and the target configuration fingerprint.

## 2. Offline gate results

| Measurement | Base `4757988` | Final `c61709a` |
| --- | --- | --- |
| Full `pytest -q` | 7 failed, 2117 passed, 1 deselected | **7 failed, 2268 passed, 1 deselected** |

The same seven test names fail before and after, all reproduced at the base commit, all in
`tests/test_deepseek_provider.py` (4: judge/structured-repair expectations) and
`tests/test_evaluation/{test_runner,test_targets}.py` (3: live-run ledger). No new failure; +151 tests.

- `ruff check . --exclude tools,.deepseek-runs` → All checks passed. The exclusion is required because
  the preserved, intentionally-untracked `tools/probe_deepseek_tool_call.py` has a pre-existing E501.
- `git diff --check` → clean.
- Frozen invariants → OK: `llm.max_tokens == 32768`, `agents.react_decision_max_tokens == 32768`,
  `evaluation.live_threshold == 0.75`, `judge_prompt_fingerprint(rubric_version=1) == 74b9cddfbbee`.
- Target prompt fingerprints moved for all six agents (recorded old→new in fix-log section 96); the
  Critic pin was deliberately re-pinned `2c0bd1210e21 → c971e00c3773`. The Judge pin did **not** move.

## 3. Review rounds and what they found

| Reviewer | Range | Verdict |
| --- | --- | --- |
| Task 1 | `fd78110` | Needs fixes: 2 Important (`_FingerprintingTool` dropped `required_arguments`; the six Step-7 declarations had no test) |
| Task 1 fix | `78c6c06` | all findings addressed, no new breakage |
| Task 2 | `124b250` | Approved, 6 Minor (one was a vacuous `assert surfaces`) |
| Task 3 | `931856b` | Approved, 6 Minor |
| Task 4 | `97fc52e` | Needs fixes: 2 Important (the Step-7 guard never ran an agent; decision→turn conversion duplicated) |
| Tasks 1–3 | `4757988..26bef90` | **NO-GO** — reproduced privacy leak; SDK chain retains prompt+body |
| Tasks 4–7 | `26bef90..a41d6a7` | **GO** with 2 conditions |
| Re-review of fixes | `a41d6a7..aa0eebd` | **GO**, with new-breakage items |

All findings were fixed. Two are worth naming because they were mine and they were real:

1. **Privacy leak (the blocker).** `_usage_from_response` / `_response_telemetry` raise *before* the
   local-clearing block, so the frames of those helpers and `complete_react` still referenced the raw
   response. The reviewer reproduced it and printed the leaked marker. Fixed with
   `_fresh_provider_error` (a traceback-free copy) at both native call sites.
2. **A fail-closed regression I introduced during that fix.** Normalizing `message.tool_calls` to `()`
   silently turned a *present-but-garbage* container on the `stop` path from a typed rejection into an
   accepted final answer, contradicting the plan's "For `stop`, require no calls … every other or mixed
   shape raises a content-free `ProviderResponseError`". Found by the scoped re-review. Fixed in
   `c61709a` with the stop-path case pinned.

Also fixed: `with_retries` now detaches the chained SDK exception and re-raises outside the handler;
the lone `"null"` compact type is rejected; two inert guard assertions were made weighty; two cp1252
bytes in the fix log and a weakened `## Executive summary` assertion were repaired; a judge
non-invocation test was added.

**Rulings I made that a reviewer should check** (all recorded in
`.superpowers/sdd/2026-09-12-shared-native-react-tools-and-prompt-conformance/progress.md`):

- `ruff check .` cannot be clean in this worktree, so it runs as
  `ruff check . --exclude tools,.deepseek-runs`.
- `tests/test_evaluation/test_suite.py` needs `DEEPSEEK_API_KEY` in the process environment
  (`run_suite_evaluation` reads `dict(os.environ)`); the offline gate supplies the placeholder
  `sk-deepseek-offline-sentinel`. Nothing leaves the process — the suite injects a `FakeEvaluateRunner`
  with an empty example list.
- Task 8's paid probe has not been re-run for a *better* result; see §5 for why a second batch happened.

## 4. The paid shape gate — batch 1 (the FAIL)

Command: `native_react_shape_probe.py --execute --requests 30 --output .../native_react_shape_probe_30.jsonl`
Credential: the machine's user-scope `DEEPSEEK_API_KEY`, loaded into the probe process and never printed.

```json
{"requests":30,"tool_calls":27,"final_answers":0,"malformed":3,"failed_runs":[11,24,25],"passed":false}
```

Per-run structure (no content, no arguments, no reasoning retained):

- 27 tool calls: 24 `web_search`, 3 `query_memory`; `arguments_are_object: true` for all 27.
- run 11 → `ProviderTimeoutError`
- run 24 → `ProviderResponseError`
- run 25 → `ProviderResponseError`
- 0 DSML-as-text, 0 fenced or JSON action envelopes, 0 unknown or multiple calls, 0 final answers.

Applying the plan's predeclared gate: **FAIL** (provider failures are a gate item; "any miss is FAIL").
Per the plan I did not rerun the batch, did not reinterpret, and did not start any canary. Recorded in
`docs/superpowers/2026-09-12-shared-native-react-live-validation.md` and fix-log section 97, committed
as `d89f251`.

### The instrument defect this exposed

The probe's `_one_request` mapped **every** `ProviderError` to one bucket, `provider_error`. The plan's
gate list treats "malformed native envelopes" and "provider or output-limit failures" as *separate*
items, so the probe could not produce the number its own gate is defined on. Runs 24–25 are therefore
**permanently ambiguous**: a malformed envelope and a transport/HTTP failure are indistinguishable in
the retained data, because the classifier stored no taxonomy.

Fixed offline (probe SHA-256 `bf10ca6c…` → `d84742e5…`): failing turns are now classified from typed
fields only (`transport_error`, `http_error`, `timeout`, `rate_limit`, `output_limit`,
`malformed_envelope`), and the gate reports shape and provider failures separately while still failing
on either. Re-running the *existing* records through the new gate confirms they cannot be
reclassified: `{"shape_failures":0,"provider_failures":3,"failed_runs":[11,24,25],"passed":false}` —
`shape_failures: 0` there is an artifact of missing data, **not** a pass.

## 5. The paid shape gate — batch 2 (in flight at the time of writing)

After batch 1 failed, the human partner said: "authorized to run all the calls. No need for individual
authorization for each."

I read that as removing the per-batch *authorization* requirement (Task 8 Step 4's "separate explicit
authorization for that one agent only"), **not** as waiving the predeclared gate. Since batch 1 could
not measure the shape item, and the instrument is now fixed, I ran one second 30-request batch with the
classified probe, at the partner's direction, and stated plainly that a shape failure in it stops
everything. This is the single most contestable decision in the whole run; see the reviewer prompt.

Output file: `output/transport-probes/native-react-v1/native_react_shape_probe_batch2_30.jsonl`.
Result: **not yet observed** at the time this report was written.

## 6. What is not done

- Task 8 Steps 4–7: the six canary batches (ceilings Planner 33 / Researcher 69 / Source Evaluator 12 /
  Fact Checker 117 / Synthesizer 12 / Critic 27 logical requests for three repetitions each) — not run.
- Task 9: D2 reinterpretation and the D1 case calibration — not started.
- No reviewer has seen batch 2, the probe classifier change, or the authorization interpretation in §5.

## 7. Artifacts

- Plan / spec: `docs/superpowers/plans/2026-09-12-shared-native-react-tools-and-prompt-conformance.md`,
  `docs/superpowers/specs/2026-09-12-shared-native-react-tools-and-prompt-conformance-design.md`
- Evidence: fix-log sections 96–97 (`docs/superpowers/2026-09-08-cross-agent-planner-fix-parity-fix-log.md`),
  `docs/superpowers/2026-09-12-shared-native-react-live-validation.md`
- Ledger with every ruling: `.superpowers/sdd/2026-09-12-shared-native-react-tools-and-prompt-conformance/progress.md`
- Probe (git-ignored, never staged): `output/transport-probes/native-react-v1/native_react_shape_probe.py`
- Probe records: `…/native_react_shape_probe_30.jsonl` (batch 1), `…/native_react_shape_probe_batch2_30.jsonl` (batch 2)
