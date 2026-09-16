# Verification-boundary diagnosis — where corroboration actually fails

Status: **diagnosis only. No code changed.** Written to be the starting point for
the next work on `codex/cross-agent-planner-fix-parity`.

## 1. The finding

After a run with **100% coverage, 8 cited sources, 6 verified claims and 9
`insufficient_evidence`**, I audited every non-verified claim using the `Reason`
column added in `c4a3f12` and then read `resolve_verdict`
(`agents/fact_checker.py:670-688`) and `_valid_passages` (`:633-667`).

**Every non-verified claim failed in one of exactly two ways.**

`resolve_verdict` is:

```python
del independent                      # publisher names alone never satisfy the gate
if not passages:
    return "insufficient_evidence", 0.0
if any(p.stance == "contradicts" for p in passages):
    return "contradicted", confidence
if any(p.stance == "supports" for p in passages):
    return "verified" if verdict == "verified" else "unverified", confidence
```

- **(a) Nine claims — no valid passage survived.** `if not passages` returns
  `insufficient_evidence` with confidence 0 and **no judgment is made at all**.
  Either the model cited nothing, or its citations were discarded by
  `_valid_passages`.
- **(b) Three claims — a valid supporting passage existed and the model declined
  to say `verified`**, so the verdict was downgraded to `unverified`.

## 2. The real evidence-handoff problem

A reviewer flagged a handoff mismatch in `claimed_domains` (extraction attaches
every supporting URL to one claim, the fact checker then excludes all of them,
so A+B would require a third publisher C). That mechanism is real but **latent**:
every claim in these runs carries exactly one URL on one domain, so nothing was
being excluded.

**The handoff problem is live somewhere else.** `_valid_passages` keeps only
passages whose URL is in `retrieved_urls` — the **verification loop's own** read
set:

```python
retrieved = {normalize_source_url(url) for url in retrieved_urls}
...
if not url or url not in retrieved or _is_copied_example_url(url):
    continue
```

Evidence the **researcher already read upstream** is therefore inadmissible as a
verification passage, however independent its publisher. The reviewer's F2
specification says the evidence pool is *"the union of supporting upstream
findings **+** supporting fact-checker retrievals"*; the code uses only the
second half. **That mismatch is what nine of twelve failures consist of**, so the
pooling change is an unblocker, not a refinement.

## 3. The intended rule (specified by the reviewer, not yet implemented)

`verified(c)` requires two evidence units `e1, e2` such that:

```
read_ok(e1) and read_ok(e2)
supports(e1, c) and supports(e2, c)
publisher(e1) != publisher(e2)      # distinct canonical publishers
work(e1)      != work(e2)           # distinct canonical works
```

- The pool is **upstream supporting findings ∪ verifier retrievals**.
- A claim already supported by qualifying upstream A+B needs **no new retrieval**.
- Two URLs alone are insufficient: each must carry a passage that was actually
  read and **directly supports the complete claim**, or a loosely related pair
  could auto-verify a claim.
- Ambiguous identity **fails closed** — it must not supply the second unit.

`work_identity` does not exist yet; `publisher_identity` is registrable-domain
only, so it would wrongly treat `nrel.gov` and `nlr.gov` as independent (the DOE
renamed NREL to National Laboratory of the Rockies; verified independently).
Precedence specified: normalized DOI → issuer-namespaced report number (e.g.
`lbnl:<number>`) → identical content hash → normalized title + year + issuing
organization → otherwise unknown. An OSTI mirror of an LBNL report is **one
publisher and one work**: a transport mirror, not corroboration.

## 4. What is NOT the constraint — measured, not assumed

- **Reading volume**: document reads rose 12 → 64 across runs; no effect on
  corroboration.
- **Retention**: `bound_sub_topic_findings` grouped by URL while its docstring
  promised publisher diversity; fixed in `0391c57`; no effect on corroboration.
- **The transport ceiling**: never bound (DeepSeek 268/700, Tavily 312/450).
- **The User-Agent**: probed failing hosts twice each, browser UA vs current UA —
  `emp.lbl.gov` 403/403, `utilitydive.com` 403/403, `iea.org` 403/403. Not the
  cause.
- **Prompt-level demands for corroboration**: made in the plan and in the
  researcher's prompt, and finally as a success criterion; verified claims rose
  2 → 3 → 4 → 6 but the verdict boundary did not move.

## 5. Two metrics that have been read wrongly, and must not be again

1. **"Claims with ≥2 independent domains"** — counts the domains among a claim's
   **own** sources (the findings it came from). It is **not** a corroboration
   measure; verification happens through the verifier's own retrieval and is
   recorded as the **verdict**. Verdicts are the metric.
2. **"0 duplicate claims"** — means zero *exact-fingerprint* duplicates. The same
   fact appears three times with verdicts verified/verified/unverified in one
   run, so semantic claim consolidation is needed before "N verified" is
   trustworthy as a count of distinct corroborated facts.

## 6. Next steps, in order

1. **Pool the evidence** so a passage whose URL was read upstream in the same run
   is admissible, under the existing independence rule. This targets 9 of the 12
   failures and changes no threshold — the rule stays "independent, read,
   supporting".
2. **Add `work_identity`** so mirrors and aliases cannot count as a second
   publisher.
3. **Re-examine (b)** with fresh data — why does the model decline `verified`
   when a supporting passage is present? Likely claims specified more tightly
   than any independent source states them, which is where semantic claim
   consolidation also helps.
4. Then re-run the **blind** readiness battery. Keep the reviewer's five
   ground-truth sources as a **holdout** for validation; run the seeded
   diagnostic separately.

## 7. Environment traps worth carrying forward

- A wrapper's exit code is not the CLI's: the background job reported 0 while the
  CLI exited 4.
- This worktree is OneDrive-backed and **mtimes drift**; `git status` / `git log`
  are the only reliable activity signals.
- `agent_prompt_fingerprint` hashes the agent's **module source**, so any edit to
  an agent module (or to the shared `agents.prompts`) moves pinned values. Re-pin
  deliberately with the reason, per the established convention.
- A live grep of a stream that does not carry a category is not evidence that the
  category is empty (I made that mistake once and reported a false all-clear).
