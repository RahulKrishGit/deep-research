# Stall investigation — why single provider calls run 200–2,000 s, and what to change

**Blocking question.** Why do individual DeepSeek calls in live runs take 200–2,000 s, and what change bounds run length without hurting report quality?

**Already settled by the brief (not redone):** timeouts were raised from the 60 s default because timed-out attempts restart the whole generation (f6fc2c8); config was identical across runs 5–10 (`timeout: 1800.0` for every role, `retry_count: 5`, reviewer 1, `max_tokens: 393216`, `sub_topic_concurrency: 10`, `extraction_concurrency: 16`) — verified with `git show <head>:config.yaml` for fa38a20, b7839aa, 07797fa, ef95ccb, 63b5063. The DeepSeek rate-limit page documents keep-alive bytes during queueing and a 10-minute pre-inference cutoff.

**Decision.** The leading hypothesis (queued requests whose keep-alive bytes reset the read timeout until the server gives up) is **not** what the traces show. Three distinct things produce the long calls:

| Cause | Where it shows | Share of the damage |
|---|---|---|
| **A. Client-side event-loop blocking** during admission / cache re-validation of huge pages (O(passages × page) normalisation) | Runs 5, 6, 7, 9 — every researcher-stage "silent gap" of 150–342 s; 24–26 calls "ending" in the same second | Run 9: 1,345 s of the 1,964 s researcher stage; ~180–206 s in each of runs 5, 6, 7 |
| **B. Provider/network stalls** that non-streaming transport can only detect at the 1,800 s read timeout | Run 5: one attempt silent for 1,800 s then a 222 s retry (2,023.6 s call). Run 6: one 669 s source-evaluator call for 534 output tokens | 1,800 s in run 5; ~600 s in run 6 |
| **C. Genuinely long generations** at normal throughput (220–270 tok/s, 30–77k output tokens) | Planner at effort `max` (57–65k tokens, 272–284 s), writer section drafts (61–77k, 230–236 s), 120k-token page packets (44–62k, 165–255 s) | Sets the floor: runs 8 and 10 (no A, no B) were 29 and 22 min |

Recommendation (one change first, one second): **fix the admission complexity** (evidence.py `build_read_record` and `validate_cached_read`, plus the double validation per cache hit in acquisition.py), which removes A entirely and is provable offline; **then** land streaming with an idle timeout to bound B. Details, evidence and falsifiers below.

---

## 1. Root causes, with evidence

### 1.1 How the numbers were reconstructed

- Traces for runs 5–10 fetched with a copy of `scratch/ev1_trace_fetch.py` writing to `%TEMP%` (682/758/798/1,068/832/682 LangSmith runs; trace ids: run 5 `01a0dc6b-aa98-7b33-b1e6-b7a1d26e6a91`, run 6 `01a0dd11-b7e0-7182-b3bd-4daeb6872640`, run 7 `01a0dd83-713e-7042-9804-70c19ce390e0`, run 8 `01a0de56-cec0-7982-ac83-0fa7c0d5b19b`, run 9 `01a0dedb-4fb5-7d50-9162-8a2695382302`, run 10 `01a0df24-9b34-7d81-b5c4-e474212ec248`).
- Each LLM span carries `request_attempt` (transport attempts), `structured_attempt`, usage (`input_tokens`, `output_tokens`), `finish_reason_category`, start/end. It does **not** carry per-attempt timings, time-to-first-byte, or `completion_tokens_details.reasoning_tokens` (`_usage_from_response` maps only input/output/total), so reasoning vs. content tokens cannot be split from the data; the API does report `reasoning_tokens` (Chat Completions page, usage schema).
- **What "slowest call" measures** (`deepseek_provider.py`): `started_at = perf_counter()` is taken immediately before `with_retries(...)` and `seconds=perf_counter() - started_at` is recorded after it (e.g. `complete` at lines ~940–975, `_structured_attempt` ~1041–1063, `complete_react` ~1258–1305). So one figure spans **all transport attempts plus the backoff sleeps**. It does **not** include waiting on our own gates: `RequestBudget.reserve` (request_budget.py) only counts under a lock and never blocks, and the researcher's `extraction_gate` semaphore is acquired *before* `complete_structured` is called (`_extract_one_page`, researcher.py ~3348). It **does** include any time the event loop is blocked while the response sits unread in the socket — which is cause A.

### 1.2 Cause A — the event loop is blocked by page admission of ~1 MB pages

**Signature in every affected run.** Windows of 150–342 s in which *no span of any type* (tool, chain or LLM) starts or ends, each followed by a burst of 16–39 span events in the same second, including `react_tool_turn` decisions with 440–2,001 output tokens that "took" 208–688 s (2–3 tok/s):

| Run | Silent window (UTC) | Length | Events in the 2 s after | LLM call that starts at the window's end | Read that precedes the window |
|---|---|---|---|---|---|
| 5 | 06:44:10→06:47:36 | 205.7 s | 30 (26 ends) | extraction, 124,088 in / 61,658 out | `topostext.org/work/498`, 951,879 chars, scrape ended 06:44:10 |
| 6 | 09:45:58→09:48:58 | 180.6 s | 39 (19 ends) | extraction, 121,053 in / 54,558 out | same page, scrape ended 09:45:58 |
| 7 | 11:48:57→11:51:53 | 175.6 s | 17 (15 ends) | extraction, 122,895 in / 52,979 out | same page, scrape ended 11:48:57 |
| 9 | 18:00:09→18:03:02 | 173.3 s | 16 (10 ends) | extraction, 122,395 in / 49,170 out | same page, scrape ended 18:00:09 |
| 9 | 18:05:14→18:10:53 | 338.8 s | 1 (0 ends) | extraction, 122,871 in / 43,892 out (parent: iteration 7 of a *different* sub-topic) | none visible — see cache note |
| 9 | 18:10:53→18:16:35 | 342.0 s | 28 (24 ends) | extraction, 123,728 in / 14,154 out (iteration 7 of a third sub-topic) | none visible — see cache note |
| 9 | 18:16:46→18:19:20 | 153.4 s | 31 (14 ends) | extraction, 118,431 in / 44,796 out | `topostext.org/work/200`, 906,307 chars, scrape ended 18:16:46 |
| 9 | 18:20:07→18:25:45 | 337.6 s | 25 (12 ends) | extraction, 124,347 in / 53,969 out | `archive.org …plutarchslifeofl00plut.pdf`, 373 chunks, read ended 18:19:53; plus `Appian/Civil_Wars/2*.html`, 235,916 chars at 18:19:55 |
| 8, 10 | none ≥ 45 s with a burst | — | — | — | no page ≥ 300k chars was read |

Run 9's five windows total **1,345 s** of its 1,964 s researcher stage. Each window ends at the instant a ~120k-input-token extraction call starts — the packet at the 400,000-char cap that only a page larger than the 200,000-char admission budget produces.

**Why this is client-side and not the provider.** (i) Tool spans (scraper, search, memory) never straddle a window, and none start inside one; a server-side hold would not stop local memory saves and scrapes. (ii) 24 responses of 7k–61k output tokens cannot complete in the same second by chance; they were processed together when the loop resumed. (iii) The langsmith span end is stamped in `run.end()` (tracker.py `_end_remote_run`, called from `_span`'s `finally`) *before* `await manager.__aexit__()`, which is `aio_to_thread` in langsmith 0.10.15 — a real yield — so the coroutine that just finished an LLM span yields, other tasks stamp their events, and then it resumes into synchronous work. That is exactly the observed order: an extraction reply ends (e.g. 18:05:09, 35,850 tokens), a few other events are stamped until 18:05:14, then 339 s of silence.

**The code that blocks (read, then profiled).**

- `src/deep_research/agents/evidence.py:2965-2975` (`build_read_record`): for every passage, `excerpt_matches(canonical, passage_text)`; `excerpt_matches` (`:189-201`) calls `cosmetic_text(text)` on the **whole page body** (twice on a miss: `join_hyphenation` both ways). Cost = passages × page length.
- `src/deep_research/agents/evidence.py:3178-3185` (`validate_cached_read`): the same per-passage loop over the whole stored body.
- `src/deep_research/agents/acquisition.py:1663-1669` (`ToolPolicyDecision` cache short-circuit) **and** `:1933-1939` (`_read_observed`): a cache hit runs `validate_cached_read` **twice** — once to serve the result, once to admit it. A cache-served result skips `tool.execute` (`react.py:617-621`), so it leaves **no tool span** in the trace, which is why run 9's two 340 s windows show no read before them. [INFERENCE, tightly bounded: 2 × ~170 s = the 339 s / 342 s windows; the two calls that start at their ends are 122–124k-token page packets created in iteration 7 of two sub-topics other than the one that scraped the page.]

**Offline reproduction (synthetic prose, no network):**

| Page size | `build_read_record` | Passages | Notes |
|---|---|---|---|
| 200,000 chars | 2.7 s | 407 | `re.Pattern.sub` inside `cosmetic_text` = 2.2 s of it |
| 906,307 chars | 55.8 s (admission total 54.4 s) | 1,847 | 2,254 `excerpt_matches` calls → 4,508 whole-body `cosmetic_text` calls |
| 906,307 chars, normalise body once then substring-check each normalised passage | **0.20 s**, identical result (`all_ok=True` both ways) | 1,847 | 277× faster |

Real pages cost ~3× the synthetic figure (173–206 s for 951,879 chars in four runs): more passages, Unicode/NFC work and line-break hyphen regexes. The other candidates were profiled and ruled out: packet build `policy.context(limit=400000, read_ids=[…])` = 0.14 s; decision context = 0.00 s; `build_findings` for 40 findings against a 200k page = 0.93 s (~23 ms/finding; `_locate_snippet_body` LRU cache works, 2 calls).

**Consequence for the telemetry.** Run 9's "slowest call researcher 812.4 s" is the 122,395-token page extraction that started 18:03:03 and whose response could not be read between 18:05:14 and 18:16:35 (the loop was blocked except for 0.2 s at 18:10:53). At the run's single-attempt median throughput (221 tok/s) its 49,170 tokens need ~220 s [INFERENCE]; ≥ 590 s of the 812 s is loop-block. The same applies to all 25 calls that end at 18:16:35 and the 24 that end at 06:47:36 in run 5.

### 1.3 Cause B — genuine provider/network stalls, detectable only at the 1,800 s read timeout

- **Run 5, 2,023.6 s call** (researcher extraction, 21,610 in / 42,158 out, `request_attempt=2`, `finish=stop`, started 06:41:30, ended 07:15:13). The span has no per-attempt timing, but the arithmetic is tight: 2,023.6 s − 1,800 s (configured read timeout) − 1 s (first backoff) = 222.6 s for attempt 2, which at 190 tok/s is exactly 42,158 tokens. So attempt 1 received **no bytes for 1,800 s** — the httpx read timeout fired (`APITimeoutError` → `ProviderTimeoutError`, retried). [INFERENCE on the split; the total, attempts and tokens are measured.] The stage's other five sub-topics finished at 06:51:24; this one call held the researcher stage open until 07:15:13 (1,429 s of the run with a single request in flight).
- **Run 6, 669.0 s source-evaluator call** (9,996 in / **534 out**, attempt 1, `finish=stop`, then failed schema validation at `$` and was repaired by a 5,311-token second structured attempt). Nothing else was in flight from 09:54:20 to 10:04:47, so this is provider-side: either ~10 minutes in DeepSeek's scheduler (keep-alive empty lines flowing, so no read timeout) or a mid-request stall that resumed. The data cannot tell which; per-attempt TTFB would.
- Field reports match this class: DeepSeek streams that go silent mid-`reasoning_content` with the connection open, "a few percent of streams on long reasoning turns", still reproduced 2026-09-14→17 after the issue was closed; clients recover only via an idle watchdog and one client lowered its DeepSeek stale-stream floor from 600 s to 180 s (deepseek-ai/DeepSeek-V3 #1608). Another report describes a 365 s zero-output streaming stall where the non-streaming retry answered in 6 s (same issue, 2026-09-15 comment).
- **What the 1,800 s timeout is doing:** `_build_client` passes `timeout=config.timeout` (a float) to `AsyncOpenAI` and each request passes `request["timeout"] = effective.timeout` (deepseek_provider.py:122-129, 886-887). A float becomes `httpx.Timeout(connect=read=write=pool=1800)`; httpcore applies `read` to **every `read()` call** in a `while True` loop (`httpcore/_async/http11.py:209-219`, verified in the installed 1.0.9), so it is an inactivity timeout with **no cumulative deadline** (httpx docs: "maximum duration to wait for a chunk of data"). With non-streaming requests nothing arrives during generation — the earlier finding that planner attempts failed at 61–63 s under the 60 s default proves DeepSeek sends no keep-alive at ≤ 60 s intervals while generating — so the read timeout must cover the whole generation, which is why it had to be 1,800 s and why a dead connection costs 1,800 s.
- **On the 10-vs-30-minute question:** the official rate-limit page today says the server closes the connection "if the request has not started inference after 10 minutes". I found no primary source for 30 minutes; the "31 minutes" seen in run 5 is our own 1,800 s read timeout plus ~78 s, not a provider cutoff. A server-side close would surface as `APIConnectionError` (retryable here), not as the 1,800 s wait.

### 1.4 Cause C — long generations at normal throughput (the floor)

Single-attempt throughput is stable across runs and unaffected by our concurrency (p50 216 / 233 / 230 / 237 / 221 / 245 tok/s for runs 5–10; peak 26–41 calls in flight; 0 rate limits in every run; the writer's six parallel 25–42k-token drafts in run 9 ran at 240–270 tok/s). So the long generation-bound calls are long because of output size: planner at `max` (57,533 tokens/272 s in run 5; 64,648/284 s in run 10), writer drafts (77,029/236 s in run 7), 120k-token page packets (61,658/228 s in run 5). These set the floor of ~22–29 min for P6/P4 at today's output volumes (runs 8 and 10 had no A and no B).

### 1.5 Every call ≥ 200 s in runs 5–10

Attempts = `request_attempt`; "burst" = spans ending within ±1 s of this call's end; class A = loop-blocked (ended in a burst, throughput well under the run median), B = provider stall, C = generation-bound. Reasoning tokens are not recoverable from the traces (see §1.1).

| Run | Agent / op | Att | In tok | Out tok | Wall s | tok/s | Start→End | Burst | Class |
|---|---|---|---|---|---|---|---|---|---|
| 5 | planner/structured_output | 1 | 4,019 | 57,533 | 272 | 211 | 06:34:07→06:38:39 | 1 | C |
| 5 | researcher/structured_output | 1 | 65,766 | 61,048 | 369 | 166 | 06:41:27→06:47:36 | 26 | C + ≥ 90 s of A (ended in the 06:47:36 burst) |
| 5 | researcher/structured_output | **2** | 21,610 | 42,158 | **2,024** | 21 | 06:41:30→07:15:13 | 3 | **B**: attempt 1 silent 1,800 s, attempt 2 ≈ 222 s |
| 5 | researcher/structured_output ×22 (49,103 … 12,400 out) | 1 | 10.5k–118.8k | 12.4k–53.9k | 212–344 | 57–161 | 06:41:52…06:44:04 → 06:47:36 | 25–26 | A (all end at 06:47:36) |
| 5 | researcher/react_tool_turn | 1 | 10,833 | 440 | 208 | 2 | 06:44:08→06:47:36 | 25 | A |
| 5 | researcher/structured_output (topostext packet) | 1 | 124,088 | 61,658 | 228 | 270 | 06:47:36→06:51:24 | 2 | C |
| 6 | researcher/structured_output ×5 (35,763 … 7,102 out) | 1 | 13.8k–32.5k | 7.1k–35.8k | 204–215 | 34–167 | 09:45:24…09:45:34 → 09:48:59 | 16 | A (all end at 09:48:59) |
| 6 | researcher/structured_output (topostext packet) | 1 | 121,053 | 54,558 | 204 | 267 | 09:48:59→09:52:23 | 1 | C |
| 6 | source_evaluator/structured_output | 1 | 9,996 | **534** | **669** | 1 | 09:53:38→10:04:47 | 1 | **B** (queue or stall; then schema-invalid reply) |
| 7 | researcher/structured_output ×6 (40,420 … 14,799 out) | 1 | 12.0k–35.0k | 14.8k–40.4k | 206–287 | 72–141 | 11:47:06…11:48:27 → 11:51:53 | 15 | A (all end at 11:51:53) |
| 7 | researcher/structured_output | 2 | 14,413 | 20,064 | 214 | 94 | 11:48:19→11:51:53 | 15 | retry + A |
| 7 | report_writer/structured_output ×2 | 1 | 46.0k–60.1k | 61.6k–77.0k | 230–236 | 268–327 | 11:59:42→12:03:3x | 1 | C |
| 8 | researcher/structured_output | 1 | 29,706 | 57,098 | 208 | 275 | 15:34:21→15:37:49 | 7 | C |
| 8 | report_writer/structured_output | 1 | 53,732 | 63,322 | 235 | 269 | 15:46:49→15:50:44 | 1 | C |
| 9 | researcher/structured_output | 2 | 66,187 | 43,921 | 223 | 197 | 17:59:20→18:03:03 | 10 | retry, generation-bound |
| 9 | researcher/structured_output | 1 | 16,826 | 32,735 | 221 | 148 | 17:59:21→18:03:03 | 10 | A (173 s window) |
| 9 | researcher/structured_output | 1 | 67,503 | 56,419 | 208 | 271 | 17:59:34→18:03:03 | 10 | C |
| 9 | researcher/structured_output (acoup 265k page) | 1 | 94,512 | 69,174 | 255 | 271 | 17:59:52→18:04:07 | 1 | C |
| 9 | researcher/structured_output (topostext packet) | 1 | 122,395 | 49,170 | **812** | 61 | 18:03:03→18:16:35 | 24 | A (≥ 590 s blocked) |
| 9 | researcher/structured_output ×21 (61,459 … 7,315 out) | 1 | 9.1k–72.6k | 7.3k–61.5k | 684–810 | 11–76 | 18:03:05…18:05:11 → 18:16:35 | 24 | A (all end at 18:16:35) |
| 9 | researcher/react_tool_turn ×2 | 1 | 12.1k–14.2k | 1,500–2,001 | 687–688 | 2–3 | 18:05:07→18:16:35 | 24 | A |
| 9 | researcher/structured_output | 1 | 31,502 | 23,546 | 846 | 28 | 18:05:14→18:19:20 | 11 | A (spans windows 2–3) |
| 9 | researcher/structured_output (cache-hit packet) | 1 | 122,871 | 43,892 | 507 | 87 | 18:10:53→18:19:20 | 11 | A |
| 9 | researcher/structured_output (topostext/200 packet) | 1 | 118,431 | 44,796 | 385 | 116 | 18:19:20→18:25:45 | 9 | A |
| 9 | researcher/structured_output ×4 (31,432 … 16,198 out) | 1 | 12.6k–18.5k | 16.2k–31.4k | 379–385 | 42–82 | 18:19:2x→18:25:45 | 9 | A |
| 9 | researcher/structured_output | 1 | 82,886 | 66,056 | 339 | 195 | 18:20:06→18:25:45 | 9 | C (+ some A) |
| 9 | researcher/structured_output ×2 | 1 | 12.3k–24.7k | 22.9k–45.1k | 425–486 | 54–93 | 18:20:07→18:27:12 / 18:28:14 | 2 | in flight across the 338 s block; the remaining 87–149 s are undetermined (queue vs. TCP flow control while unread) — needs per-attempt TTFB |
| 10 | planner/structured_output | 1 | 4,073 | 64,648 | 284 | 228 | 19:15:23→19:20:06 | 1 | C |

**What the data cannot tell, and the instrumentation that would:** per-attempt duration and time-to-first-byte (add to the provider's `_request` closures and the span outputs), reasoning vs. content tokens (`completion_tokens_details.reasoning_tokens`), whether run 6's 669 s was queue or stall (TTFB), and event-loop lag (a 1 s ticker recording max wake-up delay, reported on the Telemetry line).

### 1.6 Alternatives from the brief, ruled in or out

- (a) long generations at slow throughput — **out as a cause of the outliers**: throughput is stable at 216–245 tok/s p50; long calls are long by output size (C) or by A/B.
- (b) our concurrency stacking into per-account scheduling — **out**: 0 rate limits in every run; 26–41 in flight vs. the documented 2,500 limit; parallel calls kept full throughput.
- (c) retries after read timeouts restarting generation — **in for one call** (run 5, 1,800 s + 222 s); the other 4 two-attempt calls in runs 6–10 cost 18–223 s total.
- (d) sequential phases per read — **minor**: the post-loop tail of run 9's last sub-topic was 341 s (last-admitted page extraction 194 s → owed/cross-topic/dissent round 151 s, incl. a 32,768-token truncated re-extraction), all three sweeps already run in one `gather` (researcher.py ~4083-4122).
- (e) waiting on our own gates counted as call time — **out** for semaphores/budget (§1.1), **in** for the loop block (A), which the current measurement cannot distinguish from provider time.

---

## 2. Critical path — runs 9 and 10

**Run 9 (P6, 63b5063, 2,862 s):** planner 218 s ← one 34,605-token plan (151 s) + an 11,872-token follow-up (56 s), sequential. **Researcher 1,963 s** ← the sub-topic ending 18:31:31: loop 17:58:48→18:25:50 (10 iterations, inflated by the five loop blocks, 1,345 s) then a 341 s post-loop tail (Plutarch-PDF packet extraction 18:25:45→18:28:59, then the owed round 18:28:59→18:31:31 with a 32,768-token truncated re-extraction). Four other sub-topics ended 18:29:04–18:31:15, so removing the blocks shortens all of them together. Source evaluator 121 s ← a 78 s scoring call. Verifier 97 s ← an 82 s batch (20,096 tokens). Writer 330 s ← a 4-deep sequential chain: six parallel section drafts (max 165 s, 42k tokens) → statement checks (44 s) → bottom line (57 s) → final check (36 s). Reviewer 106 s ← one 294k-in / 25k-out call. Everything outside the researcher is generation-bound.

**Run 10 (P4, 63b5063, 1,326 s), no blocks and no stalls:** planner 370 s ← the 64,648-token plan at effort `max` (284 s) + a 17,098-token follow-up (77 s). Researcher 304 s ← the slowest of five sub-topics (loop to 19:23:16, then 191 s of post-loop extraction/owed calls; longest single call 131 s). Source evaluator 47 s. Verifier 170 s ← one 41,243-token batch (168 s). Writer 277 s ← drafts (135 s max) → checks (79 s) → bottom line (21 s) → check (41 s). Reviewer 136 s ← one 177k-in / 33k-out call. This run is the floor for today's design: 6 sequential stages each ending on its largest generation.

---

## 3. Ranked fix options

| # | Option | Expected wall-time effect on these runs | Risks | Where |
|---|---|---|---|---|
| **1** | **Make page admission linear**: normalise the body once per hyphenation mode and check `cosmetic_text(passage) in body` per passage (`build_read_record`, `validate_cached_read`); validate a cache hit once, not twice | Run 9: −1,345 s researcher (≈ 47.7 → ~25 min; [INFERENCE] some overlap is not perfectly recoverable, conservatively ≥ −1,000 s). Runs 5/6/7: −206/−181/−176 s. Run 10: 0. Also makes "slowest call" truthful | Pure CPU fix; identical results proven offline (55.4 s → 0.20 s, `all_ok` both ways). No provider or quality impact. Must keep the semantics (both hyphenation modes; NFC; casefold) | `src/deep_research/agents/evidence.py:2965-2975` and `:3178-3185`; `src/deep_research/agents/acquisition.py:1663-1669` + `:1933-1939` (pass the validated record through the cache-served `ToolResult`, or memoise by `(read_id, content_sha256)`) |
| **2** | **Streaming + idle timeout under the existing total cap**: `stream=True`, `stream_options={"include_usage": True}`, per-request `httpx.Timeout(connect=30, read=<llm.idle_timeout≈150>, write=60, pool=60)`, and `asyncio.timeout(effective.timeout)` (keep 1,800 s) around stream consumption; `reasoning_content` deltas and content deltas are bytes, so they reset the read timeout; SSE `: keep-alive` comments also reset it (correct: a queued request is alive and bounded by the provider's own 10-min cutoff, which then surfaces as `APIConnectionError` → existing retry) | Run 5: −1,650 s (the dead attempt fails at ~150 s instead of 1,800 s; retry 222 s). Run 6: −500 s if the 669 s was a silent stall, 0 if it was a keep-alive-fed queue [INFERENCE]. Runs 7–10: 0 | (i) Provider-wide transport change touching all four `_request` closures; (ii) tool-call assembly from deltas — use the SDK's `chat.completions.stream()` + `get_final_completion()` (present in openai 2.53.0) so `_native_outcome`/`_choice_text`/`_usage_from_response` keep reading a whole `ChatCompletion`; verify the accumulator tolerates the extra `reasoning_content` delta field; (iii) one unresolved field report of DeepSeek closing streams at ~4 min (LibreChat #16371, single report, no root cause) — our 165–284 s generations would straddle it; the one live run must include ≥ 5-minute streamed generations to check; (iv) a timed-out attempt may still have executed server-side, so a retry can double-bill; at 150 s the waste is ≤ 150 s of generation vs. 1,800 s today; (v) retry storms: unchanged (`retry_count: 5`, 1–16 s backoff) but each attempt is now bounded by idle silence, not 1,800 s | `src/deep_research/providers/deepseek_provider.py` `_build_client` (timeout), `_request_options`/`_responses_request_options` (`request["timeout"]` must become an `httpx.Timeout`, not a float), the four `_request()` closures (`complete`, `_structured_attempt`, `complete_react`, the Responses schema path at ~1433); `config.yaml` `llm.idle_timeout` (new) beside `llm.timeout` (kept as the total cap); `_translate_deepseek_error` already maps `APITimeoutError`→retryable |
| 3 | Per-attempt instrumentation + loop-lag monitor: attempt start/end, TTFB, `reasoning_tokens` in span outputs and the Telemetry line; a 1 s ticker recording max loop lag and "loop blocked N s" | 0 s directly; makes A/B/C distinguishable in one run, which the current "slowest call" cannot | None | `deepseek_provider.py` `_request` closures + `_set_span_result`; `observability/run_telemetry.py` (`record_call` gains attempt fields; new lag gauge); `main.py` Telemetry line |
| 4 | Hedged (duplicate) requests after ~p95 for idempotent extraction calls | Run 5: ~−1,300 s on the one stalled call; nothing for A; nothing for C | Duplicate billing on ~5% of calls, cancellation semantics, more provider load at exactly the moment it is slow; "retrying slow requests" is the classic route to throughput collapse. Option 2 gets the same bound with no duplicates | rejected |
| 5 | Lower concurrency (`sub_topic_concurrency`, `extraction_concurrency`) | Negative: lengthens runs; no rate limits or throughput loss to fix | — | rejected |
| 6 | Token / effort caps (planner `max`→`high`; cap the 400k-char packet's omitted-passage dump at the 200k admission) | Planner: 272–284 s → roughly half [INFERENCE, untested]; packets: 120k→~60k input tokens per huge page and shorter replies | Contradicts the 2026-09-25 "lift every limit" ruling; plan/extraction quality impact unmeasured — decide separately, after 1–3 | `config.yaml` `model_overrides.planner.reasoning_effort`; `acquisition.py` `build_acquisition_context` passage dump |
| 7 | Reorder sequential phases (start each page's owed batch after its own extraction; overlap writer checks) | ≤ ~150 s on run 9's tail, ≤ ~100 s on the writer chain | Design change in the researcher's merge order and the writer's statement-check pipeline | defer |

---

## 4. Verification

**Offline, before any live run.**
1. Option 1: a regression test that admits a synthetic 400k-char web page (≈ 800 passages) and asserts `build_read_record` and `admit_read_result` each finish in < 2 s (the repo already keeps a performance guard for the quote check), plus a counting test: monkeypatch `cosmetic_text` and assert the whole-body normalisation runs ≤ 2 times per admission regardless of passage count. Existing `tests/test_agents/test_acquisition.py` admission tests and the cached-read tests must stay green (semantics unchanged). e2e replay (34/34) is a free check that no packet or finding changed.
2. Option 2: transport tests with `httpx.MockTransport`/an async byte stream: (a) two SSE chunks then silence → `ProviderTimeoutError` after the idle timeout and `with_retries` issues attempt 2; (b) `: keep-alive` comment lines for longer than the idle timeout followed by content → completes (keep-alives keep the attempt alive); (c) a stream longer than the total cap → times out; (d) a tool-call stream reassembles into the same `NativeToolTurn` as the non-streaming fixture; (e) the last chunk's `usage` (incl. `prompt_cache_hit_tokens`) reaches `_record_tokens`.
3. Replay the run-9 trace-gap check offline against any new trace: "no window ≥ 45 s in the researcher stage with zero span events followed by a burst of ≥ 5 ends" — the script logic used here is ~40 lines over the JSONL the existing fetch script writes.

**Live, at most one run.** Re-run P6 ("Why did the Roman Republic fall?" — it reliably pulls `topostext.org/work/498`) with options 1 and 3 landed (2 optional but recommended in the same run so the ≥ 5-minute streamed generations get exercised). Pass criteria: researcher stage ≤ ~750 s (runs 7/8 without blocks: 555–713 s); Telemetry "slowest call" ≤ ~350 s and equal to a generation-bound call; loop-lag max < 5 s; zero silent-gap windows; no `APIConnectionError` at ~240 s in any streamed call (the #16371 check). Option 1 is safe to land before the next live run (offline-provable, no provider change). Option 2 is safe to land behind the same run's telemetry; if a ~4-minute stream close appears, fall back to non-streaming for that role only (`model_overrides.<role>.stream: false`) while keeping the idle timeout for the rest.

---

## 5. Recommendation

**Land option 1 now (with option 3's instrumentation), then option 2.** Option 1 removes the dominant cause outright: it is what stretched run 9 to 47 min and added ~3 minutes to runs 5, 6 and 7; it is provable offline (55.4 s → 0.20 s, identical results) and touches no provider behaviour. Option 2 bounds the rarer stall class (run 5's 1,800 s attempt) at ~150 s per attempt while keeping the 1,800 s total cap, and is the only mechanism that can, because non-streaming DeepSeek sends nothing during generation.

**Falsifier.** If, after option 1, the same P6 question still shows a researcher-stage window ≥ 150 s with no span events followed by a burst, the blocker is elsewhere — run the CLI once with `PYTHONASYNCIODEBUG=1` (asyncio logs every callback slower than `loop.slow_callback_duration`, default 0.1 s) and profile the named callback. If, after option 2, an attempt still waits the full total cap with keep-alive bytes flowing, DeepSeek is queueing beyond its documented 10 minutes and the answer becomes a TTFT deadline with re-queue budgeting, not an idle timeout.

**What I could not verify:** the exact split of run 5's two attempts (no per-attempt timing in the span; the 1,800/222 s split is arithmetic); whether run 6's 669 s was queue or stall; which two sub-topics re-read the 952k page from the cache (the cache path emits no tool span); the SDK stream accumulator's handling of `reasoning_content` deltas (needs the offline fixture in §4.2); and whether the ~4-minute stream close in LibreChat #16371 is real on `api.deepseek.com` (single unresolved report).

---

## 6. Sources

Repository evidence (plan tree `.worktrees/evidence-verifier`, head 63b5063): `src/deep_research/providers/deepseek_provider.py:122-129, 886-887, 940-975, 1041-1063, 1258-1305`; `src/deep_research/providers/retry.py:65-115`; `src/deep_research/request_budget.py` (`reserve`); `src/deep_research/observability/run_telemetry.py:199-234`; `src/deep_research/observability/tracker.py:673-706, 759-783, 810-870`; `src/deep_research/agents/evidence.py:173-201, 2907-2975, 3115-3192`; `src/deep_research/agents/acquisition.py:769-880, 1656-1683, 1908-1960, 2278-2310, 2371-2680`; `src/deep_research/agents/researcher.py:3312-3428, 4083-4131, 4289-4400`; `src/deep_research/agents/react.py:617-621`; `.venv` versions: openai 2.53.0, httpx 0.28.1, httpcore 1.0.9 (`httpcore/_async/http11.py:209-219`), langsmith 0.10.15; `output/live-proof/SUMMARY.tsv`; the six LangSmith traces listed in §1.1.

External:
- DeepSeek, Rate Limit & Isolation (keep-alive empty lines / `: keep-alive` comments; "If the request has not started inference after 10 minutes, the server will close the connection"; 2,500 concurrent requests for deepseek-flash; a request counts until the response is complete): https://api-docs.deepseek.com/quick_start/rate_limit/
- DeepSeek, Error Codes (429/503 semantics): https://api-docs.deepseek.com/quick_start/error_codes
- DeepSeek, Chat Completions API (`stream`, `stream_options.include_usage`, usage on the last chunk before `[DONE]`; `finish_reason` values incl. `insufficient_system_resource` and `aborted`; `max_tokens` default 64K/128K in thinking mode; `completion_tokens_details.reasoning_tokens`; the `json_object` whitespace-runaway warning): https://api-docs.deepseek.com/api/create-chat-completion/
- DeepSeek, thinking-mode streaming sample (`delta.reasoning_content` streams before `delta.content`): https://api-docs.deepseek.com/api_samples/thinking_mode_api_example_streaming
- HTTPX, Timeouts ("The read timeout specifies the maximum duration to wait for a chunk of data to be received"): https://www.python-httpx.org/advanced/timeouts/
- OpenAI Python API reference (read timeouts on `Stream`/`AsyncStream` raise `APITimeoutError`; "Stream consumption is not automatically retried"; quoted via search summary): https://developers.openai.com/api/reference/python
- deepseek-ai/DeepSeek-V3 #1608, SSE stream silently stalls mid-`reasoning_content` (prevalence "a few percent of streams on long reasoning turns"; idle-watchdog recovery; 180 s client floor; still reproducing 2026-09-14→17): https://github.com/deepseek-ai/DeepSeek-V3/issues/1608
- LibreChat-AI/LibreChat #16371, DeepSeek stream terminated at ~4 minutes (single report, unresolved): https://github.com/LibreChat-AI/LibreChat/issues/16371
- Dean & Barroso, The Tail at Scale (hedged requests after the 95th-percentile latency, ~5% extra load; cancelling duplicates): https://cacm.acm.org/research/the-tail-at-scale/
- TheRouter.ai, LLM API timeouts/retries/idempotency guide (one end-to-end deadline across attempts; idempotency keys): https://therouter.ai/blog/llm-api-timeouts-retries-idempotency-cross-provider-guide/
- AveMujica, An LLM API timeout strategy ("a timed-out request may still have executed" → duplicate billing): https://api.avemujica.moe/blog/llm-api-timeout-strategy
- 59API, rate limits and retries (retry storms): https://59api.com/blog/2026-guide-to-llm-api-rate-limits-and-retries-2493
- TianPan.co, LLM tail latency (retrying slow requests converts a latency problem into throughput collapse; hedging): https://tianpan.co/blog/2026-05-07-llm-tail-latency-p99-heavy-tailed-distributions
- bedrock-python/clientwright #25 (httpx read timeout resets on every chunk; a total deadline on httpx stops at the headers): https://github.com/bedrock-python/clientwright/issues/25
