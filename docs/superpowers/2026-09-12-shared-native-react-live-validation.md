# Shared Native ReAct — Live Validation

**Status: the shape gate FAILED. No canary is authorized or run.**

Plan: `docs/superpowers/plans/2026-09-12-shared-native-react-tools-and-prompt-conformance.md`, Task 8.
Evidence base: `docs/superpowers/2026-09-08-cross-agent-planner-fix-parity-fix-log.md`.

## Preconditions

| Item | Value |
| --- | --- |
| Branch | `codex/cross-agent-planner-fix-parity` |
| Reviewed candidate head | `c61709a` (`fix(providers): restore fail-closed stop path and close follow-ups`) |
| Dirty state | only the preserved, intentionally-untracked `.deepseek-runs/` and `tools/` |
| Import | resolves inside the worktree |
| Registered agents | planner, researcher, source-evaluator, fact-checker, synthesizer, critic |
| DeepSeek target | `deepseek-v4-flash`, thinking enabled, effort `max`, `max_tokens=32768` |
| Judge | not invoked by this probe |
| Retry policy on the probe path | `retry_count=0`, so one logical request = one HTTP request |

## Probe

`output/transport-probes/native-react-v1/native_react_shape_probe.py` (git-ignored; never staged).
SHA-256 `bf10ca6ce8403e7c86cc7b8040e23bfcf3c25acb6338b30def8197f949922150`.

It builds the registered `critic-live-review` case through the repository's own helpers
(`cases_for`, `EvaluationCase.fresh_state`, `CriticAgent.build_task`, `render_react_messages`,
`AgentToolset.provider_definitions`) and calls only the reviewed public
`DeepSeekSchemaChatProvider.complete_react`. Both tool clients are booby-trapped, so any tool
execution would fail the run.

**Process deviation, recorded:** the plan asks for this document to exist *before* paid
authorization. It was written *after* the run, in the same session, with no paid call in between.
The inventory itself was presented and the dry run was executed before the paid call.

`--dry-run --requests 30` printed exactly:

```json
{"agent":"critic","logical_requests":30,"provider_http_request_ceiling":30,"tool_execution_requests":0,"langsmith_requests":0,"effort":"max","max_tokens":32768,"tool_choice":"auto"}
```

## Authorized batch

30 sequential first-attempt DeepSeek Chat Completions calls. No Judge, no LangSmith, no Tavily, no
tool execution, no repair. Exactly 30 provider requests were made; the ceiling was 30.

## Result: FAIL

```json
{"requests":30,"tool_calls":27,"final_answers":0,"malformed":3,"failed_runs":[11,24,25],"passed":false}
```

Structural records only (run number, outcome class, finish category, selected allow-listed tool,
whether arguments decoded to an object, usage, character counts) — never content or arguments.

| Predeclared gate item | Observed | Verdict |
| --- | --- | --- |
| 0/30 DSML or other tool markup in ordinary text | 0 | pass |
| 0/30 fenced or JSON action envelopes in ordinary text | 0 | pass |
| 0/30 malformed native envelopes | up to 3 (see below) | **miss** |
| 0/30 unknown or multiple calls | 0 | pass |
| 0/30 provider or output-limit failures | 3 | **miss** |
| Every call one allow-listed native call or one non-blank final answer | 27/30 | **miss** |
| At least one native tool call observed | 27 | pass |
| Exactly 30 provider requests, zero repairs | 30 / 0 | pass |

Failing runs, by structure:

- run 11 — `ProviderTimeoutError` (transport timeout; not a shape signal, but the gate counts
  provider failures)
- run 24 — `ProviderResponseError`
- run 25 — `ProviderResponseError`

The classifier collapsed every `ProviderError` into one bucket, and the probe deliberately retains
no provider text, so runs 24 and 25 cannot be separated after the fact into "malformed envelope"
versus "transport/HTTP failure". The distinction matters for the diagnosis and does not change the
verdict.

The 27 successful turns were all real native calls — 24 `web_search`, 3 `query_memory` — with
arguments decoding to JSON objects in every case.

## What this does and does not tell us

- It does **not** show the DSML-as-text defect. Zero DSML, zero fenced or JSON action envelopes,
  zero unknown or multiple calls, zero final-answer turns.
- It **does** fail the predeclared gate, on 3 of 30 provider-level errors. Per the plan, any miss is
  FAIL: the result is not to be reinterpreted as equivalence, the batch is not to be rerun, and no
  canary may proceed on it.

## Next step

Task 8 Step 4 (the six canary batches) is **not** authorized and must not begin. The shape gate has
to be re-established first; that requires a fresh, separately stated request inventory and explicit
authorization, and a diagnosis that separates envelope shape from transport failure — which the
current probe cannot do because it does not distinguish those error classes.
