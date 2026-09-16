# Heat-pump run — predeclaration (new question, generalization test)

Status: **PARKED — NOT AUTHORIZED, NOT RUN.** Kept in the tree so the work is not
lost, and so the tree stays clean.

**Held at the user's direction, 2026-09-15:** *"don't add new runs for new
questions yet. Keep the current questions as baseline and make sure to fix all
the agents before moving on to test new questions."* The battery question is
therefore the standing baseline, and every remaining change is measured against
it. **No candidate SHA is frozen and this document authorizes nothing until it
is explicitly un-parked and filled in.** The measurement plan in §7 remains the
intended use once that happens.

## 1. Purpose — a new question, on purpose

Every run so far used the same question ("current constraints on grid-scale
battery storage deployment"). That made improvements comparable, but it also
means the recent changes — corroboration requirements, read-before-search,
re-source-on-refusal — have only ever been exercised against **one domain**.

This run changes the question to test two things at once:

1. **Generalization.** Do the agents produce a good report in a domain they have
   never seen, with no prompt tuned for it?
2. **Whether verification works when reading works.** The battery runs were
   dominated by a source-access wall: 24 of 37 page reads refused (403 ×10,
   404 ×8) while PDF reads succeeded 28 of 30. The new domain is deliberately
   **document-rich** — national labs, federal and state agencies, and utilities
   publish the relevant material as PDFs and datasets.

**This is not a comparison run.** Coverage and critic numbers are not
comparable to the battery runs; the battery record stands as its own history.

## 2. Question

> What are the current constraints on deploying heat pumps for residential
> heating in cold climates?

Chosen because it keeps the same *shape* as the previous question — "current
constraints on X", with real trade-offs the critic can weigh (cold-climate
capacity and performance, electrical panel and grid limits, refrigerant and
efficiency rules, retrofit and installed cost) — while being a different domain
whose authoritative material is published as documents.

## 3. Candidate

- **Candidate commit: `<FROZEN AT COMMIT TIME>`**
- Refused unless `git rev-parse HEAD` equals the candidate, **or** HEAD is a
  descendant whose entire diff is this document and the run's own record.
- Refused if `git status --short` shows anything staged or modified. Only the
  known unrelated untracked paths `.deepseek-runs/` and `tools/` are tolerated.

## 4. Configuration — unchanged

`reasoning_effort: high`, `agents.tool_budget: 10`, `llm.timeout: 60.0`,
`graph.max_iterations: 3`, `config.yaml` byte-identical to base. **No setting is
changed for this run**, so the only new variable against the previous run is the
question itself. In particular the tool budget stays at 10 even though the
researcher drops ~49 calls per run to it — raising it is the next lever, and
mixing it into a question-change run would destroy attribution.

## 5. Declared ceilings

| Provider | Ceiling | `stop_fraction` |
| --- | ---: | ---: |
| DeepSeek | 700 | 1.0 |
| OpenAI | 60 | 1.0 |
| Tavily | 450 | 1.0 |

## 6. Stop conditions

| # | Condition | Action |
| --- | --- | --- |
| S1 | CLI exits **3** (attempt limit) | Record actuals, do not rerun. |
| S2 | CLI exits **4** (quality gate) | Expected; the measurements are the point. |
| S3 | Pre-provider failure | No spend; fix and rerun. |
| S4 | Wall clock exceeds **45 minutes** | Kill; record as failed. |
| S5 | Tavily refuses with the account usage limit | Count the lost searches explicitly. |

## 7. What will be measured

Per agent (`.superpowers/sdd/analyze_agents.py`, `analyze_loops.py`) and per run
(`evaluate_run.py`):

| Measure | Battery baseline | What this run tells us |
| --- | --- | --- |
| Topics covered | 5-6 of 6-7 (83-86%) | Does coverage generalize? |
| Integrity (dupes / rows / uncited) | 0 / 0 / 0 | Must hold |
| **Failed page reads** | 24 of 37 (65%) | **Should fall** in a document-rich domain |
| **Claims with ≥2 independent domains** | **0** | **The decisive number** |
| Claims verified | 2 of 13 | Should rise if reading leads to corroboration |
| Critic score | 3/10 | The gate |
| Reader report length | ~1,800-2,100 words | Must stay ≤ 8,000 |

### Pre-committed interpretation

- **Reads succeed but independent domains stay 0** → publisher access was never
  the real limit; the pipeline's *sourcing strategy* is, and the fix belongs in
  how the researcher chooses and pairs sources.
- **Reads succeed and corroboration rises, but the critic stays low** → the
  critic's bar is about something else (attribution, gap size, uncertainty
  handling), and the next investigation is the critic's own objections.
- **Reads still fail at a similar rate** → the access wall is domain-independent
  and the sourcing/access help requested externally becomes mandatory.

## 8. Artifacts

- Run log: `output/cli-canary-<timestamp>-heatpump.log`
- Reader report and evidence ledger: the CLI's own output paths.
- Result: a companion record committed after the run.

## 9. Credentials

Supplied through the repository dotenv launcher by path. No credential value is
printed, logged or recorded.

## 10. Authorization

Seventh run of the standing authorization. This question change was requested by
the user directly.
