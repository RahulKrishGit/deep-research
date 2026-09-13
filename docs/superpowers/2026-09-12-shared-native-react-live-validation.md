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
unrecognised `ProviderResponseError` to `malformed_envelope`. That catch-all is a real weakness, but it
is **not** why batch 1's runs 24 and 25 could not be separated after the fact: the actual reason is the
v1 **record schema**, which kept only `outcome`, `finish`, `tool`, and `error_type` — no
`failure_category` and no `failure_origin` — so there was nothing on the artifact to separate them by,
whatever the classifier did. (Those two records read
`{"run":24,"outcome":"provider_error","finish":"unknown","tool":null,"error_type":"ProviderResponseError"}`,
and `provider_error` is not in the retained v1 classifier's closed vocabulary, so they were written by
an earlier instrument. The conclusion is the same either way.) v2 records both fields per failure.

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
tools `["web_search","query_memory"]`, repository retry count `5` (the configured value, reported
separately from the probe's own forced override of `0`), SDK `max_retries` `0`, exactly one
SDK `create` call, zero tool executions, and zero LangSmith requests. It exercises no tool execution,
no LangSmith transport, and no live provider, so it says nothing about execution behaviour; the
offline agent-boundary tests are what prove that.

The checked-in instrument is `scripts/native_react_shape_probe.py` (SHA-256 `533252ac…`) with
`tests/test_native_react_shape_probe.py` (SHA-256 `27beb8a2…`).

### Next step

Task 8's live release gate remains **unauthorized and not run**, and Task 9 is not started. A live
batch must be separately stated and explicitly authorized, and it must run the checked-in v2 probe,
not v1.

### Task 7 Step 7 — invariant audit and independent review (performed after this section)

Frozen-invariant verification **was** performed, and an independent review of `89a9089..be92b21` **was**
commissioned. It returned **0 Critical and 1 Important**, with every claimed gate result, hash, byte
count, fingerprint, and count above reproduced exactly, and 16 production mutations all caught.

The Important finding was real and is fixed in `666ad37`: `JudgeFeedback.structured_attempts` was read
as the maximum structured attempt over `output.session_id` on the evaluator's tracker, but that tracker
is **not** dedicated — `cli.py` builds one `Tracker` and hands the same instance to both the target and
the judge, and the judge opens its span on the *target's* session id. A target that used its one repair
was therefore recorded as a Judge repair, which would have failed this plan's own per-agent acceptance
rule ("Judge `structured_attempts == 1`") for a Judge that never repaired. The reading is now scoped to
the metrics the judge call itself appends.

Four minors were fixed in `08b64a3`, one of which uncovered a genuine latent defect: the probe's
dry-run set placeholder credentials in `os.environ` without restoring them, and
`test_the_dry_run_records_exactly_one_sdk_create` had been passing **only** because an earlier test
leaked them — it failed in isolation. The suite no longer depends on test order.

**Superseding counts** (the counts above are the state at `be92b21`, preserved as history): probe tests
**41 passed**, focused selection **94 passed**, full offline suite **2516 passed, 1 deselected**, zero
failures; `ruff` clean. The dry run now publishes `repository_retry_count` as the configured `5`
alongside the probe's own `probe_retry_override` of `0`.

Two residual risks are predeclared for the live gate. `native_text_violation` is content-blind: it
rejects *any* final answer containing a fence anywhere, including a legitimately Markdown-formatted
answer. And the DeepSeek mixed-envelope rule rejects even an innocuous `content` preamble beside
`tool_calls`. Both are deliberate, plan-mandated fail-closed choices, so a miss in Task 8 must be read
as a shape rejection and not as a transport regression.

## Task 8 — native-shape release gate: **FAIL** (first authorized execution)

Authorized batch: agent `critic`, 30 logical requests, SDK ceiling 30, effort `max`, `max_tokens` 32768,
repository retries 0, SDK retries 0, 0 tool executions, 0 Judge requests, 0 LangSmith requests.
Run at commit `dadccb9`, clean tree, probe SHA-256
`4e889b8a34a4edf1aeccb6cbdaecc6621087affc2e8d5f04415d0536b7e36647`, `config.yaml` SHA-256
`659db92ef90f4d58c601b67f394ed441224f5944f799e0b2b132fba60efac2dd`.
Output: `output/transport-probes/native-react-v2/native_react_shape_gate_30.jsonl` (30 records, ignored).

```json
{"agent":"critic","requests":30,"accepted":22,"tool_calls":22,"final_answers":0,"shape_failures":8,"shape_failed_runs":[2,15,16,18,19,23,24,28],"shape_failure_kinds":["local_rejection"],"instrument_errors":0,"provider_failures":0,"provider_failed_runs":[],"provider_failure_kinds":[],"failed_runs":[2,15,16,18,19,23,24,28],"expected_requests":30,"passed":false}
```

**Shape integrity: MISS** — 8 of 30 requests were rejected by the repository's own response parser.
**Operational availability: PASS** — 0 provider failures of any kind (no timeout, rate-limit,
transport, HTTP, generic SDK, output-limit, or instrument failure). Exactly 30 requests were made and
no retry path ran, so the authorized ceiling was respected.

All 8 rejected records are identical in content-free terms: `outcome` `local_rejection`,
`failure_category` `response`, `failure_origin` `local_response`, `retryable` `false`,
`status_code` `null`. Because `failure_category="response"` with `failure_origin="local_response"`
means the repository rejected a response it *received*, the provider transport was healthy — this is a
shape disagreement, not an outage.

**Per the plan, this gate is not rerun, the gate is not relaxed, and no canary starts.** Task 9 is
blocked and none of its authorized requests were spent.

### The verdict is not yet actionable, and that is itself a finding

The instrument records `failure_category` and `failure_origin` but **not which parser rejection
fired**, so eight identical-looking records cannot be attributed to a cause. `build_failure_record`
never reads the exception message, because an *SDK* exception's message can carry provider text — a
correct privacy decision. But the DeepSeek parser's rejection messages are **project-authored
constants** (for example `"DeepSeek native tool response mixed a final answer with a tool call"`) and
carry no provider text at all. Recording a bounded, project-authored rejection *category* would be
privacy-safe and would make this verdict diagnosable.

This matters because the plan's stated diagnosis method — "identify the owning operation and validation
category from content-free telemetry" — is not satisfied by this instrument as built.

### Candidate causes, ranked, with the ownership stated

1. **Mixed envelope: a typed tool call beside non-blank `message.content`.** This rule was *added by
   Task 4* (`ccd152a`), and the independent review predeclared it as a residual risk: it "rejects even
   an innocuous `content` preamble beside `tool_calls`". Before Task 4 the `content` field was ignored
   entirely on the tool-call path. **This is the only candidate that rejects a well-formed call.**
2. **A prohibited-text final answer.** Also added by Task 4, via `native_text_violation`. Would require
   the model to answer with DSML, tool markup, a fence, or a legacy action object.
3. **Blank final answer on `stop`** — a pre-existing rule.
4. **More than one tool call in one turn** — a pre-existing rule.

Cause 1 is the prime suspect and it is a change this remediation introduced, so a production regression
here is this plan's responsibility, not a pre-existing defect. It is not yet proven: no artifact in the
retention set can distinguish these four, and 0 of 30 turns produced a final answer.

A separately authorized diagnostic measurement, under a new experiment name, is required before the
gate can be re-attempted — as the plan's own rule requires.

## Task 8 — attempt 2 after the mixed-envelope relaxation: **FAIL**, and now DIAGNOSED

Authorized by the human as a single re-attempt, taken after relaxing Task 4's mixed-envelope rule
(commit `6b2acef`). New probe SHA-256
`5e5b546d7a6cc7a88c106608c8acfb7639a11119ca07f25340fda56844d09dac`. Output:
`output/transport-probes/native-react-v2/native_react_shape_gate_30_attempt2.jsonl`. The attempt-1 batch
is preserved unmodified beside it.

```json
{"agent":"critic","requests":30,"accepted":26,"tool_calls":26,"final_answers":0,"shape_failures":4,"shape_failed_runs":[9,16,20,27],"shape_failure_kinds":["local_rejection"],"instrument_errors":0,"provider_failures":0,"provider_failed_runs":[],"provider_failure_kinds":[],"failed_runs":[9,16,20,27],"expected_requests":30,"passed":false}
```

**Shape integrity: MISS** (4/30). **Operational availability: PASS** (zero provider failures).
**Verdict: FAIL.**

### The diagnosis, and why it was obtainable this time

The probe now records a bounded, project-authored `rejection_reason`. That field was added at zero
additional request cost specifically so a second failure would name its cause instead of repeating
attempt 1's undiagnosable `local_rejection`. It did:

| run | rejection_reason |
| --- | --- |
| 9, 16, 20, 27 | `call_count_not_one` |

All four are the **pre-existing** rule `"DeepSeek native tool response must carry exactly one tool call"`
— `len(calls) != 1` in `_native_outcome`. This is **not** the rule that was relaxed and **not** a Task 4
change: the exactly-one-call contract predates this entire remediation and is stated in the plan's own
spec ("accepts exactly one typed allow-listed tool call").

**The relaxation was still correct and is kept.** It halved the failure rate (8/30 to 4/30), so the
mixed-envelope rule was a genuine contributor to attempt 1's failure. But it was not the whole cause, and
the residual cause is a different, older rule. Attempt 1's records show `rejection_reason: null` because
that run predates the field — the field was not back-filled, so no value there is invented.

### What this means, stated plainly

The release gate requires **0/30** multiple-call shape failures. With `tool_choice="auto"` and two tools
advertised, the model emits a turn this rule refuses at roughly 13-27% across the two attempts. Exact
zero out of 30 is therefore not achievable against real provider behaviour, and the remaining work is a
decision about the contract itself, not a bug fix:

1. **Accept multiple calls** — execute them in order, or execute the first and record the rest as
   explicitly unexecuted. This changes the documented one-call ReAct contract, so it is a design change,
   not a patch. Silently dropping calls is explicitly forbidden by the plan.
2. **Advertise one tool at a time** so a multi-call turn is impossible — changes what the Critic can do in
   one turn and weakens the tool selection the planner and researcher depend on.
3. **Constrain the provider** to exactly one call — not available: with thinking enabled DeepSeek answers
   HTTP 400 to function-specific and `required` tool choice, which the provider already documents.
4. **Record it as a measured limitation and relax the gate** — contradicts the plan's stated guarantee
   that malformed output never crosses the boundary, since a multi-call turn is a shape this system
   refuses rather than a malformed one.

Not yet distinguished: whether `call_count_not_one` fired on **zero** calls or **two or more**. Given a
`tool_calls` finish reason and a healthy transport, parallel calls (two or more) is the overwhelmingly
likely case, but the record names the rule and not the count. One bounded `call_count` field would settle
it on the next run; it was not added here because observing it requires another paid run.

**No further paid request was made.** Both attempts are preserved, neither is relabelled, and the gate
remains FAIL. Task 9 is still blocked with all 270 pre-authorized requests unspent.
