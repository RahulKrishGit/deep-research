# Sol High Review: Researcher Sequential Live Evidence

Date: 2026-09-10  
Reviewed branch: `RahulKrishGit/deep-research` / `codex/cross-agent-planner-fix-parity`  
Reviewed candidate: `fa805ab2cf11adf6620eecffd5b502c56537465d`

## Evidence supplied

The review used the pushed sequential Researcher report:

`docs/superpowers/2026-09-10-cross-agent-planner-fix-parity-sequential-live-researcher.md`

That report records one live repetition with `12/14` hard gates passing,
failed `citations_known` and `no_invented_sources`, deterministic quality
`0.70`, `sources_are_real_urls == 0.0`, `react_stop_reason == max_iterations`,
zero prohibited calls, and an unscorable judge with typed paths `rationale` and
`$`. The report contains no raw provider output, prompts, credentials, or
individual URL list.

## Review disposition

Sol High found no basis for a Researcher prompt, iteration, or token-budget
change yet. The judge schema failures are independent shared infrastructure
evidence and supply no Researcher quality score. `max_iterations` is a
separate trajectory signal: it repeated, but the existing provenance rule
prevents the live artifact from proving whether the cited URLs were retrieved.

The confirmed repair boundary is the evaluator/artifact provenance seam:
Researcher extraction can see a successful tool payload up to the existing
evidence limit, while the artifact retains only a bounded observation summary.
The source-provenance gates then inspect the lossy summary. A valid URL can
therefore disappear before evaluation. The single live artifact cannot prove
that every failed URL was retrieved, so the agent itself must not be changed
on this evidence alone.

## Execution decision

Proceed with Task 20: add bounded SHA-256 fingerprints of normalized source
identities from successful `web_search`, `web_scraper`, and remote
`document_reader` results to `DependencyLedger`; use those fingerprints only
for the live Researcher source-provenance gates; preserve the raw-URL-free
telemetry boundary and all frozen agent/evaluation settings. Add RED/GREEN
model, target, and evaluator tests first. After review and offline verification,
run one focused Researcher confirmation. If a finding remains absent from the
complete fingerprint set, open a separate Researcher-agent repair task.

No prompt change, budget increase, case/rubric/threshold/weight change, judge
repair, or iteration increase is authorized by this review.
