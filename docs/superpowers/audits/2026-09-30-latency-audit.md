# Latency audit: where a 25-minute run spends its time, and what can be cut without losing accuracy

**Status** audit only, written 2026-09-30 (read-only on code; no run started, no paid call made; not committed). It proposes changes; none is made here. Every experiment it names that costs money is governed by the existing spend rules.

**Sources of truth.** Code at `f4282818` (branch `feat/notes-progress-report-stop` = `origin/main`). The two live runs of 2026-09-30, read from the running API (`GET /research/{id}/stream`, `/status`) and from `output/report-<id>-0-quality.json`: **Tamil** = `f3a667b153db440fbd3dcb7fb5bfdddf` ("what are the best films in tamil?", completed, accepted at 0.814) and **Latte** = `a02a75fd75d44d8481f34953a4ff52e1` (incomplete; DeepSeek credit ran out during the writer, so its writer and reviewer figures are not used). The earlier latency investigation `docs/evaluation/live-audits/stall-investigation-fable.md` (its fixes landed: `3a99458a` page admission, `f2433462` per-attempt telemetry and loop-lag monitor, `e0e823b5` streaming with a 150 s idle timeout). Closed human decisions this audit does not reopen: output caps lifted (2026-09-25, `config.yaml:25-31`, `:82-90`, `:179-208`); 15 turns and 40 tools per sub-topic (user rulings in `2d5e24e3`); the reviewer at `max` "because a wrong acceptance is more expensive than a slower review" (`config.yaml:51-64`); the running-stage UI picks of 2026-09-28 and 2026-09-30. Where an option collides with one of these, §9 says so instead of changing it.

**Conventions.** Engine paths are under `src/deep_research/` and written `agents/…`, `graph/…`, `tools/…`, `providers/…`, `memory/…`, `runtime/…`, `utils/…`, `api/…`. Web paths are under `web/`. Times are seconds after `graph.session.started` (T0) unless marked "end-to-end" (from session creation, which includes the one-time check). **[INFERENCE]** marks anything not observed in code or data; each names the test that proves or falsifies it. "Accuracy" means what the brief defines: every finding verified against its page, every report sentence Statement-Checked, verification never touched by reader notes, the same honesty rules in writer and reviewer, the same acceptance rule, and no expected drop in coverage or report quality.

---

## 0. Recommendations

The Tamil run took **1,530 s end-to-end (25.5 min)**: 27 s of one-time check (25 s of it the reader), then seven stages that run strictly one after another (`graph/orchestrator.py:191-197`). Almost every stage ends on its single largest generation at ~220-270 tokens/s, so the run is at the "generation-bound floor" the stall investigation described (`stall-investigation-fable.md:13`, `:128`). Four groups of changes remove waste with no or low accuracy risk; three more need proof; the structural options buy little on runs like these and cost a UI redesign.

| Step | What to do | Kind | Expected end-to-end on Tamil | Why (one line) | Accuracy safeguard |
|---|---|---|---|---|---|
| 0 | **Instrument first (O8):** per-tool duration and lock wait on `researcher.tool_call`, the tail split on `researcher.sub_topic.completed`, per-call records (start offset, tokens, reasoning tokens) per stage | small code | 1,530 s (unchanged) | Today nothing records tool time, lock waits, or which call was slow; steps 3-4 and every experiment are sized from inference without it | Telemetry only; nothing a model reads changes |
| 1 | **Raise `agents.verifier_concurrency` 16 → 64 (O1)** | config | ~1,460 s (−0 to −72) | 34 Context Check batches queue behind 16 slots: the verifier ran 290 s against a slowest call of 217.5 s | Identical requests, only start times move; prove with the sorted request-digest test |
| 2 | **Write the 262 memory entries in one batch with one embedding session (O2)** | small code | ~1,420 s (−38) | The report is held back 42 s by one-at-a-time Chroma writes that each rebuild the ONNX embedder | Same entries and vectors; memory only feeds later runs; the report and its hashes are already rendered |
| 3 | **Tail and small fixes (O3, O9-O12):** owed re-extraction batches of one page in parallel; split re-asks in parallel; drop the forced "finish now" 15th turn; no download of formats the reader cannot parse; reputation lookups in parallel | small code | ~1,360-1,410 s | The post-loop tail is two sequential call rounds; the forced last turn cost 4-12 s per topic | Identical requests and merge order, or a turn no artifact reads (§7 tier 1) |
| 4 | **Narrow the run-wide tool lock (O4):** fetch and parse outside it, single-flight per URL, several tool calls of one turn at once, one shared HTTP client, robots.txt cached per host per run | medium code; amends design decision D9 | ~1,320-1,380 s (22-23 min) | Only one tool call runs at a time across all topics; Tamil shows two all-topic stalls of 11 s and 15 s | Same tools, same admission and robots rules; replay-matrix invariants (§7 tier 1) |
| X1 | **First experiment: `agents.verifier_batch_size` 5 → 2, or batches balanced by figure count (O5)** | config (or small code) | ~1,230-1,300 s | One 5-item batch generated 52,799 tokens in 217.5 s; smaller batches shrink the slowest call | Stage replay on frozen states: per-figure verdicts must agree with the control as well as the control agrees with itself (§7 tier 2) |
| X2 | **Researcher with thinking disabled (O6)** | config + harness toggle | ~1,100-1,220 s | Decision turns and page extractions are reasoning-heavy; the tail is extraction time | Every finding still passes Figure Match and the Context Check; coverage must be non-inferior in paired runs (§7 tiers 3-4) |
| X3 | **Planner at `high` effort, or its repairs at `high` (O7)** | config / small code | ~980-1,130 s (16-19 min) | Three sequential `max` plan calls took ~285 s | Planner suite at `high` vs `max` plus paired live runs; the planner is the most accuracy-sensitive agent (§6) |
| Skip | Per-topic pipelining (P1), research on the draft plan (P3), speculative planning during the check (P4), lower effort for writer or reviewer, lower concurrency, hedged requests, shorter output caps | — | — | Small gain here (the heaviest topic finishes last, §1.4), accuracy-bearing, or already rejected | — |
| Later, only if 16-19 min is not enough | Streaming verification inside research (P2) | large code, UI-expensive | −90 to −200 s more | Removes the evaluator and most of the verifier from after research | Needs a dedup and notes-timing redesign and full proof |

**Realistic target.** About **22-23 min** with steps 0-4 alone (no accuracy risk). About **18-20 min** if X1 and X2 pass, **16-19 min** with X3 as well. The writer (346 s) and the reviewer (180 s) together are ~9 min that this audit deliberately leaves alone (§6), so ~15-17 min is the floor of this design under the brief's accuracy rules, even with P2; the commercial 5-15 min band is out of reach without touching them.

---

## 1. Where the time goes

### 1.1 Per-step table

Timings are node wall times from `graph.node.started` to `graph.node.completed`. Call figures are from `telemetry.stages[]` in the quality JSON (`observability/run_telemetry.py:236-330`): `calls` counts provider calls that returned usage, `Σ call-s` sums their durations (retries included), and only each stage's slowest call is kept.

| Step | Tamil start → end | Tamil wall | Calls | Σ call-s | Slowest call (s / output tokens) | Latte wall | Shape of the stage |
|---|---|---|---|---|---|---|---|
| One-time check | −26.9 → 0 | 26.9 (1.6 model + 25.3 reader) | 1 | — | — | 33.8 (5.8 model + 26.0 reader + 2.0) | Thinking disabled (`api/clarify.py:187-189`); waits up to 60 s for the reader (`api/sessions.py:604-614`) |
| Planner | 0.8 → 295.6 | **294.8** | 5 | 294.7 | 193.6 / 42,129 | **445.7** | Fully sequential: Σ call-s equals wall. Tamil: 2 ReAct turns, draft, lint repair, review (sound); Latte: 2 turns, draft, review (unsound), review repair, confirming review (§3.1 O7) |
| Researcher | 295.6 → 599.8 | **304.2** | 173 | 2,784.8 | 149.7 / 40,937 | **401.2** | 5 topics at once; each loop 130-151 s (ends 425.9-447.0), then a tail of 3.0-152.6 s (§1.3) |
| Source evaluator | 600.1 → 641.9 | 41.8 | 3 | 97.2 | 39.4 / 9,256 | 42.9 | 3 scoring batches all in flight (33 sources / 12, `config.yaml:144`; 6 slots, `:121`); wall ≈ slowest call |
| Evidence verifier | 642.4 → 932.3 | **289.9** | 34 Context Check calls | (2,699.2 incl. 11 Statement Check calls of the writer) | 217.5 / 52,799 [INFERENCE: a Context Check call, §9 item 1] | 142.2 | 170 figure-bearing findings in batches of 5 through one 16-slot pool (`agents/evidence_verifier.py:1041-1049`) |
| Report writer (+ quality) | 932.9 → 1,279.0 | **346.1** | 6 drafts + ~11 checks | 641.4 (drafts only) | 239.1 / 62,574 | (not usable) | 5 part drafts in parallel, each followed by its Statement Check, then the bottom line and its check (`agents/report_writer.py:3017-3042`, `:2195-2225`) |
| Report reviewer | 1,279.9 → 1,459.8 | **179.9** | 1 | 179.2 | 179.2 / 43,127 | (not usable) | One whole-report call at `max`; first token after 4.2 s |
| Finalize | 1,461.6 → 1,503.8 | **42.2** | 0 | — | — | 0.2 (partial run, 0 memory writes) | 3 document writes (~14 ms) then 262 memory writes one at a time (`graph/nodes.py:627-646`) |
| Node hand-offs | — | ~7 | — | — | — | — | Whole-state dump and validate at every boundary (`graph/state.py:196-217`); e.g. evaluator start → its first event 1.7 s |
| **Total** | | **1,504.7 graph / 1,530 end-to-end** | 233 in telemetry (incl. the 11 Statement Check calls) + the check call | | | 1,296 end-to-end (incomplete) | |

Run-level telemetry (Tamil): input 2,795,849 tokens, of which 833,536 served from DeepSeek's prefix cache (29.8 %); 1,462,629 reasoning tokens; peak 25 calls in flight; 0 rate-limit errors; event-loop lag at most 3.4 s with no block of 5 s or more. Latte: 39.8 % cached, 1,365,097 reasoning tokens, peak 31, 0 rate limits, lag at most 1.3 s.

The 45 calls telemetry files under `evidence_verifier` are 34 Context Check calls (170 findings with figures and a snippet on the page, 5 per call: `agents/evidence_verifier.py:1027-1042`) plus 11 Statement Check calls that the writer makes under the verifier's name (`agents/evidence_verifier.py:1443-1446`; 35 section sentences in 5+1+2+2+2 batches, plus the bottom line's 4 in 1).

### 1.2 The critical path

The graph is a straight chain (`graph/orchestrator.py:191-197`), each node awaits its whole agent (`graph/nodes.py:244-288`), and each agent ends on its slowest branch:

1. Planner: draft → lint repair → review, each at `max` and each awaited in turn (`agents/planner.py:3664-3755`).
2. Researcher: the slowest topic, topic-03, loop 151.4 s plus tail 152.6 s. The node waits for every topic (`agents/researcher.py:4786-4792`).
3. Evaluator: its slowest batch, 39.4 s.
4. Verifier: the 16-slot chain holding the 217.5 s call (≈72 s of queueing in front of it, §3.1 O1).
5. Writer: the slowest part (a 239.1 s draft, then its checks) and then the bottom line and its check, ~107 s together.
6. Reviewer: one 179.2 s call.
7. Finalize: 42.2 s of memory writes.

Generation speed on the slowest calls was 218-273 tokens/s (reviewer 43,127 tokens / 179.2 s = 241; slowest verifier call 243; writer 262; planner 218; researcher 273 with 25 calls in flight), the same 216-245 tokens/s p50 the stall investigation measured (`stall-investigation-fable.md:75`). Concurrency does not slow a call: the busiest stage produced the fastest call. **Each 10,000 output tokens on the critical path is ~40 s.**

### 1.3 The researcher: loop, tail, and stalls

**Loop.** Every topic ran 15 model turns (the researcher never overrides the sufficiency hook, so nothing stops a loop early: `agents/base.py:362-365`, `agents/react.py:747-748`). Tool turns 1-14 averaged 9.5-10.5 s. Turn 15 is a forced finish: the prompt says "This is the last iteration: return the final answer now without calling a tool" (`agents/prompts.py:261-267`), and no topic called a tool on it. Between each topic's last `researcher.tool_call` and its loop end (start + `elapsed_s`, `agents/researcher.py:4512`, `:4528`) lie 3.8-12.5 s; on the critical topic-03, 7.8 s.

**Tail.** `elapsed_s` stops when the loop returns; the completed event is built only after `extract_findings` (`agents/researcher.py:4528-4541`, `:4610-4628`). The tail is two rounds of model calls:

- the wait for every page extraction started during the loop, in admission order (`agents/researcher.py:3797-3802`); a page read in turn 14 starts its extraction then, and extraction calls reached 40,937 tokens (149.7 s);
- then one `gather` of owed, cross-topic and dissent re-extractions (`agents/researcher.py:4144-4178`), where one page's up to two batches run **one after the other** (`agents/researcher.py:3522-3564`, `MAX_OWED_BATCHES = 2` at `:159`), each capped at 32,768 tokens (`config.yaml:208`), i.e. up to ~130-146 s each.

Call accounting supports this: 173 researcher calls = 75 decision turns (5 × 15) + ≤ 51 page extractions (51 successful reads) + ≥ 47 re-extraction calls in the tail. Latte: 179 = 60 + ≤ 69 + ≥ 50. Tails: Tamil 3.0 / 82.2 / 78.6 / 97.0 / 152.6 s; Latte 14.1 / 107.8 / 126.5 / 231.8 s, the last including a 105.1 s attempt lost to a connection error and its 70.4 s retry.

**Stalls across all topics.** Turn completions of all five topics bunch twice after long silences: after an 11.2 s gap, all five complete turn 2 within 0.4 s (315.6-316.0); after a 15.4 s gap, five turn completions land within 2.8 s (363.9-366.7). The event loop was never blocked 5 s or more (lag max 3.4 s), so these are not CPU stalls. The run holds **one tool lock for all topics** (`agents/researcher.py:4741-4746`); every `use_tool` decision's policy check, execution and admission run under it (`agents/react.py:369-373`, `:410-424`), and a turn's several tool calls run one after another (`agents/react.py:410`, `:608`). One slow fetch therefore holds every topic: the scraper allows 10 s per request (`tools/web_scraper.py:128`), fetches robots.txt on every call (`:186`, `:244-262`), retries twice with backoff and honours any `Retry-After` without a ceiling (`:267-282`, `:1219-1230`), and the document reader allows 20 s (`tools/document_reader.py:82`). A worst-case scrape holds the lock ~41 s. [INFERENCE: the stalls are lock queueing behind one slow fetch. Test: O8's `lock_wait_s` on `researcher.tool_call`.] `researcher.tool_call` events carry no timing and are stamped after the whole turn (`agents/react.py:743-746`, `agents/researcher.py:4441-4446`), so the stream cannot say more.

Wasted tool calls are common but do not lengthen the loop, because the loop always runs 15 turns: 39 of ~99 Tamil scrapes were `robots_disallowed`, 4 `empty_page_content`, 2 `unsupported_document_format`. The last kind is certain to fail: the policy sends `.doc`, `.docx`, `.xls` and `.xlsx` URLs to the document reader (`agents/acquisition.py:946-958`), which parses none of them (`tools/document_reader.py:36-50`), after a full download under the lock.

### 1.4 The heaviest topic finishes last and dominates every later stage

| Run | Last topic to finish research | Its share of Context Check items | Its share of figures | Its report part |
|---|---|---|---|---|
| Tamil | topic-03 (box office), 599.6 s | 83 of 169 | 295 of 395 (incl. the one 16-figure finding) | 96 findings |
| Latte | topic-02 (ratings), 847.9 s | 61 of 96 | 164 of 256 | 91 findings |

(From `parts[].finding_ids` joined to `findings[].verification.figure_results` in each quality JSON.) Figure-heavy topics extract longer outputs, so they finish research last, and then they also carry most of the verification and the biggest part draft. This is why per-topic pipelining (§3.3 P1) buys little on these runs: the critical path simply becomes the heaviest topic's own chain.

### 1.5 What does not cost time

- **Prefill and prefix caching.** First tokens of the slowest calls arrived after 0.8-4.2 s, against totals of 150-239 s. Better cache hits would lower cost, not wall time. Static-first ordering is already in place (`agents/prompts.py:59-73`, `providers/deepseek_provider.py:167-180`).
- **Rate limits and concurrency ceilings.** 0 rate-limit errors in all three saved runs at 25-37 calls in flight; DeepSeek documents 2,500 (`config.yaml:124-128`). Sub-topic (10), extraction (16 per topic, `agents/researcher.py:4374`), scoring (6) and section (10) limits never bound in these runs.
- **Event-loop blocking.** Fixed by `3a99458a`; lag stayed under 3.4 s.
- **Memory recall at start.** For planning it skips the long-term query entirely (per `runtime/recall.py:108`); `planner.memory.recalled` fires at 0.8 s.
- **JSON repairs and transport retries.** None in Tamil (call counts match exactly: 45 = 34 + 11, writer 6 = 5 + 1). One connection-error retry in Latte (105.1 s lost); streaming already detects a dead stream within 150 s.

---

## 2. Ranked opportunities

Savings are on the Tamil critical path, measured from data where possible. They **do not add up**: O1 and O5 act on the same verifier stage (O5 is quoted after O1), O3 and O6 on the same research tail, and P1/P2 overlap O1 and O5. "Accuracy risk": **none** = the provider receives the same requests and the same post-processing runs in the same order, or the change touches nothing an artifact reads; **low** = same rules and inputs, but ordering can change; **needs proof** = a model sees different input or runs differently.

| Rank | ID | Change | Where | Expected saving (Tamil) | Accuracy risk | How to prove neutrality | Effort |
|---|---|---|---|---|---|---|---|
| 1 | O1 | `agents.verifier_concurrency` 16 → 64 (≥ the number of Context Check batches) | `config.yaml:123`; `agents/evidence_verifier.py:1041-1049` | **0-72 s** (node 290 s → its slowest call, 217.5 s). Latte: ~1 s (its slowest call was in the first wave) | **None**: same batches, same prompts | Sorted per-request digest equal before and after (§7 tier 1); `rate_limit_errors: 0` in the next live telemetry | S |
| 2 | O2 | Finalize writes all memory entries in one `save_many` and embeds with one cached ONNX session | `graph/nodes.py:627-646`; `memory/long_term.py:224-259`; `providers/embeddings.py:148-160` | **~38 s** (42.2 s → ~2-5 s [INFERENCE]; a partial run with no writes finalized in 0.2 s) | **None**: same entries, same vectors, same run status (fixed before finalize, `graph/state.py:409-411`) | Unit test: identical ids, documents, metadata and vectors vs. the per-entry path; publication tests (`tests/test_graph/test_nodes.py:1532-1579`) keep per-finding failure counts | S |
| 3 | O3 | Issue one page's owed re-extraction batches together; apply `build_findings` in batch order afterwards | `agents/researcher.py:3522-3564` | **0-70 s** [INFERENCE] on the tail when the slowest topic's page owes 2 batches (the stall audit measured a 151 s owed round in its run 9 and 191 s of post-loop extraction and owed calls in its run 10: `stall-investigation-fable.md:126-128`) | **None**: batch 2's prompt never depends on batch 1's reply; only `admitted_keys` dedup does, and it runs in the same order | Unit test: identical findings, rejections and admitted keys for both reply-arrival orders | S |
| 4 | O4 | Narrow the tool lock to admission and ledger commits; fetch and parse outside it with a per-URL single-flight; run one turn's calls together; one shared `httpx.AsyncClient`; robots.txt cached per host per run | `agents/react.py:369-373`, `:410-424`; `agents/researcher.py:4741-4746`; `tools/web_scraper.py:174-187`, `:244-262`; `main.py:284-286` | **20-40 s** [INFERENCE] of loop time (visible stalls 11 s + 15 s, plus per-scrape TLS and robots round trips) | **Low**: same tools, policy, robots rule; admission order across topics can differ (as today between turns) | New single-flight tests (one download and one admission per URL); `test_the_tool_lock_serialises_only_the_tool_section` (`tests/test_agents/test_react.py:1728`) rewritten; replay matrix 35 rows × 3 with 0 network attempts; O8 lock wait before/after | M; amends spec decision D9 (§9) |
| 5 | O5 | Smaller Context Check batches (5 → 2) or batches bounded by figure count; same knob sizes the Statement Check | `config.yaml:122`; `agents/evidence_verifier.py:1041-1042`, `:1520-1523` | **60-110 s** after O1 [INFERENCE: the slowest call would carry ≤2 findings instead of 5] | **Needs proof**: each call sees fewer findings; per-item judgments are independent by design (`agents/evidence_verifier.py:921-965`) but model behaviour at a different batch size is unmeasured | Stage replay on frozen Tamil and Latte states (§7 tier 2): per-figure status and dropped reasons vs. control's repeat agreement; evidence-verifier suite 3 × 3 | S (config) / S (balancing code) |
| 6 | O6 | `llm.model_overrides.researcher.thinking_mode: disabled` (decisions and extraction) | `config.yaml:35-37`; `utils/config.py:31-41`, `:85-125`; `providers/capabilities.py:73-79` | **80-150 s** [INFERENCE: depends on the reasoning share of researcher output, which no artifact records] | **Needs proof**: precision is still guarded (every finding passes Figure Match and the Context Check), but search choices, extraction recall and target binding may change coverage | Harness toggle for thinking mode (`evaluation/config.py:84-91` is hard-wired to enabled); researcher suite 4 cases × 3; paired live runs on coverage metrics (§7 tiers 3-4) | S config + S harness |
| 7 | O7 | Planner at `high` effort, or only its two repair calls at `high` | `config.yaml:32-34`; `agents/planner.py:3664-3842`; per-call effort already exists (`providers/capabilities.py:203-225`, used for truncation re-asks at `agents/base.py:95`) | **70-150 s** [INFERENCE: the stall audit guessed "roughly half", untested, `stall-investigation-fable.md:141`] | **Needs proof**: the plan sets coverage; a run-10 material defect started in the plan (`docs/evaluation/live-audits/run10-fix-wave-brief.md:6`); no planner-at-`high` quality measurement exists (§9 item 3) | Planner suite (4 controlled cases × 3) at `high` vs `max`, both at 0.80 / 0.65; plan diff on the 9 probe questions; paired live runs | S |
| 8 | O8 | Instrumentation: per-tool `duration_s` and `lock_wait_s`; tail split (`extraction_wait_s`, `owed_round_s`, `owed_calls`, slowest page); per-call start offset, output and reasoning tokens per stage | `agents/react.py:403-746`; `agents/researcher.py:4528-4627` (per-page `elapsed_s` already exists, `:3422`, `:3478`, but is never emitted); `observability/run_telemetry.py:236-330` | 0 (enables O3, O4, O5, O6 sizing) | **None** | — | S |
| 9 | O9 | Drop the forced last turn: end the loop after the last tool turn with a synthetic finish | `agents/prompts.py:261-267`; `agents/react.py:394` | **~8 s** (measured 7.8 s on topic-03; 3.8-12.5 s per topic) | **None** on artifacts: the finish text only reaches `ReActRun.final_answer` (`agents/researcher.py:395-437`), which no report stage reads | Test that `final_answer` is unused downstream; count last-turn tool calls in recorded runs (0 of 9 topics here) | S |
| 10 | O10 | Run the split re-asks of a failed Context Check or Statement Check batch together | `agents/evidence_verifier.py:1069-1073`, `:1450-1461` | 0 on Tamil (no failed batch); removes one half-call from a failure's tail | **None** | Existing split tests with order-independent assertions | S |
| 11 | O11 | Do not download URLs whose format the document reader cannot parse (`.doc`, `.docx`, `.xls`, `.xlsx`); mark them unreadable in the candidate list | `agents/acquisition.py:946-958`; `tools/document_reader.py:36-50` | Small: 2 full downloads under the lock in Tamil | **None**: no evidence was ever obtainable from them | Unit test on the routing table | S |
| 12 | O12 | Fetch the 33 source reputations together (or one `collection.get(ids=[…])`) | `agents/source_evaluator.py:1132-1134`; `memory/long_term.py:314-316` | ~1-2 s | **None** | Unit test: same reputations map | S |
| 13 | P2 | Streaming verification: score a source when its read is admitted; Context-Check a finding when its page extraction completes | §3.3 | **90-200 s** [INFERENCE], less after O1 and O5 | **Needs proof** (cross-topic dedup and notes timing change) | Full tiers 2-4 | L; UI-expensive |
| 14 | P1 | Per-topic pipelining (evaluate, verify and draft a topic's part as soon as its research ends) | §3.3 | **40-60 s** after O1 on these runs [INFERENCE] | **Needs proof** | Full tiers 2-4 | L; UI-expensive |
| — | P3 | Start research on the draft plan while the review runs | §3.3 | — | Breaks accuracy (repairs renumber topics) | — | Rejected |
| — | P4 | Speculative planning during the reader's check | §3.3 | — | — | — | Rejected |

An obligation-aware early stop for research loops (the "stop a branch whose obligation is met" technique) is listed in §3.1 as a later experiment, not ranked: it trades coverage for time by construction.

---

## 3. Details by kind

### 3.1 Config-only (and experiments)

**O1 — `verifier_concurrency` 16 → 64.** `verify` slices every figure-bearing finding into batches of `verifier_batch_size` and gathers them behind one `asyncio.Semaphore(verifier_concurrency)` (`agents/evidence_verifier.py:1041-1049`). Tamil had 34 batches for 16 slots, so 18 started only when a slot freed; the node took 289.9 s against a slowest call of 217.5 s, so that call started about 72 s in. With every batch in flight at once, the node takes about as long as its slowest call. The batches, their order and their prompts do not change, so no model sees anything different. The writer's Statement Check shares the same setting (`agents/report_writer.py:3018`) with only 11 batches, so it is unaffected. The field has no upper bound (`utils/config.py:323`). Rate-limit risk is low: 0 rate limits at 25-37 calls in flight in every saved run, and the stall investigation ruled out concurrency effects at 26-41 in flight (`stall-investigation-fable.md:117`). The saving is run-dependent: 0 when the slowest batch starts in the first wave (Latte: node 142.2 s vs. slowest 140.9 s).

**O5 — smaller or balanced Context Check batches (first experiment).** The slowest verifier call produced 52,799 tokens for at most 5 findings. Figures per finding are very uneven (Tamil: 86 findings with 1 figure, one with 16), and batches are cut by count in raw-finding order (`agents/evidence_verifier.py:1041-1042`), so one batch can collect several figure-heavy findings of the heaviest topic. Two versions:
- `verifier_batch_size: 2` (config): 170 findings → 85 calls; with O1's 64 slots, ~2 waves. The static prefix (~6k characters) repeats per call but is prefix-cached.
- Batches bounded by figure count (small code): e.g. at most 5 findings and at most 12 figures per batch, a figure-heavy finding alone.

Either way a model judges fewer findings at once. The instruction is per figure and per finding (`agents/evidence_verifier.py:921-965`), and replies are matched by label (`:1084-1094`), so the design already treats items as independent. But the model's attention and corrections at a different batch size are unmeasured, so this needs the §7 tier-2 stage replay. The Statement Check reads the same knob (`agents/report_writer.py:3255`); a smaller Statement Check batch also shortens the writer's check phase, and the same proof covers it. Expected: the verifier node falls from ~218 s (after O1) to ~100-150 s [INFERENCE: assumes output tokens grow roughly with figures judged; O8's per-call records test this].

**O6 — researcher with thinking disabled.** DeepSeek flash accepts `thinking_mode: disabled` (`providers/capabilities.py:73-79`), the one-time check already runs that way (`api/clarify.py:187-189`), and a per-agent override is a config field (`utils/config.py:36`, resolved at `:99-103`). The request-scoped `config_overrides` can set it for one run (`api/models.py:77-91`); note that `llm.model_overrides` is merged per agent entry, not per field (`utils/config.py:712-716`), so the override must restate `reasoning_effort` and `timeout`. It speeds both halves of the researcher: 75 decision turns (≤ 3,286 tokens each) and every page and owed extraction (up to 40,937 tokens). Accuracy: precision is structurally protected, because no finding reaches the report without its snippet on the page and, for figures, the Context Check. What can move is **coverage**: which URLs the loop picks, how many findings extraction recovers, and which targets they bind. That is exactly "no expected drop in coverage", so it needs paired live evidence. Two obstacles: the evaluation harness runs everything with thinking enabled (`evaluation/config.py:84-91`), so it needs a toggle first; and a narrower version (extraction only, decisions keep thinking) needs a per-operation profile name (small code). Temperature 0.7 is sent when thinking is off (`providers/capabilities.py:78`, `config.yaml:81`).

**O7 — planner effort.** The planner is strictly sequential: 2 ReAct turns, the draft, a lint repair when deterministic lints fire (`agents/planner.py:3668-3681`), the review on every pass (`:3732-3755`), a review repair when the review says unsound, and a confirming review (`:94-100`); all at `max` (`config.yaml:32-34`). Tamil's 5 calls took 294.7 s. Two variants: everything at `high` (config), or only the two repair calls at `high` while draft and review stay at `max` (small code, reusing the per-call effort the truncation re-ask already uses). The planner is where coverage is decided, so it is the last experiment, after O5 and O6 have their proof tooling in place.

**Obligation-aware early stop (later experiment, not ranked).** `target_obligation_completed` was `true` for all nine topics, but it is computed after extraction (`agents/researcher.py:4204-4211`) and nothing in the loop reads it. A live sufficiency rule (for example: every own required target bound by findings from at least two publishers, and two turns without a new bound finding) could end loops early through the existing hook (`agents/react.py:747-748`). It removes evidence by design, the human set 15 turns deliberately (`2d5e24e3`), and the heaviest topic is the one least likely to stop early. Only worth testing after O6.

**Config left alone.** `sub_topic_concurrency`, `extraction_concurrency`, `source_scoring_concurrency`, `writer_section_concurrency`: never binding here; lowering them lengthens runs (`stall-investigation-fable.md:140`). `timeout`, `idle_timeout`, `retry_count`: set by the stall investigation for tail safety. `max_tokens` and the other caps: closed human decision, and no call was truncated. `report_reviewer.reasoning_effort`: §6.

### 3.2 Small code changes

**O2 — finalize.** `_publish` awaits `publisher.publish_finding` once per cited finding (`graph/nodes.py:629-646`), and each call goes tool span → `LongTermMemoryBridge.save` → `LongTermMemory.save` → `save_many([entry])` (`runtime/memory_bridge.py:64-101`, `memory/long_term.py:220-259`). The documents are written in ~14 ms; `memory/chroma/chroma.sqlite3` was last written 42 s later, ~160 ms per entry. The local embedder calls chromadb's `DefaultEmbeddingFunction` (`providers/embeddings.py:148-160`), and in the installed chromadb 1.5.9 its `__call__` builds a new `ONNXMiniLM_L6_V2()` every time (`.venv/Lib/site-packages/chromadb/api/types.py:952-965`), whose model and tokenizer are cached only per instance (`.venv/Lib/site-packages/chromadb/utils/embedding_functions/onnx_mini_lm_l6_v2.py:198-256`). So every write reloads the ONNX session [INFERENCE: this dominates the 160 ms; test: time 262 single writes vs. one `save_many` of 262]. Fix: (a) collect the payloads and call `save_many` once (the store already batches embeddings and does one upsert), falling back to per-entry writes if the batch fails so per-finding failure records survive; (b) hold one `ONNXMiniLM_L6_V2` instance in `LocalEmbeddingProvider`, which also speeds every `query_memory` call made under the tool lock. The report cannot see the difference: the status is decided before finalize, and the quality record is rendered before the memory writes (`graph/nodes.py:748-759`). Keep the writes inside finalize rather than after the session ends: moving them to the background would let a shutdown silently drop them. It matters to the reader because `/report` answers only once the runner has returned (`api/app.py:411-421`, `api/sessions.py:536-570`).

**O3 — the owed round.** Within one page, `_retry_owed_batches_for_page` awaits batch 1, runs `build_findings`, then awaits batch 2 (`agents/researcher.py:3522-3564`). Batch 2's request is built from its own units only (`:3525-3557`); the one cross-batch dependency is `admitted_keys`, which `build_findings` extends (`:3568-3578`). Issue both calls together under the same per-topic gate, then run `build_findings` for batch 1 and batch 2 in that order. Given the same replies, the output is identical. The stall investigation's option 7 (start each page's owed batch as soon as its own extraction ends) is a different change: which passages are owed depends on every page's merged findings (`unanswered` at `:3962`), so it changes requests and needs proof.

**O9 — the forced last turn.** On turn 15 the model is told to answer without a tool (`agents/prompts.py:261-267`). The answer text is merged into `ReActRun.final_answer` (`agents/researcher.py:395-437`) and nothing downstream reads it (the only other reader is the planner's own loop, `agents/planner.py:2649-2658`). Ending the loop after the last tool turn with a synthetic `finished` step saves one decision call per topic. The UI does not show `iterations`, so the change is invisible there.

**O8 — instrumentation.** Needed before O3, O4, O5 and O6 can be sized from data:
- `researcher.tool_call`: `duration_s` and `lock_wait_s` (tool latency today starts only after the lock is taken, `tools/base.py:108`).
- `researcher.sub_topic.completed`: `extraction_wait_s`, `owed_round_s`, `owed_calls`, `slowest_page_s` (the per-page figure exists, `agents/researcher.py:3422`, `:3478`).
- `telemetry.stages[]`: per-call start offset, output tokens and reasoning tokens (today only the slowest call per stage and a run-wide reasoning total are kept, `observability/run_telemetry.py:252-281`).
- The planner's operation names on its calls (draft, lint repair, review, review repair, confirming review), so the 193.6 s call can be attributed.

**O10-O12** are listed in §2 and need no more detail.

### 3.3 Structural changes (removing a stage barrier)

What any barrier removal has to respect, verified in code:

| Dependency | Where | Why it blocks starting a stage early |
|---|---|---|
| The researcher waits for every topic | `agents/researcher.py:4786-4792` | The node returns once, with all findings folded in plan order (`:4810-4827`) |
| Cross-topic dedup before verification | `agents/evidence_verifier.py:993` (`deduplicate_findings`) | Its second fold merges findings of different topics on a passage key and unions their `target_ids`; the key includes the verification outcome once one exists (`agents/identity.py:121-161`) |
| The verifier needs the evaluator's issuer and page date for each page | `agents/evidence_verifier.py:476-506`, `:1039-1040`, `:948-950` | Without them the prompt's page-date line and attribution resolution change |
| The evaluator reads the reader's notes (relevance only) | `agents/source_evaluator.py:1105-1109` | Scoring a source early means scoring it before later notes arrive |
| The source cap and the whole-list snapshots | `agents/source_evaluator.py:1188-1192`; `agents/evidence_verifier.py:1115-1134` | Written once per pass over everything |
| The writer's labels are global | `agents/report_writer.py:2205-2208`, `:3092` | F-labels are assigned over the whole verified snapshot before any part is drafted |
| The bottom line needs every checked section | `agents/report_writer.py:3028-3042` | It is built from kept section statements |
| Extra passes and note passes loop back to the researcher only | `graph/orchestrator.py:211-218` | A pipelined design must still re-run the downstream stages for the pass's new findings |

**P1 — per-topic pipelining.** When a topic's research ends, score its new sources, verify its findings, and draft its part; the bottom line, reviewer and finalize wait for all parts. On these runs the heaviest topic finishes last (§1.4), so its own chain stays critical: after O1, P1 saves roughly the evaluator stage and a little verifier queueing, **~40-60 s** [INFERENCE]. It needs dedup keyed so a later topic can merge into an already-verified finding, per-topic snapshots, stable labels assigned at render time, and a decision about notes that arrive after a topic's sources were scored. **Not recommended**: small gain, large change, and the most expensive option for the UI (§5).

**P2 — streaming verification inside research.** Score a source when its read is admitted, and Context-Check each finding when its page extraction returns (both happen during the loop today: `agents/researcher.py:4398-4414`, `:3462-3470`). After research only the tail's findings remain. Gain **~90-200 s** [INFERENCE], less once O1 and O5 have shrunk the verifier. Its accuracy questions: dedup must move after verification without changing which findings merge; a note that arrives mid-research affects the relevance score only of sources scored after it (verification itself stays note-free); owed re-extractions produce findings that must still be verified. Worth designing only if steps 0-4 and X1-X3 leave the target unmet.

**P3 — research on the draft plan while the review runs. Rejected.** Every repair regenerates the whole plan and re-stamps topic and target ids by position (`agents/planner.py:3297-3321`, `:1467-1513`); `sub_topics` merges by appending and target ids by union, so a draft's entries could never be removed (`utils/types.py:2181-2188`, `:2196-2201`). In Latte the review found the plan unsound, so research on the draft would have followed defects the review exists to remove.

**P4 — speculative planning during the one-time check. Rejected.** The reader's answers change the plan: in Tamil the reader typed "this year" where the best guess was "All time". Speculation would usually be thrown away, for at most the 25 s the reader took.

---

## 4. Recommended sequence and realistic target

Assumptions: the Tamil run is representative (five topics, one figure-heavy); generation speed stays at 218-273 tokens/s; each experiment is adopted only if it passes its proof; savings on the same stage are not added twice.

| After step | Contents | Expected end-to-end | Mostly from |
|---|---|---|---|
| Baseline | — | 1,530 s (25.5 min) | — |
| 0 | O8 instrumentation | 1,530 s | Measurement only |
| 1 | O1 (config) | ~1,460 s | Verifier: 290 → ~218 s |
| 2 | O2 | ~1,420 s | Finalize: 42 → ~3 s |
| 3 | O3, O9, O10, O11, O12 | ~1,360-1,410 s | Research tail and last turn |
| 4 | O4 | ~1,320-1,380 s (**22-23 min**) | Research loop stalls |
| X1 | O5 if it passes | ~1,230-1,300 s | Verifier: ~218 → ~100-150 s |
| X2 | O6 if it passes | ~1,100-1,220 s (**18-20 min**) | Research loop and tail |
| X3 | O7 if it passes | ~980-1,130 s (**16-19 min**) | Planner |
| (P2) | Only if still needed | ~900-1,050 s (15-17 min) | Evaluator and verifier off the critical path |

Two runs of the same question can differ by minutes (web content, source mix, the heaviest topic), so judge each step on the telemetry of the stage it targets, not on the total.

---

## 5. Impact on the UI decisions

What the running stage assumes today (from `web/` at `f4282818` and the picks of 2026-09-30):

- **One active step.** `active` is a single value, "the successor of the last `graph.node.completed`" (`web/lib/run-state.ts:59`, `:183-192`; `docs/design/DESIGN.md:810-816`); `openNode` is the single last-started node (`web/lib/run-state.ts:178-182`). The e2e tests read one `li[data-state='active']` (`web/e2e/running.spec.ts:18-19`, `:39-40`).
- **One hand-off at a time.** `BriefSpine` holds one `{from, to, awaiting}` slot (`web/components/BriefSpine.tsx:16`, `:33-56`).
- **"Stopped at <step>."** The Stop canvas names one step ("Stopped by you · at Researching", `.superpowers/progress-canvas/project/Stop.dc.html:68`); halted views read `openNode`.
- **Counters whose total comes from the step before.** Verifying's count is `researcher.research.completed.findings` (`web/lib/run-state.ts:224-228`); the picked canvases use "N of 44 rated" (Evaluating A), "N of 384 checked" where 384 is Researching's total (Verifying C), and "N of 39 sentences … section W of 5" with the bottom line after the sections (Writing E).
- **Ticker sources.** Verifying C and Writing E show one item at a time from their own step.
- **The Researching checklist** is the only per-topic list (`web/lib/briefs.ts:61-68`).
- **Notes** are acknowledged in the active row and remember where they landed (`web/lib/briefs.ts:58-60`; `note.where = run.active`).

| Option | Which assumptions break | How the UI could show it, keeping the picks as intact as possible | UI cost |
|---|---|---|---|
| O1, O2, O3, O5, O6, O7, O8-O12 | None: the seven steps still run one after another and emit the same events | Nothing to change. O5 makes Verifying C's "Just checked" ticker update in more, smaller steps; O2 makes Publishing a brief flash and brings the report ~40 s sooner; O7 shortens Planning B's "Checking the plan…" beat | **Free** |
| O4 (tool lock) | None. Tool calls may complete in a different order, and if a turn's calls run together their `researcher.tool_call` events may come out of turn order; only the Failed/Stopped tool count reads them | Nothing to change. Stop must cancel more in-flight fetches (it already cancels page tasks, `agents/researcher.py:4542-4544`) | **Free** |
| P1 (per-topic pipelining) | All of them: several steps active at once; completions interleave, so the derived active row jumps backwards and forwards; one hand-off slot; Verifying's and Writing's totals are unknown until research ends; tickers interleave topics; "stopped at" has no single step; a note's `where` is ambiguous | (a) **Per-topic rows spanning steps**: each Researching checklist row grows stage marks (reading → rated → checked → drafted) and the spine rows below show aggregate progress. Keeps the spine and the checklist, but Evaluating A, Verifying C and Writing E become sub-views. (b) **Several active rows**: `active` becomes a set, one hand-off per row, "Stopped at Researching and Verifying". (c) A **sequential facade** that replays pre-computed checks as if live would be dishonest and is not proposed | **Expensive** (run-state model, BriefSpine, every running-stage test, three picked canvases) |
| P2 (streaming verification) | Single active step and the hand-off during research; Evaluating's and Verifying's denominators; the order in which rows start | Keep the seven-row spine. While research runs, **let Evaluating and Verifying be active alongside Researching** with their own live briefs (Evaluating A's bar and Verifying C's ticker work unchanged, but their "of N" grows as findings arrive: "212 checked so far"). When research ends they finish their tails and hand off to Writing as today. "Stopped at" names the earliest active row ("at Researching · checking alongside") | **Medium-high**: `active` becomes a set for rows 2-4 only; the hand-off and the e2e single-active assertions change; Writing, Reviewing and Publishing untouched |
| P3 (research on the draft plan) | Planning B's slots ("Checking the plan…", "Fixing one topic…") would show topics already researching; the decision that a planning-time note joins the plan conflicts with research already under way | Not proposed (rejected on accuracy) | Expensive |
| P4 (speculative planning during the check) | The check stage would show planning behind the questions | Not proposed (rejected) | Medium |

Every recommended step (0-4, X1-X3) is UI-free, so they come first. The only barrier removal worth considering later, P2, is also the cheaper of the two for the UI, because it overlaps only rows 2-4 and leaves Writing, Reviewing and Publishing strictly sequential.

---

## 6. Looks slow, but should not be changed

| Item | Cost here | Why it stays |
|---|---|---|
| Reviewer at `max`, one whole-report call over all 302 labelled findings | 180 s | It is the acceptance rule's judge (`agents/report_reviewer.py:443-468`; mean ≥ 0.80, no material defect, every statement dispositioned). Tamil passed at 0.814, just over 0.80. It sees uncited findings so it can catch evidence left unwritten, a defect earlier audits found (`docs/evaluation/live-audits/audit-run6-fable.md:132`). There is no reviewer suite to prove a lower effort neutral |
| Statement Check on every section sentence, and again on the bottom line | Part of the ~107 s between the slowest draft and the writer's end | "Every report sentence Statement-Checked" is part of the brief's definition of accuracy |
| The bottom line waiting for all parts, and its one re-ask on a refusal | The rest of those ~107 s | It is built from checked statements (`agents/report_writer.py:3028-3042`, `:2693-2766`) |
| Writer effort or thinking | 239 s slowest draft | Lower deliberation risks more refused sentences and weaker honesty compliance; the Statement Check would catch errors, but coverage would drop |
| Figure Match and the Context Check on every figure-bearing finding | 290 s | The evidence standard. Only scheduling (O1) and batch size (O5, with proof) are touched |
| Plan review on every pass and the confirming review | One or two `max` calls per run | They catch missing question dimensions before any research is spent |
| Output caps at the provider maximum; 1,800 s timeouts with a 150 s idle timeout; 5 retries | tail risk only | Closed human decision (2026-09-25) and the stall investigation's fixes |
| robots.txt compliance | 39 of ~99 scrapes denied | A policy, not a latency knob. O4 only caches the file per host per run |
| 15 turns and 40 tools per topic; the 120-findings cap | ~150 s loop | Human rulings (`2d5e24e3`); the cap runs after extraction, so changing it saves no time |
| Hedged or duplicate requests; lower concurrency | — | Rejected by the stall investigation (`stall-investigation-fable.md:139-140`) |
| The one-time check's 60 s wait | 25 s (the reader) | The reader's time, bounded by `hitl.answer_wait_s` (`config.yaml:218`) |

---

## 7. How to prove a change is accuracy-neutral

**Tier 1 — by construction, offline, free.** For O1, O2, O3, O9, O10, O11, O12: the unit tests named for each in §2, then the replay matrix. The replay matrix runs the real graph and agents with scripted model, search, pages and memory (`e2e_evaluation/replay.py:761-1628`, `:1989-2099`; 35 rows, `e2e_evaluation/replay_matrix.py:3163-3569`) and checks every row's invariants, including `no_false_verification` and `statement_failure_keeps_sentences` (`e2e_evaluation/replay.py:3049-3171`), over 3 repetitions with 0 network attempts (`e2e_evaluation/runner.py:231-263`, `:337-339`). Run it with `python -m deep_research.e2e_evaluation suite` (`e2e_evaluation/runner.py:358-398`). For a pure scheduling change, add the request-digest check from `tests/test_graph/test_reader_notes_replay.py:37-40`, `:89-91`, `:117-125` (sorted per-request hashes plus the count): equal digests prove every model saw exactly the same input. It is order-insensitive by design. O4 can reorder admissions across topics and so change later decision prompts; it is proved by the matrix outcomes and invariants, not by digest equality. The scripted model discards `reasoning_effort` (`e2e_evaluation/replay.py:842-855`), so tier 1 says nothing about O5-O7.

**Tier 2 — stage replay on frozen state (to build; model calls only, no search).** Nothing today can push a recorded run's evidence through a changed stage: checkpointing is in memory and off (`config.yaml:212`, `graph/orchestrator.py:246-253`). Build it: write `dump_state` (`graph/state.py:196-204`) to disk at the evaluator→verifier and verifier→writer boundaries of a live run, and a runner that loads the file and runs one agent under settings A and B. For O5: run the verifier on the frozen Tamil and Latte states twice at batch 5 (the control's own noise floor) and twice at batch 2; compare each figure's kept/dropped status, corrections and dropped reasons, and each finding's status (`verified`, `verified_corrected`, `quoted`, `dropped`). Pass: batch 2 agrees with batch 5 at least as often as batch 5 agrees with itself, and drops no more figures. The same tool re-runs the writer's Statement Check at a different batch size.

**Tier 3 — the per-agent harness (live DeepSeek and LangSmith).** Suites exist for planner, researcher, source_evaluator, evidence_verifier and report_writer (`evaluation/models.py:49-64`); 3 repetitions per controlled case, pass at case average ≥ 0.80 and every repetition ≥ 0.65, live cases ≥ 0.75 (`config.yaml:225-230`; `evaluation/runner.py:535-605`, `:701-706`). Run one agent at another effort with `python -m deep_research.evaluation agent planner --reasoning-effort high` (`evaluation/cli.py:98-122`, `:148-266`). Gaps to close first: thinking mode is fixed to enabled (`evaluation/config.py:84-91`), so O6 needs a toggle; the harness records no latency per case (`evaluation/models.py:548-578`, `:751-773`); and a per-agent run flattens every call to the target's effort, including the Statement Check it makes under the verifier's name (`evaluation/config.py:605-613`).

**Tier 4 — paired live runs (paid, under the spend rules).** Outputs vary between identical runs, so first run the control twice on each question to measure its own spread, then the treatment. Use at least three questions of different shapes, including one figure-heavy. From each `-quality.json` (the same field names every run writes): `quality.hard_failures` empty; `quality.unjudged_sentences` and `unresolved_citations` empty; `review.status` scored and `review.mean_score` within the control's spread; no new material defect; required `answered_target_ids` not fewer and `not_found` not longer; cited sources and publishers not fewer than the control's lowest; verified + corrected findings within the control's spread. Request-scoped `config_overrides` (`api/models.py:77-91`) run the treatment arm without editing `config.yaml`.

| Change | Tier needed |
|---|---|
| O1, O2, O3, O9, O10, O11, O12 | 1 |
| O4 | 1, plus one live run watching `lock_wait_s` |
| O5 | 1 and 2 (plus the verifier suite) |
| O6 | 1, 3 (after the toggle) and 4 |
| O7 | 3 and 4 |
| P1, P2 | 1 through 4 |

---

## 8. Corrections to the brief's premises

1. **`researcher.tool_call` events carry no timing metadata.** Their keys are `error_type`, `iteration`, `proposal_id`, `sub_topic`, `success` and `tool`, and all of a turn's events are stamped when the whole turn ends (`agents/react.py:743-746`). Per-tool durations are only in LangSmith spans (not read here), and even those exclude the time spent waiting for the lock (`tools/base.py:108`). O8 adds them.
2. **The topic completion times** (+471/533/546/568/625 s) are counted from session creation, including the 25 s check; from graph start they are 446.0/508.1/520.8/542.7/599.6 s. The tail after the last tool call (~160 s in the brief) is two parts: the forced finish turn (4-12 s) and the post-loop extraction rounds (3-153 s).
3. **"Planner draft + review + repair"** fits Latte (draft, review unsound, review repair, confirming review: 6 calls with the two ReAct turns, and two recorded plan defects). In Tamil the repair was the deterministic **lint** repair before the review, and the review found the plan sound (5 calls, no defect recorded). Both are inferred from call counts and recorded errors; O8 names each call.
4. **Finalize's 42 s is not model time.** It is 262 single memory writes; the documents take ~14 ms.
5. **The verifier's 45 calls include the writer's 11 Statement Check calls,** which run under the verifier's name. The verifier node itself made 34.

---

## 9. Open issues and inferences

1. **Which call was the 217.5 s verifier call** [INFERENCE: a Context Check call in the verifier node]. The writer's arithmetic rules out a 217 s Statement Check on its slowest part, but not on a lighter one. If it was a Statement Check, O1 saves more, not less. Test: O8's per-call records.
2. **The all-topic stalls are tool-lock queueing** [INFERENCE]. Test: O8's `lock_wait_s`; O4's benefit is sized from it.
3. **No planner-at-`high` quality measurement exists.** `config.yaml:17-23` calls the per-agent efforts "measured to be at least as good", but the recorded evaluations ran the planner at `max` only (`docs/superpowers/validation/2026-09-16-output-quality-baseline.md:459-504`; `docs/superpowers/2026-08-25-planner-evaluation-fix-log.md:13`). The 2026-09-15 records that `max` "broke the planner" (`docs/superpowers/specs/2026-09-15-production-ready-reports-design.md:153-155`) match a schema bug since fixed (`agents/planner.py:729-736`), not an effort effect. O7 must therefore start from a proper `high` vs `max` suite run; this audit does not treat either direction as measured.
4. **O4 amends design decision D9** ("tool calls stay serialised run-wide under one lock (one body, one download)", `docs/superpowers/specs/2026-09-24-evidence-verifier-pipeline-design.md:47`). Single-flight per URL keeps "one body, one download"; giving up "one tool call at a time" is a decision for the human, not for this audit.
5. **The size of each O2 part is not profiled** [INFERENCE: the per-write ONNX reload dominates]. Test: time one `save_many` of 262 entries against 262 `save` calls on the same machine. Chroma lives in a OneDrive-synced folder, which may add per-write cost.
6. **O5, O6 and O7 savings rest on reasoning share and figure counts no artifact records** [INFERENCE]. Test: O8's per-call output and reasoning tokens, regressed on figures per batch (O5) and per operation (O6, O7).
7. **Outside latency, noticed in passing.** The scraper's tool description says a host that refused "will refuse again: do not retry it" (`tools/web_scraper.py:104-105`), while the researcher's prompt says a refusal is about the URL, not the publisher (`agents/researcher.py:200-204`). Web-search retries catch httpx exceptions only, while the Tavily client raises its own types, so Tavily 429 and 5xx responses are never retried (`tools/web_search.py:167-171`). Every memory write gets a fresh uuid, so the same findings accumulate in Chroma across runs (`runtime/memory_bridge.py:76`). None of these changes a latency recommendation.
