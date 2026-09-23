# Report quality from the live audit — implementation plan

## Goal

The CLI must produce a great report for the audit question:

> How much grid-scale battery storage capacity was added in the United States in
> 2024, and what do the latest forecasts project for 2025?

"Great" means all of the following, judged on a live run by an independent audit:

1. The report answers both halves. The 2024 addition is stated with its vintage
   (EIA: 10.4 GW, January-2025 inventory; 10.3 GW, December-2024 inventory, and
   why they differ). The 2025 projection is the *latest* one the evidence carries
   (EIA's January-2025 inventory gives 19.6 GW; the Feb-2025 18.2 GW is older).
   Because the clock is past 2025, the report also states the 2025 outturn the
   evidence carries.
2. Every load-bearing figure traces to a cited passage that actually contains it,
   with unit and date. Nothing is stated more firmly than its source ("could"
   stays "could").
3. The run's own accounting agrees with the content. Claims supported verbatim
   by their sources are not "not established". Answered targets reflect what the
   report answers. Badges are true to the evidence.
4. The reader report itself discloses the run's status, the critic and review
   outcome, coverage and gate failures, and it ranks limitations by consequence.
5. The three artifacts (report, ledger, quality record) agree with each other.

## Evidence

The diagnosis is the audit of live run `23a5f3621d2348bd8ec585694e318a87` at
`9258e7a`, stored as the subagent result `agent://LiveRunAudit`. Read it in full;
every finding there has file:line evidence and a replay command. Finding ids
below (#N) refer to it. The run's published artifacts are
`output/report-23a5f3621d2348bd8ec585694e318a87-0{.md,-evidence.md,-quality.json}`,
and its log is `output/live-proof/audit-1/cli.log`. The trace dump the auditor
made is in `%TEMP%/liverunaudit/runs.jsonl`, with a `git archive` of `9258e7a`
beside it. Use it for replays; never commit it.

## Slices

Each slice runs in its own worktree on its own branch, cut from the branch head.
The integration owner merges them. Slices do NOT re-pin prompt fingerprints in
`tests/test_evaluation/test_config.py`: the integration owner does that once,
after the merge. Deselect or ignore the fingerprint-pin failures your change
causes, and list them in your report.

### T1: evidence visibility (P0 #1, P1 #7, P2 #10)

Files: `agents/acquisition.py` (web-read passage shape), `agents/fact_checker.py`
(packet rendering, verdict reasons, badges), `agents/source_evaluator.py`
(excerpt), plus helpers they need.

1. A web read is no longer one passage. Split its body into bounded paragraph
   passages (`chunk-0..N`) so passage selection and packets can address the
   sentence that carries a figure. Read identity for a complete read is its
   body hash and must not change.
2. The adjudication packet shows each claim the passages that bear on it, not a
   page's first ≤1000 characters. Any passage shown only in part counts as
   partially shown. `no_complete_support` must never be emitted when the
   supporting text may lie in unshown text; record it like `deferred_capacity`
   instead.
3. The source evaluator judges on the relevant passages, not a 400-character head.
4. `source_supported` needs a complete supporting passage. The "primary" badge
   needs that passage to come from the claim's issuer. A relay supports as a
   relay.
5. Within one fact-checker pass, a URL that was refused or failed is not
   requested again for another claim.

Acceptance: replay the run's stored evidence units and packet ids (auditor
commands C9 and C12). Claims 3, 4, 6, 7, 10 and 12 now see their figures; the
verdicts of the supported ones are no longer `no_complete_support`. Regression
tests use small fixtures built from the public EIA pages' text, with navigation
ahead of the figure.

### T2: plan answerability and claim-to-target binding (P0 #2, P0 #3)

Files: `agents/planner.py`, `agents/claim_clusters.py`.

1. Attribution recognizes subject-verb forms: "EIA reported/stated/said/
   estimated/forecast/expects/projected/found that …". It also recognizes
   "according to its <document>" when the issuer is named in the same claim.
   It stays deterministic and still rejects a bare pronoun with no named issuer.
2. A qualitative `measure:` dimension does not demand a numeric value.
3. The planner emits metadata dimensions (publication date, rating basis, …)
   only when the question asks for them. It does not demand content the question
   did not ask for (MWh, a second publisher, a specific series).
4. Support policy follows the evidence's nature. A statistic with a single
   authoritative issuer uses `primary_attribution`; `independent_pair` stays for
   claims where independent measurement exists.
5. Reconcile `PLAN_INSTRUCTION` and the one-shot example with the review's
   rules: no mandatory "benefits and risks", no compound target in the example.
6. At plan time, check every target against a template answer via
   `atom_answers_target`. A target that no claim could ever satisfy is an
   advisory plan problem (repaired once, recorded if it survives), never fatal.

Acceptance: the auditor's replays C8, C10 and C11 at the new head. "EIA
reported that … 10.4 GW …" binds to the 2024-addition target. Ideal claims bind
every target of the run's plan (from C5), or the plan-time check names the ones
that cannot be bound. Paired positive and negative regressions for each rule.

### T3: research mining (P1 #4, the researcher half of P0 #2)

Files: `agents/researcher.py`, the extraction path in `agents/acquisition.py`
(outside T1's passage shaping), `utils/types.py` (the finding model only), and
`agents/fact_checker.py` `claim_attribution` only (the consumption of finding
target ids).

1. Findings keep the target ids the extraction named, validated against the plan
   (a new field that defaults to empty). Claims made from those findings start
   from those targets. The deterministic answer check still gates binding.
2. A read is mined for every planned target, not only for the sub-topic that
   fetched it.
3. Extraction records every dated figure with its vintage and date, so a later
   stage can tell the latest statement from an older one.

Acceptance: replay the run's stored reads through extraction with a scripted
completer. EIA 64705's 19.6 GW sentence becomes a finding for the 2025-forecast
target, carrying its January-2025 inventory vintage.

### T4: report honesty and rendering (P1 #5, #6, #8; P2 #11, #12; P3 #13)

Files: `agents/report.py`, `agents/synthesizer.py`, `graph/nodes.py`
(finalize), `cli.py`. Starts after the synthesizer output-limit commit lands.

1. At finalize, the reader report states the run status, the critic status, the
   review status, answered/required targets and any gate failures. Limitations
   are ranked by consequence; "low confidence" is scoped to cited sources.
2. The quality record carries session status, critic status and review status,
   and the claims' target bindings. Evidence excerpts are the supporting span,
   not the page head. Claim text is published uncut.
3. The CLI prints no critic score when `review_status` is failed, and takes
   "claimed" coverage out of the headline line.
4. The writer keeps the claim's modality and hedges. A statement asserting a
   scope fact with no claim link is rejected. Where claims carry vintages and the
   question asks for the latest, the latest dated statement leads and older ones
   are named as older.
5. No display-truncated text is fed to a model (the 240-character claim cut).
   Text cut for display is marked as cut.
6. Attributed answers are headed as the answer, not as "What would change the
   answer".

Acceptance: re-render the run's final state (from the trace) through the new
finalize. The report states "failed / never judged / 0 of 11 targets" for that
state, and the ledger, the quality record and the report agree.

## Integration and verification

1. Scoped review per slice (read-only reviewer), then merge in the order T2, T3,
   T1, T4. Re-pin the fingerprints once, then run the project-wide suite.
2. Live run of the audit question on the production config, with isolated
   memory (`scratch/live_proof.sh`).
3. Independent audit of that run against the five criteria above. Iterate on its
   findings until it passes, then the live proof (three audit runs plus the
   smoke question).

## Constraints for every slice

- `PYTHONPATH="src;."` with the shared venv interpreter. NEVER `git stash`. Never
  read, print or copy `.env`. Never run the live CLI.
- TDD: each behaviour change has a test that fails before and passes after.
- Do not raise token caps. Do not weaken the evidence-identity refusals, the
  TR-04 lints, or the fatal structural plan checks.
