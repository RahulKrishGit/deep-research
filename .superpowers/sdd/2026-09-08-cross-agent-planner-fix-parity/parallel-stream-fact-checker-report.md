# Parallel Stream F — Fact Checker Report

Date: 2026-09-10
Requested branch: `codex/cross-agent-planner-fix-parity`
Workspace state: detached `HEAD` at `69803d9b302d24b0d536532dadedb094f5f0d30c`; the requested branch ref points to the same commit.
Scope: offline typed diagnosis only.

## Decision

No production change is justified. The existing Fact Checker behavior already
has the conservative fallback required by the brief. The only code change in
this stream is the requested characterization test in
`tests/test_agents/test_fact_checker.py`.

The test uses `_output_limit_error()` as a synthetic ReAct decision failure and
confirms that the typed fallback is local to the affected claim. It does not
authorize a token, retry, iteration, prompt, or budget change, and it does not
weaken `insufficient_evidence` semantics.

## Evidence limits and preserved live result

The preserved Fact Checker JSON artifact named by the sequential evidence
document is not present in this worktree. The diagnosis therefore uses the
tracked typed evidence in:

- `docs/superpowers/2026-09-10-cross-agent-planner-fix-parity-sequential-live-fact-checker.md`
- `docs/superpowers/2026-09-10-cross-agent-planner-fix-parity-live-evaluation.md`
- the Fact Checker source, controlled cases, and offline tests

The latest preserved typed live result records:

- hard gates: `15/15`
- deterministic quality: `1.00`
- all four deterministic metrics: `1.00`
- judge: `0.3675`
- aggregate: `0.6205`
- runner status: `FAILED` against the configured quality threshold
- fallback diagnostic: `{kind: output_limit, operation: react_decision}`
- no top-level target-side typed `output_limit` failure

The fallback diagnostic is therefore evidence that the fallback provider path
was exercised, not evidence that the Fact Checker target contract failed.

## Claim, verdict, confidence, and independent-domain matrix

The following matrix combines the typed controlled-case contracts with the
typed implementation rules. It does not infer raw provider text or invent
per-claim live output that is not preserved in this checkout.

| Typed trajectory | Required/recorded verdict | Confidence rule | Independent-domain evidence | Diagnosis |
| --- | --- | --- | --- | --- |
| Mixed case: corroborated claim | `verified` | At least `0.5` | Two independent domains, `nrc.gov` and `ans.org` | Valid positive verification path. |
| Mixed case: contradicted claim | `contradicted` | Clamped model confidence; contradictions override the model verdict | Independent `nei.org` result | Valid contradiction path. |
| Mixed case: thin claim | `insufficient_evidence` | Exactly `0.0` | No retrieved independent domain | Conservative no-evidence path. |
| Independent-domain case | Must not be `verified` when the evidence remains within one publisher family | No invented confidence for the insufficient path | Same-family `news.example.com` pages collapse to one domain family | Independence guard is working. |
| Search-failure case | `insufficient_evidence` or `unverified`, never `verified` for the failed claim | `insufficient_evidence` is zero-confidence | Failed search supplies no independent evidence; the later claim can use `agu.org` and `gcos.wmo.int` | Recoverable search failure remains conservative while later work can succeed. |
| Latest preserved live result | All deterministic target metrics score `1.00` | `confidence_calibrated=1.00` | `independence_enforced=1.00` | Target gates pass; the low judge/aggregate score is not a typed target-contract failure. |

## Fallback trajectory matrix

| Stage | Typed input | Typed output | Required next action |
| --- | --- | --- | --- |
| ReAct decision | `ProviderOutputLimitError`; configured cap `4096`; request attempt `1` | `ReActRun.stop_reason="provider_error"`; `agent_provider_error`; details `operation="react_decision"`, provider kind `output_limit`; no ReAct step | Stop the non-recoverable loop. |
| Affected claim | Failed ReAct run | `Claim.verdict="insufficient_evidence"`, `confidence=0.0`, empty evidence and contradictions; event reason `loop_failed` | Preserve the claim as unverified rather than asking for a fabricated verdict. |
| Claim loop | First claim is recorded; run is unsuccessful | Outer Fact Checker loop breaks before later claims | Do not blindly attempt the second claim. |
| Provider budget | Synthetic completer records the calls | `budgets == [None, None]`; no per-call max-token override | Leave the global `4096` cap unchanged. |

## RED/GREEN result

The characterization test was added before any production edit and passed on its
first run:

```text
1 passed, 40 deselected
```

That is a characterization GREEN, not a production-fix GREEN. No genuine RED
reproducing a typed target contract violation was observed, so the conditional
production-edit gate was not met.

The focused Fact Checker test suite subsequently passed with `41 passed`, and
the Fact Checker case tests passed with `39 passed` and one unrelated
third-party deprecation warning.

## Verification

The requested offline checks passed:

- `python -m pytest tests/test_agents/test_fact_checker.py -q` — `41 passed`
- `python -m pytest tests/test_evaluation/test_cases_fact_checker.py -q` — `39 passed`, one deprecation warning
- combined focused run — `80 passed`, one deprecation warning
- full offline suite — `1995 passed, 1 deselected`, two unrelated third-party warnings
- Ruff on the three scoped files — `All checks passed!`
- `git diff --check` — exit code `0`

The first full-suite attempt collected five import errors because the Python
editable installation still pointed at the unrelated `streamlit-ui-polish`
worktree. Reinstalling this checkout with `python -m pip install -e ".[dev]"`
fixed that environment pointer; no repository files were changed by the
repair.

## Remaining concern/blocker

The latest preserved live run remains below the quality threshold because the
judge scored `0.3675` and the aggregate was `0.6205`, despite all typed target
gates and deterministic metrics passing. The permitted typed evidence does not
localize that quality shortfall to `fact_checker.py`. The missing raw JSON
artifact also prevents any finer-grained live claim analysis in this worktree.

No token increase, retry/iteration change, prompt tuning, top-level
`output_limit` reclassification, or relaxation of conservative
`insufficient_evidence` semantics is justified by this stream.
