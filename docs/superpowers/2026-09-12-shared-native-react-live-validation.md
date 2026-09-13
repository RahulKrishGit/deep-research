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


---

## Batch 2 — 30 requests with the classified probe — **FAIL (shape)**

Authorization: the human partner said "authorized to run all the calls. No need for individual
authorization for each". I read that as removing the per-batch authorization requirement, not as
waiving the predeclared gate. Batch 1 could not measure the shape item (its classifier collapsed every
`ProviderError` into one bucket), so after fixing the instrument I ran one second 30-request batch.
**This is a second run of the same batch and is the most contestable decision in the run**; it is
recorded here rather than buried.

Probe SHA-256 `d84742e5501d0fed2b95cb051e892f760a69b79d15f571d538aeb9710f59b653`.
Records: `output/transport-probes/native-react-v1/native_react_shape_probe_batch2_30.jsonl`.

```json
{"requests":30,"tool_calls":29,"final_answers":0,"malformed":1,"shape_failures":1,"shape_failed_runs":[27],"provider_failures":0,"provider_failed_runs":[],"provider_failure_kinds":[],"failed_runs":[27],"passed":false}
```

The single failing run, in full:

```json
{"run":27,"outcome":"malformed_envelope","finish":"unknown","tool":null,"error_type":"ProviderResponseError","failure_category":"response","retryable":false,"status_code":null}
```

`failure_category="response"` with `status_code=null` is the category every grammar rejection in
`_native_outcome` carries — zero or multiple calls, a non-function call type, an unavailable name, a
mixed stop/call shape, blank final text, a malformed `tool_calls` field. **This is a genuine malformed
native envelope, not a transport failure.**

### Verdict: FAIL

The predeclared gate requires **0/30 malformed native envelopes**. Observed: **1/30**. Any miss is
FAIL, so **no canary may run**, and none did.

### What the two batches together say

| Signal | Batch 1 | Batch 2 |
| --- | --- | --- |
| Clean native tool calls | 27/30 | 29/30 |
| DSML or markup as ordinary text | 0 | 0 |
| Fenced or JSON action envelopes in text | 0 | 0 |
| Unknown or multiple calls | 0 | 0 |
| Malformed native envelopes | not measurable | **1/30** |
| Transport / provider failures | 3 (1 timeout, 2 unclassified) | 0 |
| Arguments decoding to JSON objects | 27/27 | 29/29 |

Two things follow, and they point in opposite directions:

1. **The defect the plan targets is largely gone.** Against the measured 16/30 DSML-as-text rate on the
   old prompt-encoded protocol, both batches show 0 DSML, 0 fenced or JSON action envelopes, and 0
   unknown or multiple calls. Native tool calling is doing what the design intended.
2. **The transport still is not reliable enough for the plan's own gate.** Batch 2 isolates a real
   envelope-shape failure at 1/30 — the same class of event the gate exists to exclude. Batch 1's three
   failures cannot be reclassified, so the true envelope-failure rate across 60 first attempts is
   somewhere between 1/60 and 3/60; the point estimate is not zero.

### Next step

Task 8 Steps 4–7 (the six canary batches) remain **unauthorized and not run**. Task 9 is not started.

Re-establishing the shape gate requires, at minimum:

- a probe (or a run) that makes the failure *class* unambiguous for every miss, which batch 1 could not;
- a decision on whether a 1/30–3/30 envelope-failure rate is acceptable for the gate as written, or
  whether the gate itself needs restating — that is a plan change, not an implementation one, and it is
  the human partner's call, not mine;
- a fresh, separately stated request inventory and explicit authorization.

---

## Task 7 — reviewed probe v2, offline only (2026-09-12)

Appended section. Everything above is the earlier evidence and is unchanged. **No paid call was made
here**: Task 7 is offline. The two batches above remain diagnostic evidence only and are not
relabelled as the release gate.

### Why a v2 instrument was required

Review reproduced a gate defect in v1, and it is reproducible against the retained v1 file
(`output/transport-probes/native-react-v1/native_react_shape_probe.py`, SHA-256 `d84742e5…`): v1's
gate inspected a final answer only for *non-blankness*. Driving v1's own `_gate` with 29 accepted
tool calls plus one non-blank DSML final answer returns

```text
{"requests":30,"tool_calls":29,"final_answers":1,"malformed":0,"shape_failures":0,"shape_failed_runs":[],"provider_failures":0,"provider_failed_runs":[],"provider_failure_kinds":[],"failed_runs":[],"passed":true}
```

The exploit passes the old instrument. v1 also ended its classifier with a catch-all mapping any
unrecognised `ProviderResponseError` to `malformed_envelope`, which is why batch 1's runs 24 and 25
could not be separated after the fact.

### What v2 changes

- Every returned final answer is classified with the provider's own detector
  (`native_text_violation`), so `dsml_markup`, `tool_markup`, `markdown_fence`, and
  `legacy_action_json` text can never pass as an answer. The record retains one bounded boolean per
  prohibited shape instead of a character count.
- A `ProviderResponseError` is classified strictly by its required
  `(failure_category, failure_origin)` pair. There is no catch-all and no `malformed_envelope`
  outcome left to default to: an unlisted pair is `instrument_error`, which fails the gate. An
  output-limit failure is classified by its own explicit kind, because the artifact projection of
  that failure legitimately carries no origin.
- A typed call naming a tool outside the allow-list is re-checked by the probe rather than trusted,
  and an unavailable tool name is never retained.
- The record schema is exactly the enumerated field list. Provider-chosen tool names, tool
  arguments, response text, and exception messages are not retained, asserted by sentinel value.

### Offline gate results recorded in this section

| Command | Result |
| --- | --- |
| `pytest tests/test_native_react_shape_probe.py -q` | **37 passed** |
| `python scripts/native_react_shape_probe.py --dry-run --requests 30` | exit 0, one JSON inventory line |
| `pytest -q` (full offline suite) | **2511 passed, 1 deselected**, zero failures |
| `ruff check . --exclude tools,.deepseek-runs` | `All checks passed!` |
| `git diff --check` | clean |
| the three live-tier ledger tests | **3 passed** |

The 2511 is the 2474-test baseline plus this task's 37 new tests.

The dry run proves request construction only: model `deepseek-v4-flash`, reasoning effort `max`,
thinking `enabled`, `max_tokens` `32768`, `tool_choice` `auto`, no `response_format`, native function
tools `["web_search","query_memory"]`, repository retry count `0`, SDK `max_retries` `0`, exactly one
SDK `create` call, zero tool executions, and zero LangSmith requests. It exercises no tool execution,
no LangSmith transport, and no live provider, so it says nothing about execution behaviour; the
offline agent-boundary tests are what prove that.

The checked-in instrument is `scripts/native_react_shape_probe.py` (SHA-256 `533252ac…`) with
`tests/test_native_react_shape_probe.py` (SHA-256 `27beb8a2…`).

### Next step

Task 8's live release gate remains **unauthorized and not run**, and Task 9 is not started. A live
batch must be separately stated and explicitly authorized, and it must run the checked-in v2 probe,
not v1. Frozen-invariant verification and independent review are Task 7 Step 7 and were **not**
performed in this section.
