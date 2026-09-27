<!-- Rubric v1 as delivered by Fable (AuditRun3Fable, 2026-09-25T23:30:58Z), verbatim. Frozen: any change needs v2 and a re-score of the baseline. -->

# Rubric **v1** — score-decided consumer release gate (frozen 2026-09-25; judge: Fable)

**Release is decided by the score alone: RELEASE ⇔ overall score ≥ 80.** Every condition that must block release acts through the score (§4 caps), never beside it. The rubric names only the question's parts, its evidence and its sources — never a domain, a question type or a layout element — so it applies unchanged to any question and any report layout ("gap section" = whatever section the layout uses to admit what was not found).

## 1. Items and weights (sum 100)

| Item | Weight | Why (what matters most to someone researching a topic) |
|---|---|---|
| A1 Parts answered | 25 | The reader came for an answer to each part of the question, with the item or value named and in the form its evidence takes. |
| A2 Traceability | 15 | A number or credited statement that is not what its page says is worse than one that is missing. |
| A3 Honesty rules | 15 | The report never says more than its sources do: no relay as issuer, no invented date/scope, forecasts labelled, no report-made verdict, criteria kept with judgements. |
| A4 Authority | 10 | The answer is only as good as the sources that produced, measured, judged or announced the evidence. |
| A5 Completeness & currency | 20 | Missing the readily available, current fact — or that the sources agree — is the second-largest failure after A1; an undisclosed gap hides it. |
| A6 Structure & readability | 10 | Shape fits the question's parts; no filler or leakage; disagreements between sources reconciled or dated. |
| A7 Mechanics | 5 | Delivered within the time target, without a crash, at most one extra pass, review recorded. |

## 2. Defect classes (decided first; the rest is arithmetic)

- **Cosmetic** — wording or form that cannot change what a reader understands.
- **Minor** — a reader could be misled on a detail or must work harder, but every part's answer stays intact.
- **Material** — a part of the question left unanswered or answered without a usable name/value; a claim its page does not make; a wrong or source-less figure; anything a reader would act wrongly on.

| Item | *Material* when… |
|---|---|
| A1 | a required part has no named item/value in the answer, or a pick is credited to the wrong source. |
| A2 | a load-bearing value, period, organisation or kind differs from the page, or cannot be found on it. |
| A3 | a verdict/rating the page does not make; a relay presented as issuer; a forecast as actual; an invented as-of, date or scope; the report's own pick. |
| A4 | a required part rests on a self-interested, off-subject or relay-only source while the source that produced or judged the evidence was readily reachable. |
| A5 | a major readily available fact (on a page the run read, a candidate it listed, or the first page of an independent search) is missing or contradicted; a required gap undisclosed. |
| A6 | a part's answer is displaced by filler/leakage or buried under an unreconciled contradiction. |
| A7 | no report published; a crash; more than one extra pass. |

Each defect is charged **once, to one item**. Borderline class → the stricter class.

## 3. Per-item score (0–10) and the raw score

**Item score = 10 − C − M − X, floored at 0**: C = 1 if any cosmetic defect; M = 2 for the first minor + 1 per further minor (cap 5); X = 5 for the first material + 3 per further material (cap 10). Anchors, applied literally: **10** no defect · **9** cosmetic only · **8** one minor · **7** two minors · **6** three minors · **5** one material, rest sound · **4** material + cosmetic · **3** material + minor · **2** two materials · **1** two materials with more · **0** three materials or the item fails outright.

**Raw score = Σ weight × item score ÷ 10** (0–100).

## 4. Caps, overall score, threshold, bands

**Overall = min(raw, cap)**, where cap is the lowest that applies:

| Condition (any one suffices) | Cap |
|---|---|
| No report published; or an honesty breach — a figure or claim not on its page, a relay named as the issuer of a load-bearing claim, a forecast presented as actual, the report's own pick presented as fact, an invented date/provenance | **0** |
| A required part of the question missing *and* undisclosed in the gap section; or an untraceable load-bearing figure or credited statement (unverifiable counts as untraceable) | **49** |
| Any material defect in any item, A7 included | **69** |
| A1 < 8 or A5 < 8 (an answer item carrying two or more minors) | **79** |
| Any minor defect anywhere | **89** |

**Release rule: RELEASE ⇔ overall ≥ 80.** Nothing else decides.

**Arithmetic check.** A material defect anywhere caps the overall at 69 < 80, whatever the raw score — including A7, where one material alone leaves a raw 97.5 → overall 69. An honesty breach is 0; a missing-and-undisclosed part or an untraceable number is ≤ 49. Conversely, a report with no material, no veto, A1 ≥ 8 and A5 ≥ 8 has no cap below 89 and its raw is exactly what decides. So 80 is reachable only by reports with no material defect and no veto.

**Why 80.** It is the floor of "good enough to ship": no material defect, every part answered with A1 and A5 at 8 or above, and on average at most one minor per item — the reader gets every part and at most one detail per item to work around. It is also the pipeline's own review acceptance mean (`SEMANTIC_REVIEW_MEAN = 0.80`), so operator, judge and reviewer share one scale.

**Bands (the rating is the band).** **GREAT** 90–100 — reachable only with nothing beyond cosmetic defects (the 89 cap on any minor); **GOOD** 80–89 — minors only, answer items ≥ 8, releasable; **POOR** 0–79 — any material defect, any veto, an answer item below 8, or minors enough to pull the raw under 80. This reproduces the earlier class-based rating exactly for GREAT and for material/POOR; the one deliberate difference is that a minors-only report scoring under 80 is POOR, because the score is authoritative.

## 5. "Passes honestly" — the judge's checklist (all eight, or the score does not count)

1. Every cited page opened for **every** load-bearing figure and credited statement (not the evidence log's snippets) and a stated sample of the rest; matched on value, period, organisation, kind.
2. **Independent research** before scoring A5: ≥1 search on the question's subject and ≥2 readily reachable pages the run did not read, with what they say recorded.
3. **Anchors applied literally** and defects classified by their effect on the reader, never by the pipeline's intent: no credit for a finding that existed but was not written; no downgrade because a fix is planned or the cause is known.
4. Every defect once in the defect table with report line, page text (or its absence), class and item; item scores, raw, caps and overall computed from that table with the arithmetic shown.
5. The untraceable-claims list stated explicitly even when empty.
6. Borderline calls to the stricter class; inferences marked [INFERENCE] and never used to raise a score.
7. Published artefacts audited as published — no re-run, no edit, no regenerated report.
8. The record names the rubric version, raw, cap and reason, overall, band and verdict.

## 6. Verdict line and CLI release policy

**6.1 Per-report verdict** (last line of every audit):
`VERDICT: RELEASE | DO NOT RELEASE — rubric v1 — score NN/100 (raw RR; cap CC: <reason>) — band — honest-pass 1–8 ✓ — session <id>, head <sha>`.

**6.2 Release policy for the CLI.** One report scoring ≥ 80 on one randomly drawn question does **not** release the CLI; I require **four**: questions drawn at random *after* the head is frozen, from a pool the judge does not see beforehand and that was not used to develop the head, one per shape — (a) figures with a period/kind, (b) items or options with attributes, (c) reasons or mechanisms, (d) rules or constraints with dates — each run on the frozen head with default config and `--require-quality`, off-peak, artefacts retained, and **each scoring ≥ 80** (no averaging; one failure blocks). On a failure: fix, re-draw a fresh question of the failed shape and re-run that shape only, unless the fix touched a component every shape runs through (planner, acquisition/selection, extraction, verifier, writer, reviewer, routing) or any model-read prompt — then all four re-run on fresh draws. The same rule governs every later head.

Reason: the failures observed so far are shape-specific — an items question broke passage selection and subject naming (run 3), a rules question broke conditions/exceptions and host-as-speaker (runs 1–2), a figures question broke periods and qualifiers (the dry run) — and one pass cannot tell a fix from a lucky draw (this run's reviewer flipped one defect from major to minor; the redraft dropped twelve statements unprompted). Cost, weighed honestly: four runs ≈ 60–80 minutes of wall time and roughly $10–20 of provider spend, plus four audits — and the audits, at 1–2 hours each, are the real cost. That is a one-time cost per release candidate; releasing on one shape saves ~1 hour and ~$10 and leaves three shapes untested where the pipeline has already failed materially. I do not add stability repeats beyond four: the caps already refuse any material defect, and a repeat of the same shape adds cost without a new failure surface.

## 7. Freeze

Rubric v1 is used unchanged for every later audit. Any change to a weight, class test, formula, cap, threshold, band, checklist item or release policy is v2 (or later) and requires re-scoring the baseline (run 3) under the new version, so trend lines stay comparable.

## 8. Baseline — live run 3 (`output/report-2f74eaf95d644e688d2673cff04106f9-0.md`), rubric v1

Content only (the retiring layout's header counts, Key-facts column set/cell wording and Not-found wording excluded), as the audit was.

| Item | Defects charged (class) | Score | Weighted | Evidence line |
|---|---|---|---|---|
| A1 Parts | material: the "best mic" part has no named consumer product (S009 is a gaming headset; S011 "this is the model to beat" and S012 "the buds'" name nothing); minor: the audio part never applies its own basis (Sound 4.8/4.3/3.9; CNET 9.3 vs 9.2) to say which is best | 3 | 7.5 | Finding 5's content named the XM6; `registry_lines` shows the writer only the snippet; both reviews flagged S011/S012 |
| A2 Traceability | cosmetic: S006 credits the page title rather than CNET; every figure traced to its page | 9 | 13.5 | SoundGuys $390/$459.99/4.8/8.7/37 h; CNET 9.2; BI $298/$399.99; What Hi-Fi $199 — all on page |
| A3 Honesty | material: S010 turns audio-player captions "microphone demo (Ideal)" into a SoundGuys mic verdict; minor: S007/S008 drop BI's stated value criterion and newer-model note | 3 | 4.5 | Buying-guide chunk-39; BI page "we still give an edge to the older model in overall value"; not a veto (the words are on the page; the misreading is interpretive) |
| A4 Authority | minor: a 2023 manufacturer guide on stage/broadcast microphones (relevance 0.45) fills the mic section; minor: a gaming-headset guide stands in for headphones | 7 | 7.0 | Source Evaluator: "stage/broadcast headworn mics rather than consumer headphones" |
| A5 Completeness & currency | material: answers on pages the run read were missed (What Hi-Fi "WH-1000XM6 … best over-ear headphones with a mic"; CNET XM6 9.3 + "excellent voice-calling … more mics"; SoundGuys FAQ "best headphones for calls and meetings") and the sources' convergence on one model never stated; minor: no guide dates (BI Apr/May 2026 vs SoundGuys Sep 2026); minor: RTINGS/Consumer Reports/Mashable queued unread with 12–18 calls left; minor: gaps undisclosed (`not_found: []`; Wirecutter 403s) | 1 | 2.0 | 551 chunks `deferred_capacity`; CNET chunks 8/9/47, What Hi-Fi 14/31 deferred; independent research: RTINGS best-headphones (2026-08-28) names the XM6 |
| A6 Structure | minor: filler sections (lab methodology; generic mic sentence + Shure); minor: SoundGuys' XM6 and BI's XM5 "ones to beat" side by side, unreconciled and undated | 7 | 7.0 | Sections "SoundGuys' lab testing…", "Microphone quality"; S001 vs S008 |
| A7 Mechanics | cosmetic: stored review `problem` text clamped to "[…] (cut)"; 717 s, no crash, one redraft, no extra pass, review `scored` | 9 | 4.5 | cli.log "Elapsed: 11m 53s"; quality JSON `semantic_review_status: scored` |
| **Raw** | | | **46.0** | |

**Caps applied:** 49 — the mic part is neither answered by a named item nor disclosed in a gap section (`not_found: []`); 69 — material defects in A1, A3, A5; 79 — A1 = 3, A5 = 1; 89 — minors present. No honesty veto (S010 quotes page words; charged as A3 material). Untraceable claims: none. Lowest cap **49**; **overall = min(46.0, 49) = 46.0** (the raw is already below the cap; had the raw been higher, the 49 cap would have held it there).

**Band:** POOR. Honest-pass checklist: 1 ✓ (all seven pages opened) · 2 ✓ (RTINGS, CNET/BI full pages) · 3 ✓ · 4 ✓ · 5 ✓ · 6 ✓ · 7 ✓ · 8 ✓.

**VERDICT: DO NOT RELEASE — rubric v1 — score 46/100 (raw 46.0; cap 49: required part unanswered by name and undisclosed) — POOR — honest-pass 1–8 ✓ — session 2f74eaf95d644e688d2673cff04106f9, head b7a7f8a.**

CLI status under §6.2: no gate run exists yet; run 3 is the baseline for the items/options shape only. Consumer release requires four fresh reports at ≥ 80, one per shape, on a frozen head that at least removes the three material defects above.
