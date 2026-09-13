# Reviewer prompt — shared native ReAct, Tasks 1–8

Copy everything below the line into the reviewer's session.

---

You are reviewing a completed implementation plus its first paid validation run. Your job is to
**falsify the implementing agent's account**, not to confirm it. You are the adversarial reader.

Repository (read-only for you): `C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.worktrees\cross-agent-planner-fix-parity`, branch `codex/cross-agent-planner-fix-parity`.

## Read these first

- The plan: `docs/superpowers/plans/2026-09-12-shared-native-react-tools-and-prompt-conformance.md`
- The spec: `docs/superpowers/specs/2026-09-12-shared-native-react-tools-and-prompt-conformance-design.md`
- The implementing agent's account: `docs/superpowers/2026-09-12-shared-native-react-observation-report.md`
  — treat every sentence as a claim to be checked
- Evidence: `docs/superpowers/2026-09-08-cross-agent-planner-fix-parity-fix-log.md` sections 96–97;
  `docs/superpowers/2026-09-12-shared-native-react-live-validation.md`
- Rulings ledger: `.superpowers/sdd/2026-09-12-shared-native-react-tools-and-prompt-conformance/progress.md`
- Probe: `output/transport-probes/native-react-v1/native_react_shape_probe.py` and its two `.jsonl` record files

## The questions that matter most

**1. Was the paid shape gate's first FAIL handled correctly, and was the second batch legitimate?**

Batch 1 returned `{"requests":30,"tool_calls":27,"final_answers":0,"malformed":3,"failed_runs":[11,24,25],"passed":false}`.
The plan says of a miss: *"Any miss is FAIL. Do not reinterpret zero as equivalence, rerun the same
batch, or proceed to canaries."*

The agent then changed the probe's failure classifier (batch 1 collapsed every `ProviderError` into one
bucket, so it could not distinguish "malformed envelope" from "transport timeout" — the two are
separate items in the plan's own gate list) and ran a **second 30-request batch**, citing the human
partner's blanket authorization.

Decide, with reasons:

- Was the first run's FAIL correct under the plan's gate, or was some part of it a misapplication?
- Is "the instrument could not measure the gate item" a legitimate reason to run the batch a second
  time, or is it exactly the result-shopping the rule forbids? Quote the plan text you are relying on.
- Does blanket authorization ("authorized to run all the calls, no need for individual authorization
  for each") waive a predeclared *gate*, as opposed to a per-batch *authorization*? The plan treats
  these as different things.
- If you judge the second batch illegitimate, say what the correct action was instead.

**2. Does the observed data support or undermine the plan's premise?**

The plan exists because the old prompt-encoded protocol produced DSML-as-text on 16/30 measured first
attempts. Batch 1 showed 0 DSML, 0 fenced/JSON action envelopes, 0 unknown or multiple calls, and 27/30
clean native calls with arguments decoding to JSON objects.

- Is 27/30 native calls with 0 malformed shapes genuinely consistent with "the defect is fixed", or can
  you construct a reading where it is not?
- Is 3/30 provider failures (1 timeout, 2 unclassified) within normal variance for this provider at
  thinking/effort `max`, or is it a signal? Read the probe's classification code and the retained
  records — do not accept "ambiguous" as an answer without checking whether more could have been
  retained without violating the no-content rule.
- The probe deliberately retains no provider text. Was that the right trade for a *shape* gate? Could the
  agent have retained the failure taxonomy in batch 1 and chosen not to?

**3. Is the probe itself sound?**

- Does `--execute --requests 30` really make exactly 30 provider HTTP requests? Check how
  `retry_count=0` is applied and whether anything else can multiply requests (the repo default is 5).
- Does it call only the reviewed public `complete_react`, and copy no private provider serialization?
- Does `_gate` implement the plan's predeclared gate items faithfully? In particular: it now reports
  `shape_failures` and `provider_failures` separately. Does it still fail on **both**? A change that
  made the gate easier to pass would be a serious finding.
- Does anything in the probe retain content, arguments, reasoning, prompts, or secrets — in the JSONL,
  in stdout, or in span metadata?
- The booby-trapped tool clients are meant to prove no tool executed. Is that proof real?

**4. Verify the code claims against the code, not the report.**

- Do `_fresh_provider_error` (both providers) and the `with_retries` chain detach actually prevent the
  raw response from being reachable from a public error's `args`/`__dict__`/`__cause__`/`__context__`
  and from `f_locals` of every traceback frame under `src/deep_research/providers/`? The scoped
  re-review found the chain fix leaves the SDK exception reachable via the *translator's* frame local
  and the agent narrowed the docstring rather than closing it — decide whether that residual is
  acceptable, and whether the docstring is now accurate.
- Is the fail-closed rule restored on the `stop` path for a present-but-non-sequence `tool_calls`? The
  agent introduced and then fixed a regression here.
- Do the "weighty" assertions actually carry weight? Specifically
  `tests/test_agents/test_native_react_boundary.py`'s `ReActDecision` assertion,
  `tests/test_evaluation/test_judging.py`'s `react_calls == []`, the OpenAI chain sentinel, and the
  OpenAI `request_attempt` case. Earlier rounds found three inert assertions in this work; look for
  more rather than assuming the pattern stopped.
- Reproduce the fingerprint tables in fix-log section 96 independently (both columns).

**5. Judge independence and blast radius.**

- Confirm `judge_prompt_fingerprint(rubric_version=1) == "74b9cddfbbee"` and that no Judge file changed.
- Confirm the seven standing test failures are genuinely pre-existing and identical in name before and
  after (base `4757988`: 7 failed / 2117 passed; head: 7 failed / 2268 passed). Do not accept a
  count-only comparison.
- Check whether anything in this change could alter production behaviour beyond tool selection: retry
  semantics, error taxonomy, budgets, routing, thresholds, or the structured (non-ReAct) paths.

## Rules

- Read-only: do not mutate the working tree, index, HEAD, or the branch.
- Do not re-run the paid probe. Re-run ordinary tests as needed; `tests/test_evaluation/*` needs
  `DEEPSEEK_API_KEY` present (any placeholder works — nothing leaves the process).
- Do not accept a claim because the report asserts it, and do not accept a finding because it is
  labelled Minor.
- Where you disagree with a ruling the agent recorded, say so explicitly and name the ruling.

## Output

1. **Verdict on the gate decision** — was batch 1's FAIL correct, and was running batch 2 legitimate?
   Yes/no with the plan text you relied on.
2. **Findings** — Critical / Important / Minor, each with `file:line` or the exact command and its output.
3. **Claims I could not verify** — and what would settle each.
4. **Are the six canaries safe to authorize?** Say yes only if you would stake your own name on it.
