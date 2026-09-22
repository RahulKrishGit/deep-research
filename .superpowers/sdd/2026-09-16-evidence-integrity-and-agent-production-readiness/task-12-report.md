# Task 12 report — Prove the real agents and CLI in an offline adversarial matrix

## Round 6 - 18/18; both rulings landed as TDD commits and the last red row is green

Both round-5 rulings were applied as asked. Ruling 4 - the acquisition-side
audit-sequence collision, the more serious of the two - is fixed in shipped code
with its own RED-then-GREEN test, and a run's sub-topics now keep their own
manifests all the way to the end of the run. Ruling 3 - the row rebuilt for
cross-session validated cache reuse - is delivered end to end: the harness seeds
a genuine prior-session read artifact, this run validates and reuses it through
the shipped `validate_cached_read` path, and the record the run files is
`acquisition_kind == "cache"` carrying the reading session and the moment this
session validated it. `validated-cache-reuse` holds for all three offline
repetitions and **all eighteen rows are green** for the first time in this task.

Dispatch settings: model `deepseek-v4-flash`, reasoning effort `max`. Worktree
`...\.worktrees\agent-cli-quality-trace-plan`, branch
`codex/agent-cli-quality-trace-plan`, started from `103d140` (round 5's head;
baseline `1 failed, 4242 passed, 1 deselected, 2 warnings`, the failure being
this row). I did not push, did not dispatch subagents, did not run `git stash`,
and did not invoke the paid individual-agent evaluation CLI. Every run below was
made with `PYTHONPATH=<worktree>\src` and the worktree as cwd, with the import
target printed first (`python -c "import deep_research; print(deep_research.__file__)"`
-> `...\agent-cli-quality-trace-plan\src\deep_research\__init__.py`).

### 1. Ruling 4 - the run's policies share one acquisition sequence (`404050d`)

Ruling 4 asked for the mechanism to be designed here rather than copied from
Ruling 1, for a real RED that reproduces the cross-sub-topic overwrite, and for
the fix to stay acquisition-side. All three are done.

**What landed.** `ManifestSequence` (`src/deep_research/agents/acquisition.py`)
is a one-field counter with a single `take()` - the sequence number to use, then
increment - so the operator is named once and cannot be re-derived at four call
sites. `AcquisitionPolicy` gains `audit_sequence: ManifestSequence | None` and
an `_own_sequence` fallback, and `_next_sequence()` returns
`(self.audit_sequence or self._own_sequence).take()`; all four write sites
(admission, passage selection, adjudication packet, and the refusal path) read
it. `ResearcherAgent.__init__` holds one `self._run_audit_sequence` for the run
and `_policy_for_task` passes that same object to every sub-topic's policy, so
the ids a run mints are unique across the run while every policy still writes
into the one shared `boundary_audits` mapping. The fallback keeps a
directly-constructed policy working as before. `ManifestSequence` is exported
from `deep_research.agents` (`tests/test_imports.py` requires it).

**RED.** `test_two_sub_topics_keep_their_own_acquisition_manifests`
(`tests/test_agents/test_acquisition.py`) builds two real policies over one
shared `_RecordingAudits` mapping and asserts the second sub-topic's manifests
do not displace the first's. The first run of it is `scratch/r6-audit-red1.txt`:

```
E   ImportError: cannot import name 'ManifestSequence' from 'deep_research.agents.acquisition'
```

- the test names the seam before it exists. The collision itself is captured in
`scratch/r6-audit-red2.txt` once the test can run:

```
E       assert ['audit-7bf6b...333f528ac9fb'] == []
E         Left contains one more item: 'audit-7bf6b8f10ce3333f528ac9fb'
FAILED tests/test_agents/test_acquisition.py::test_two_sub_topics_keep_their_own_acquisition_manifests
1 failed, 30 deselected in 0.94s
```

**GREEN.** `scratch/r6-audit-green2.txt` - `2 passed, 29 deselected in 0.51s`;
the whole module `scratch/r6-acq-final.txt` - `31 passed in 0.61s`; and the
agents, imports and pin suites together `scratch/r6-ruling4-green.txt` -
`1812 passed, 1 warning in 5.55s`.

**The pin.** The fix changes the researcher module source, so its
`agent_prompt_fingerprint` moves and the pin table in
`tests/test_evaluation/test_config.py` is re-pinned
`613603dc5cbd` -> `25fba5d22654` with an attributed comment, as every earlier
re-pin in this task has been.
`test_the_acquisition_sequence_repin_is_attributed_to_the_shared_counter`
asserts the table value first, so it failed before the edit for the right
reason (`scratch/r6-repin-red.txt`):

```
E       AssertionError: assert '613603dc5cbd' == '25fba5d22654'
FAILED tests/test_evaluation/test_config.py::test_the_acquisition_sequence_repin_is_attributed_to_the_shared_counter
1 failed, 71 deselected, 1 warning in 0.67s
```

and passes after it (`scratch/r6-config-green.txt`, `72 passed`).

**Measured before and after.** Before the fix, on this same row
(`scratch/r5-cache-probe5-final.txt`, quoted in round 5): twelve manifest writes
over seven ids, eight of them replacing an existing id, for example

```
OVERWRITE[read] target=topic-02 id=audit-fc131260417ec9a6d872c0ed
  was_op=read_admission fp=0eb33d05e4 -> op=read_admission fp=5af88bd12a
OVERWRITE[read] target=topic-03 id=audit-ee599db2612369fa0003d721
  was_op=passage_selection fp=0eb33d05e4 -> op=passage_selection fp=6e35ef489f
```

leaving seven audits and both surviving `read_admission` manifests belonging to
`topic-03` alone. After the fix, the same driver run
(`scratch/r6-vcr-auditfix.txt`) reports

```
audits: 13 Counter({'read_admission': 5, 'passage_selection': 5, 'adjudication_packet': 3})
```

- thirteen writes, thirteen ids, no overwrite, and the per-audit dump shows them
spanning topic-01, topic-02 and topic-03, including topic-01's admissions. The
run is `accepted`, exit 0, exactly as before - the loss was silent, which is why
nothing but the ids could have caught it.

**Scope.** The only production files touched are `acquisition.py`,
`researcher.py` and `agents/__init__.py`. No adjudicated verdict, evidence
identity or Fact-Checker-side audit behaviour changes: the Fact Checker's ids
already carried its agent and operation names plus Ruling 1's session-wide
sequence, and its manifests are counted unchanged in the "after" dump above
(`adjudication_packet: 3`, the same three that survived before). This is
acquisition-side only, as the ruling required.

### 2. Ruling 3 - the row rebuilt for a real prior-session import (`b1f0ba4`)

**What the fixture now is.** `ReplaySource` gained `cache_artifact`
(`""` / `"valid"` / `"stale"` / `"forged"`) and `cached_text`, with
`__post_init__` rules that keep a fixture honest: a `cached_text` needs an
artifact kind, a `valid` artifact may not state a body (it must agree with the
page), and `stale`/`forged` must state a body that differs from the live page -
`stale` still containing the excerpt the case's citation rests on, `forged` not.
The harness builds each artifact through the shipped
`build_read_record_from_tool_result` from a reader-shaped payload under
`PRIOR_SESSION_ID = "earlier-session"` and `PRIOR_READ_RETRIEVED_AT`, i.e. as if
an earlier session had read it, and `build_replay_runtime` starts the run with
them as its source cache - `read_cache=dict(stored_reads)` - while recording the
pristine declaration separately as `ReplayRuntime.seeded_reads`. The run then
reaches each of them through the ordinary cache-hit path: `before_action`
intercepts the URL, `validate_cached_read` decides, and the admitted ones are
filed with `acquisition_kind == "cache"`. Nothing in the fixture asserts the
kind; the shipped code produces it.

**The scoped production change the ruling anticipated.** Two, both minimal:

1. `build_agent` / `build_agents` / `build_runtime` gained an optional
   `read_cache` mapping, handed to the Researcher alone (it is the one agent
   that looks a URL up before downloading it; the other five never fetch a
   body). Absent, every run starts with an empty cache exactly as before.
   RED for the seam - `scratch/r6-assembly-red.txt`:

   ```
   E       TypeError: build_agents() got an unexpected keyword argument 'read_cache'
   FAILED tests/test_runtime/test_assembly.py::test_the_seeded_source_cache_reaches_the_researcher_alone
   1 failed, 45 deselected in 1.45s
   ```

   GREEN - `scratch/r6-assembly-green.txt`, `46 passed in 1.15s`, with the test
   asserting `agents.researcher._shared_cache is stored` and that the planner
   and fact checker hold no cache at all.

2. `AcquisitionPolicy._read_observed`'s import branch filed the pre-validation
   original network record rather than the import, so no fixture of any shape
   could put a `cache`-kind record into `state.read_records`: the reuse was
   performed and then recorded as if this session had downloaded the bytes. It
   now files the validated import - the record `validate_cached_read` returned,
   with the reading session and this session's validation stamp - and does so
   with `setdefault`, so a body the registry already holds (this run's own read,
   or an earlier sub-topic's import) keeps the record it was filed as instead of
   being re-stamped by each reuse. This is what makes `ReadRecord`'s own
   contract true in the output: `cache` "only for a record this session
   validated locally", `origin_session_id` "the session that read the bytes;
   never the session that imported them", and `report._read_counts.cache_reads`
   counting a reuse as a cache read.

**The checker.** `cache_provenance_is_validated` is registered in
`_REPLAY_INVARIANTS` and declared by the row. For each seeded artifact it
locates the run's record and the fetched URLs, and asserts what the artifact
claims: a `valid`/`stale` artifact must not have been fetched again, must have
been filed as `cache` by the prior session, and must carry a validation stamp
and the declared content hash; a `forged` artifact must not be what the run
recorded, and what the run did record must be this session's own network read.
It is asserted by the row, so `test_every_checker_is_asserted_by_a_case` holds.

**RED, then the finding it produced.** The first end-to-end run of the rebuilt
row (`scratch/r6-vcr-rebuild1.txt`):

```
case: validated-cache-reuse exit: 0 quality: accepted
failures: ["invariant 'cache_provenance_is_validated' broken: the forged stored read for https://bureau3.example.test/widget-funding-2024-panel is what the run recorded"]
```

The cause was real and worth keeping: the seeded mapping *is* the run's live
cache, so the run's own admission for that URL replaced the index entry in
place, and comparing the run's cache against the declaration was comparing the
run with itself. `build_replay_runtime` now hands the run a copy
(`dict(stored_reads)`) and keeps the declaration untouched, which is also the
honest model of "what an earlier session stored" being an input rather than a
live index.

**The checker is not vacuous.** Forcing it to return early
(`if not seeded or True: return None`) makes its dedicated test fail
(`scratch/r6-checker-mutation-red.txt`, `1 failed, 28 deselected`, the assertion
showing the missing message), and reverting the mutation restores
`1 passed, 28 deselected` (`scratch/r6-checker-green.txt`).

**The green row** (`scratch/r6-vcr-rebuild2.txt`):

```
case: validated-cache-reuse exit: 0 quality: accepted
failures: []
errors: Counter({'researcher_sub_topic_skipped': 2, 'fact_checker_invalid_claim': 1})
reads: {'read-3dbc3b2b1aaff222262cc96c': ('network', 'replay-validated-cache-reuse-r1', 'https://bureau3.example.test/widget-funding-2024-panel'), 'read-3e7af19414a7245b408b2e54': ('cache', 'earlier-session', 'https://agency11.example.test/adoption-2024'), 'read-4e60974ed7a75342f09309d5': ('cache', 'earlier-session', 'https://bureau11.example.test/adoption-panel-2024'), 'read-90866c270a1e4c960b84dc39': ('network', 'replay-validated-cache-reuse-r1', 'https://agency3.example.test/widget-funding-2024')}
audits: 13 Counter({'passage_selection': 5, 'read_admission': 5, 'adjudication_packet': 3})
fetched: Counter({'https://agency3.example.test/widget-funding-2024': 1, 'https://bureau3.example.test/widget-funding-2024-panel': 1})
```

Two records are `cache` imports from `earlier-session`, two are this session's
own network reads, and the fetch log holds exactly the two pages this session
had to download - the shared page and the stale panel were answered from the
cache without a fetch, and the forged page was refused and then really fetched.
Exit 0, accepted, and the three-repetition matrix test for the row passes.

### 3. What the rebuilt row covers adversarially, and the one gap left

The brief's clause for this row - stale, changed or forged cache provenance
cannot silently pass - is covered end to end for three of its four shapes:

| Shape | Behaviour asserted |
| --- | --- |
| Valid prior-session artifact | Reused with no fetch; filed `cache` by the reading session with a validation stamp and the declared hash |
| Stale artifact (older body, still stating the cited figure) | Imported without a fetch; the report answers for it with the label, and the checker asserts kind, reading session, stamp and hash |
| Forged artifact (metadata contradicting its own text) | `validate_cached_read` refuses it, the run re-fetches the page, and the record is this session's own network read |

The fourth shape - an entry **changed since its citation** - is a refusal
(`content_version_changed` / `content_hash_changed` in `cache_reuse_problem`)
that remains unit-tested (`tests/test_agents/test_acquisition.py:731-765`)
rather than exercised by a matrix row. A self-consistent stale artifact is
importable **by design**; the harness answers for that with labelling rather
than with a refusal, which the new checker asserts directly. This is recorded as
a gap rather than dropped silently.

### 4. Gates run this round (real output)

Import target printed before the runs:
`...\agent-cli-quality-trace-plan\src\deep_research\__init__.py`.

| Gate | Command | Result |
| --- | --- | --- |
| 1 | `python -m pytest tests/test_evaluation tests/test_e2e_evaluation tests/test_cli -q` | `1124 passed, 1 warning in 69.94s (0:01:09)`, exit 0 (`scratch/r6-gate1-final.txt`) |
| 2 | `python -m deep_research.e2e_evaluation suite --tier controlled --repetitions 3` | three cases each `accepted` (`broad-constraints` coverage 1.00 / judge 1.00, `comparative-conflict` 1.00 / 0.81, `refinement-evidence-recovery` 1.00 / 0.86), `Suite: accepted (3 repetitions per case)`, `Network: zero (scripted dependencies only)`, exit 0 (`scratch/r6-gate2-final.txt`) |
| 3 | `python -m pytest -q` | `4249 passed, 1 deselected, 2 warnings in 88.12s (0:01:28)`, exit 0 (`scratch/r6-gate-full.txt`) |
| 4 | `python -m ruff check src tests` | `All checks passed!`, exit 0 |
| 5 | `git diff --check` | exit 0; `git status --porcelain` filtered to tracked modifications prints nothing |

Gate 3 is the baseline `1 failed, 4242 passed` plus the six new tests of this
round and the row that was failing: 4242 + 6 + 1 = 4249, and no pass count
dropped below the 4242 floor. The controlled tier still runs three cases, not
the eighteen-row manifest - see open items.

### 5. Commits this round

| Commit | What it does |
| --- | --- |
| `404050d` `fix(researcher): keep acquisition audit ids unique across a run's sub-topics` | Ruling 4: `ManifestSequence`, the per-run sequence held by `ResearcherAgent` and passed to each sub-topic's `AcquisitionPolicy`, the researcher re-pin with attribution, and the collision RED-then-GREEN tests. 5 files, +199/-11. |
| `b1f0ba4` `test(evaluation): exercise real agents and CLI against adversarial evidence` | Ruling 3: the cache-artifact fixture and `prior_session_reads`, the `read_cache` seam through `build_runtime`, the import-filing fix in `_read_observed`, the `cache_provenance_is_validated` invariant, the rebuilt `validated-cache-reuse` row, and the focused tests for all of it. 7 files, +643/-41. |

### 6. Open items after 18/18

- The controlled-tier suite still runs three cases (`broad-constraints`,
  `comparative-conflict`, `refinement-evidence-recovery`) rather than the
  eighteen-row manifest. Wiring the tier to the offline matrix is not cheap and
  remains open, as rounds 3-5 recorded; the offline matrix itself runs all
  eighteen rows at three repetitions.
- The named mutation tests (monkeypatched `claim_evidence_pool` omitting a
  required support, `statement_source_urls` returning an invented URL,
  `query_memory`-as-read) remain not started - the last item of the brief's
  evidence list that no round has reached.
- The `validate_cached_read` / `cache_reuse_problem` focused test asked for
  after 18/18 is satisfied: `tests/test_agents/test_evidence.py` covers each
  refusal branch of `validate_cached_read` (incomplete-hash expectation, blank
  id, non-network kind, ineligible version, incomplete extraction, hash
  disagreement, missing and rewritten passages) and
  `tests/test_agents/test_acquisition.py` covers all four
  `cache_reuse_problem` verdicts; this round added the two end-to-end-shaped
  ones - a genuine prior-session import is admitted as this session's cache
  record, and a forged entry is refused and never filed.
- Round 4's remaining notes stand unchanged (session-scoped reference
  numbering; every harness run writes under a caller-supplied `root`, so
  offline fixture output cannot land in the live evaluation namespace).
---

## Round 5 — 17/18; both rulings landed as TDD commits; `validated-cache-reuse` stops on a new finding

Round 4's two questions were ruled and both rulings landed: the boundary-audit
id collision is fixed with a per-session sequence (`9b530d8`), the harness's
claim verdict is now scoped to what the packet selected (`7caa255`), and the
claim pool is linked to the sub-topics its obligations live in (`263945d`).
Each fix has its own RED-then-GREEN test and its own commit. The three rows
Ruling 2c named hold for all three offline repetitions, and no row that was
green regressed: **seventeen of eighteen** matrix rows are green.

`validated-cache-reuse` remains red, on the invariant `read_downloaded_once`
alone, and the two facts behind that are measured here for the first time -
neither is covered by the rulings. **4.1:** the invariant's second half asks
`state.read_records` for a read with `acquisition_kind == "cache"`, and the
shipped cache-hit path deliberately keeps the run's own network record
canonical, so the clause is satisfied only by an admission whose read id the
run has not seen - the persisted artifact this case does not build and the
harness has no way to seed. **4.2:** every acquisition policy starts its own
audit sequence over the run's one shared manifest mapping, so each later
sub-topic's Section 2.6 manifests silently replace the earlier topic's -
including the admission manifest that recorded the reuse - the same identity
class as Ruling 1, differing only in that nothing refuses the loss. Ruling 2's
stop rule is honoured; Ruling 1's iterate-the-fixture instruction is carried as
far as fixture work reaches, with the case's two self-caused refusals declared
(`be4608f`, following the sibling rows that declare the identical records), and
the failure list left holding exactly one line whose fix is not a fixture's.

Dispatch settings: model `deepseek-v4-flash`, reasoning effort `max`. Worktree
`...\.worktrees\agent-cli-quality-trace-plan`, branch
`codex/agent-cli-quality-trace-plan`, started from `f47bb19` (round 4's head:
2 failed / 4237 passed / 1 deselected; the two reds were `late-contradiction`
and `validated-cache-reuse`). I did not push, did not dispatch subagents, did
not run `git stash`, and did not invoke the paid individual-agent evaluation
CLI. Every run below was made with `PYTHONPATH=<worktree>\src` and the worktree
as cwd, with the import target printed first
(`python -c "import deep_research; print(deep_research.__file__)"`).

### 1. What landed this round

| commit | what it is |
|---|---|
| `9b530d8` `fix(fact-checker): keep boundary-audit ids unique across a whole session` | Ruling 1, applied as proposed and re-tested: a per-session `self._audit_sequence` read with `getattr(self, "_audit_sequence", 0)` so the `object.__new__` stubs still build, so a second pass cannot re-mint an id the first pass used. New test `test_a_second_pass_keeps_its_manifests_distinct_from_the_first`: two passes over one stub, ids asserted disjoint, both manifests merged through the real `merge_boundary_audits`. RED, with the collision captured - both passes minting `audit-0348b27da461b57f573e3416` (`scratch/r5-audit-red.txt`); GREEN - `4 passed, 183 deselected` (`scratch/r5-audit-green.txt`); `tests/test_agents` after the fix - `1908 passed` (`scratch/r5-agents-green.txt`). Pin re-pinned `7012a186eb59` -> `c4d37173726c` with an attributed comment (the pin hashes the agent module source). |
| `7caa255` `fix(evaluation): scope the harness's claim verdict to what the packet actually selected` | Ruling 2a: `claim_source` resolves the claim the packet states under its `# Claim` header, then only the pages whose evidence ids the packet actually carries (`packet_sources`), instead of scanning the whole scenario registry for the first declared page whose claim string appears anywhere. Two new tests (`test_claim_source_is_the_page_that_states_the_claim_under_check`, `test_claim_source_is_scoped_to_the_pages_the_packet_selected`) with a packet renderer that reproduces the product's own packet shape. RED, with the wrong page measured - `agency12.test/adoption-2024` where the packet selected `bureau12.test/adoption-2024` (`scratch/r5-harness-red.txt`); GREEN - `5 passed, 23 deselected` (`scratch/r5-harness-commit.txt`). |
| `263945d` `fix(fact-checker): link a claim's evidence pool to its obligations' coverage` | Ruling 2b: `_claim_target_scope` resolves a claim's obligations to themselves (the Fact Checker registers its own acquisitions against the obligation id, `fact_checker.py:2675`) plus the sub-topics those obligations live in (the Researcher registers reads against the coverage id, `researcher.py:1298`), and `by_target` intersects that scope - never another topic's. New test `test_the_pool_links_an_obligation_to_the_units_that_cover_it`. RED, with the rival unit missing - `Extra items in the right set: 'https://lab-c.test/rival'` (`scratch/r5-pool-red.txt`); GREEN - `188 passed` (`scratch/r5-pool-green2.txt`). Pin re-pinned `c4d37173726c` -> `70fa432dfc6d` (`scratch/r5-pin2.txt` is the failure that caught the move). The literal round-4 proposal (coverage ids only) is **not** what shipped: it regressed eleven green tests (`11 failed, 177 passed`, `scratch/r5-pool-green.txt`) because both id spaces are real in production, so the scope keeps both and nothing else. |
| `2ca4a14` `style(evaluation): wrap two over-long fixture lines from the harness scoping fix` | Gate item 4 caught two E501s `7caa255` introduced; wrapped, gate green. |
| `be4608f` `test(evaluation): exercise real agents and CLI against adversarial evidence` | `validated-cache-reuse`'s declaration admits the two refusals the case's own shape causes: the topics the first read discharged (and the filler topic) are skipped rather than re-researched, and the second topic's extraction re-drafts the claim the first pass adjudicated, which the product records as the fact having been checked once. Sibling rows declare both classes for the identical records (`unsupported-mechanism`'s comment is the same reasoning); the case still asserts quality `accepted`, exit 0, its two required targets, its minimum claim count and its invariant. |

### 2. The matrix, measured

```
PYTHONPATH=<worktree>\src python -m pytest tests/test_e2e_evaluation/test_real_agents.py -q
1 failed, 27 passed, 1 warning in 24.01s        (scratch/r5-matrix-final.txt)
```

**Green, three offline repetitions each (17):** `broad-constraints`,
`comparative-conflict`, `refinement-evidence-recovery`,
`blocked-html-pdf-fallback`, `same-work-mirror`, `semantic-duplicate-claims`,
`stalled-refinement`, `primary-attribution`, `current-versus-forecast`,
`unsupported-mechanism`, `judge-failure`, `non-constraint-answer`,
`empty-but-clean`, `memory-is-not-read`, `decision-context-late-candidate`,
`reopen-unanswered-target`, `late-contradiction`.

**Red (1):** `validated-cache-reuse` - `invariant 'read_downloaded_once'
broken: no later answer reused a read this run had already made`
(`scratch/r5-vcr-fixture.txt`); section 4.

The three rows Ruling 2c named, re-measured after both fixes:

```
$ python -m pytest tests/test_e2e_evaluation/test_real_agents.py -q \
    -k 'late-contradiction or decision-context-late-candidate or reopen-unanswered-target'
3 passed, 25 deselected, 1 warning in 3.19s     (scratch/r5-2c-rows-final.txt)
```

`late-contradiction` is green for the first time: the pool link of `263945d`
admits the contradicting account, and the run ends `partial` / exit 4 with the
required gap recorded, as the case declares.

### 3. Zero-network evidence

Unchanged in shape and re-verified: `network_denied()` replaces
`socket.socket`, `socket.socketpair`, `socket.create_connection` and
`socket.getaddrinfo`, records each attempt and restores them in a `finally`.
Every repetition asserts `attempts == []` before reading anything else, every
replay behind this report - green or red - ran through `run_replay_scenario`
inside that guard, and gate item 2 prints `Network: zero (scripted
dependencies only)`. The two new harness tests touch no network at all: they
exercise the completer's verdict resolution directly, against a packet built in
the product's own shape.

### 4. `validated-cache-reuse`, the remaining red

State at the end of the round (`scratch/r5-vcr-errors.txt`): exit 0, quality
`accepted`, both required targets answered, `quality.hard_failures` empty. The
halt round 4 recorded is gone (Ruling 1) and the pool link is in (Ruling 2b).
The run does record the two refusals section 1 describes. What is left is the
invariant, and the two measured facts behind it.

#### 4.1 the invariant's second half is not satisfiable by the shape this case builds

1. `_invariant_read_downloaded_once` (`replay.py:1868-1888`) requires
   (i) no URL fetched twice - which holds, measured: four bodies, one fetch
   each, `Counter({'https://agency11.example.test/adoption-2024': 1, ...})`
   (`scratch/r5-cache-probe-final.txt`) - and (ii) at least one
   `state.read_records` entry with `acquisition_kind == "cache"`.
2. The reuse itself happens, measured: `AFTER-OBS target=topic-02
   meta_kind=cache` and `VALIDATE read=read-aa46f438c1d kind=network
   expected=True -> OK` (`scratch/r5-cache-probe3-final.txt`) - topic-02's read
   of the page topic-01 read is admitted as a validated import.
3. But the admitted record keeps the original's read id
   (`validate_cached_read`, `evidence.py:1666-1743`, re-stamps the kind and
   keeps the identity), and `_read_observed` then stores the **original**
   record under that id: `self.reads.setdefault(validated.read_id, original)`
   (`acquisition.py:1207-1213`), whose comment states the rule - "a cache hit
   is a local admission, not a second read". The cache-stamped record never
   reaches `self.reads`, so `state.read_records` ends with four records, every
   one `acquisition_kind == "network"`.
4. The clause can therefore only hold for an admission whose read id the run
   has **not** seen - a stored original from another session. The harness has
   no way to build one: `grep -n cache src/deep_research/e2e_evaluation/replay.py`
   finds the invariant and nothing else, and the fixture declares the same page
   on two topics rather than a stored artifact.

**Question 3.** Which correction is mine here? **(a)** the product records the
admitted cache read in `state.read_records` - a change to a documented
deliberate rule, and one that would put two records under one body; **(b)** the
harness gains a way to seed the case with a stored original, so the reuse is an
import (the brief's row 46, "known valid original cache artifact") and the
invariant can hold as written; **(c)** the invariant reads the boundary's own
admission record rather than the read registry - which 4.2 blocks today, since
that record is currently overwritten in place; or **(d)** leave the row red.

#### 4.2 the acquisition manifests overwrite each other (new production defect)

**Every acquisition policy of a run writes into one shared manifest mapping
while counting its own sequence from zero, so all but the last sub-topic's
manifests are silently replaced before any reader sees them.**

The chain, all in shipped code:

1. `AcquisitionPolicy` holds one `boundary_audits` mapping (`acquisition.py:590`,
   passed in by the caller) and a per-instance `_sequence` starting at 0
   (`:607`), incremented after each write (`:823`, `:1265`, `:1287`, `:1495`).
2. `ResearcherAgent._policy_for_task` (`researcher.py:1297-1328`) builds a
   **new policy per sub-topic**, each with `boundary_audits=self._run_boundary_audits`
   - the run's one mapping.
3. `boundary_audit_id` names the job, the agent, the operation and the sequence,
   so policy #2's first audit of the same operation mints the id policy #1
   already used, with different contents.

**Measured** (`scratch/r5-cache-probe5.py`, output
`scratch/r5-cache-probe5-final.txt`): twelve manifest writes over seven ids,
eight of which replaced an existing id, for example

```
OVERWRITE[read] target=topic-02 id=audit-fc131260417ec9a6d872c0ed
  was_op=read_admission fp=0eb33d05e4 -> op=read_admission fp=5af88bd12a
OVERWRITE[read] target=topic-03 id=audit-ee599db2612369fa0003d721
  was_op=passage_selection fp=0eb33d05e4 -> op=passage_selection fp=6e35ef489f
```

The end state holds seven audits,
`Counter({'adjudication_packet': 3, 'read_admission': 2, 'passage_selection': 2})`,
and both surviving `read_admission` manifests are `targets: ['topic-03']`
(`scratch/r5-cache-probe-final.txt`). The manifests for topic-01's and
topic-02's reads - including the admission that recorded the reuse of 4.1 -
were replaced in place. **No error is raised and the run is `accepted` / exit
0**: the loss is silent, which is what separates this from Ruling 1's
collision, where the merge refused the update loudly. The three
`adjudication_packet` manifests survive because the Fact Checker's own ids
carry its agent and operation and now a session-wide sequence.

**Proposal, not applied:** give the run's policies one sequence, shared the way
`_run_boundary_audits` is shared (`researcher.py:1297-1328`) - the exact shape
of Ruling 1's fix, with no new id scheme - or take the sub-topic into the id's
namespace, which changes a format the evidence contract owns.

**Question 4.** Is the acquisition-side id collision mine to fix in Task 12,
and if so which shape?

### 5. The gate, measured

```
1. PYTHONPATH=<worktree>\src python -m pytest tests/test_evaluation tests/test_e2e_evaluation tests/test_cli -q
   1 failed, 1121 passed, 1 warning in 82.56s                    (scratch/r5-gate1c.txt)
2. PYTHONPATH=<worktree>\src python -m deep_research.e2e_evaluation suite --tier controlled --repetitions 3
   Suite: accepted (3 repetitions per case)
   Artifact: output\evaluations\e2e\suite.json
   Network: zero (scripted dependencies only)                    (scratch/r5-gate2c.txt)
3. PYTHONPATH=<worktree>\src python -m pytest -q
   1 failed, 4242 passed, 1 deselected, 2 warnings in 97.59s     (scratch/r5-gate3c.txt)
4. python -m ruff check src tests
   All checks passed!                                            (exit 0)
5. git diff --check
   (no output; exit 0)
```

The full-suite pass count is **4242**, five above the round's 4237 floor: four
added tests plus `late-contradiction`, which was one of the two failures at the
start of the round. The one failure in items 1 and 3 is the row of section 4.
Gate item 2 still runs the controlled tier's three cases rather than the
eighteen-row manifest (a carried open item, not a regression).

### 6. Self-review, concerns and open items

Self-review:

- Every test added or changed this round was RED first, with the failure
  captured in the file named beside it, and GREEN after; no assertion was
  relaxed to reach a green. The two pre-existing assertions that moved are the
  `fact_checker` prompt-drift pin (moved by the source it hashes, twice, each
  with an attributed comment) and the `validated-cache-reuse` declaration,
  which gained two tolerated classes that sibling rows declare for the
  identical records while every other assertion of the case stayed.
- The shipped pool fix deviates from round 4's proposal in exactly one place
  and says so: the literal coverage-id-only form regressed eleven green tests
  (`scratch/r5-pool-green.txt`), because the two id spaces are both live in
  production, so the scope keeps the obligations themselves and the sub-topics
  they live in - and nothing else. With it, `late-contradiction` reaches the
  result the case declares and the other seventeen rows are unmoved.
- The harness fix anchors on the packet's stated claim as well as its ids
  because id-scoping alone still decides the wrong page when a page is *in* the
  ids and quotes a foreign claim in its own excerpt - measured in round 4
  (`scratch/r4-lc5.txt`), and pinned by the first of the two new tests.
- Nothing in this section is from memory: every number is the tail of a file
  under `scratch/` printed beside it, and the line numbers were re-read from
  the tree after the first draft.

Concerns:

- 4.2 is wider than this case. Any run with more than one researched sub-topic
  loses the earlier sub-topics' acquisition manifests, silently, in the state
  every Section 2.6 consumer reads. The matrix only catches it because this
  case's invariant looks for a manifest that is gone.
- `validated-cache-reuse` is red on the invariant alone; with the fixture work
  of this round in, no further fixture-side change moves it. Questions 3 and 4
  are the ruling it needs.

Open items:

- The controlled-tier manifest wiring (gate item 2 runs 3 of 18 cases),
  carried since round 3; not started.
- The four named mutation tests and the
  `validate_cached_read`/`cache_reuse_problem` focused test: not started this
  round.
- Round 4's remaining notes stand unchanged (session-scoped reference
  numbering; every harness run writes under a caller-supplied `root`, so
  offline fixture output cannot land in the live evaluation namespace - this
  round's gate artifact is `output\evaluations\e2e\suite.json`).

## Round 4 — matrix closed to 16/18; two structural defects diagnosed, BLOCKED

Sixteen of the eighteen matrix rows now hold for three offline repetitions
each. The two that do not — `late-contradiction` and `validated-cache-reuse` —
are both failing on defects in **shipped** code, diagnosed below with file:line
evidence and a measured repro; the two specific questions are at the end of
sections 4.1 and 4.2. Round 3's report is preserved unedited below.

Dispatch settings: model `deepseek-v4-flash`, reasoning effort `max`. Worktree
`...\.worktrees\agent-cli-quality-trace-plan`, branch
`codex/agent-cli-quality-trace-plan`, started from the tree round 3 left
(`55a7851`, 11 failed / 4225 passed, all eleven in
`tests/test_e2e_evaluation/test_real_agents.py`). I did not push, did not
dispatch subagents, did not run `git stash`, and did not invoke the paid
individual-agent evaluation CLI.

---

### 1. What landed this round

| commit | what it is |
|---|---|
| `173d9b8` `fix(planner): give the comparison answer form a creditable dimension` | Round 3's blocker, closed by the owner's R1 ruling: `_ANSWER_FORM_REQUIREMENTS["comparison"]` now names the shared basis, so the form shares a token with `_DIMENSION_SIGNALS` and a comparison target can be answered by evidence that fills that atom field. One requirement string in `agents/planner.py`; the strictness of `target_is_answered`, `_DIMENSION_SIGNALS`, and the credit-side exclusion of `answer form:`/`evidence period:` prose are untouched. `comparative-conflict` reached green. Fixture and pin work in the same commit: `e2e_evaluation/replay_matrix.py`, `tests/test_agents/test_planner.py`, `tests/test_evaluation/test_config.py` |
| `c582e11` | `broad-constraints`: the required phrase `1.2 million supplier records` was a *reordering* of the page's own sentence, so no rendering of the case's own evidence could satisfy it; the row asserts the figure the page states. `replay_matrix.py` |
| `0c87d19` | `semantic-duplicate-claims`: three fixture defects measured against the packet the run actually asked for — the equivalence proposal named packet *positions* (1, 2) where the stale 2023 claim sits, the two 2024 wordings differed in the relation they state as well as in wording, and the wording that carries `verified_pair` is the one two bodies publish. The 2023 archive is a read of the first topic, not a planned sub-topic: the frozen contract's evidence period is stamped on every target, so a planned 2023 sub-topic carried an obligation its own page could never discharge. `replay_matrix.py` |
| `ac043b2` | `reopen-unanswered-target`: the second-round pages are only discoverable through the topic's follow-up query, and the first-round record page now names the subject and dimension its answer row is drafted from. `replay_matrix.py` |
| `0b2f927` | `decision-context-late-candidate`: the corroborating account moves ahead of the late candidate so the topic is answerable while the last source is still the one the decision packet must keep offering; both evidence-poor notes name the record they belong to. `replay_matrix.py` |
| `612fed9` | `empty-but-clean`: the synthesis packet's coverage cell is a *list* when one claim is stated by several topics - the row carrying the case's only claim was being refused as an empty packet. `replay.py` |
| `7898252` | `unsupported-mechanism`: the cause obligation is declared the way the planner writes one, and the case declares the two refusals it is about (the composer refusing the drafted recommendation, and the second pass re-offering claims already adjudicated). `replay_matrix.py` |
| `f695db0` | `blocked-html-pdf-fallback`, which was failing for five separate reasons: the harness read every candidate with the scraper instead of through the product's own required-reader table; `claim_source` let a 403 landing page decide a verdict for a fact two readable documents state; the mirror page was declared `original` (so the collapse rule had no way to prefer the running copy, and the published reference moved between hosts with the session's read order) and did not serve the official's bytes; and the `mirror_not_double_counted` checker asserted the wrong thing. Two new harness tests pin the reader half and the reference-list half. `replay.py`, `replay_matrix.py`, `test_real_agents.py` |
| `695b0f2` | `memory-is-not-read`: the scripted Researcher never asked long-term memory for anything - a sub-topic that declares no page of its own ended its loop with zero tool calls, so the remembered lead could never reach a decision packet. The fixture recalls once per target, only for such a topic and only when the scenario seeded memory; measured, both leads are carried by requests 005/024/028, no read record names either URL, no claim cites one. The two records such a topic produces are declared, as the sibling partial cases declare them. `replay.py`, `replay_matrix.py` |
| `81fac82` | `late-contradiction`'s two scripted boundaries made faithful: a read the fixture marks as a contradicting account reaches the adjudication packet as one, attributed to the id the packet gave it; and a row the synthesis packet badges `contradicted` is disclosed as an uncertainty note rather than published as a settled finding (new test `test_writer_discloses_a_claim_the_packet_badges_contradicted`). `replay.py`, `test_real_agents.py` |
| `d5914f9` | the `late-contradiction` declaration admits the skip the Researcher records for the two topics whose obligations the first pass met - the class five sibling cases already allow. `replay_matrix.py` |
| `0e53854` | E501 wrap in the memory-recall guard, so the lint gate is clean. `replay.py` |

Nine of the eleven round-start reds are closed. Every case keeps its own three
offline repetitions, each in its own storage root under its own session, with
the three outcomes compared for equality.

### 2. The matrix, measured

```
PYTHONPATH=<worktree>\src python -m pytest tests/test_e2e_evaluation/test_real_agents.py -q
2 failed, 24 passed, 1 warning in 9.64s
```

**Green, three offline repetitions each (16):** `broad-constraints`,
`comparative-conflict`, `refinement-evidence-recovery`,
`blocked-html-pdf-fallback`, `same-work-mirror`, `semantic-duplicate-claims`,
`stalled-refinement`, `primary-attribution`, `current-versus-forecast`,
`unsupported-mechanism`, `judge-failure`, `non-constraint-answer`,
`empty-but-clean`, `memory-is-not-read`, `decision-context-late-candidate`,
`reopen-unanswered-target`.

**Red (2),** with the case's own reported reason at repetition 1:

| case | reason at r1 | verdict |
|---|---|---|
| `late-contradiction` | `terminal quality 'accepted' != 'partial'`, `exit code 0 != 4`, `the expected gap 'hard:unanswered_critical_targets' was not recorded` | **shipped-code defect, section 4.1** |
| `validated-cache-reuse` | `terminal quality 'partial' != 'accepted'`, `exit code 3 != 0`, `error:graph_invalid_agent_state`, `error:researcher_sub_topic_skipped`, `hard:unaccounted_required_targets`, `read_downloaded_once`: "no later answer reused a read this run had already made" | **shipped-code defect, section 4.2** |

Both rows are down to the one cause each section 4 names, with the fixture
noise they started with gone. `late-contradiction` used to fail on
`contradiction_recorded: "no contradicting passage was recorded for any claim"`
and now records the passage, the verdict and the disclosure; what it still
fails on is that the run counts the contested obligation **answered**.
`validated-cache-reuse` ends at the same halt round 3 recorded, now with the
cause traced (section 4.2) and with the declaration-level skips and the
unaccounted-target gap visible in the same failure list.

### 3. Zero-network evidence

Unchanged in shape and re-verified this round: `network_denied()` replaces
`socket.socket`, `socket.socketpair`, `socket.create_connection` and
`socket.getaddrinfo`, records each attempt and restores them in a `finally`.
Every repetition asserts `attempts == []` before reading anything else
(`test_real_agents.py:572`), and the class-check test does the same (`:432`).
Every run behind this report, green or red, was made under that guard, and gate
item 2 prints `Network: zero (scripted dependencies only)`.

### 4. The two blockers

#### 4.1 `late-contradiction`: the claim pool's sub-topic link compares two id spaces that never meet

**A contradicting account the run has already read cannot reach the packet of a
claim that does not cite it, because the pool's `by_target` link compares the
claim's evidence-target ids against the units' coverage ids.**

The chain, all in shipped code:

1. `claim_evidence_pool` (`agents/fact_checker.py:986-1039`) builds a claim's
   packet from three links: by citation, by target, by passage.
2. `obligations` (line 1008) is the claim's `target_ids` - the planner mints
   these as `topic-01-target-01` (`agents/planner.py:1293`, `target_id_for`).
3. Units are registered by the acquisition layer against the sub-topic the read
   was taken for: `researcher.py:1298` sets
   `target_id = task.sub_topic.coverage_id` - the bare `topic-01`.
4. `by_target = bool(obligations and obligations.intersection(unit.target_ids))`
   (`fact_checker.py:1029-1031`) therefore intersects `{topic-01-target-01}`
   with `{topic-01}`.

**Measured over all eighteen cases** (`scratch/r4-pool-link.py`, output
`scratch/r4-pool-link-2.txt`): unit target-id shapes `{'coverage'}`, claim
target-id shapes `{'obligation'}`, and **0 of 331 (claim, unit) pairs whose ids
intersect** - so the link admits nothing, in any case, ever.
`claim_missing_read_ids` (`fact_checker.py:1097`) makes the same comparison
against `read.target_ids` and is dead for the same reason.

**Run level.** In `late-contradiction` a third publisher states 30 percent
where two state 40, and all three are reads of the same sub-topic. The
40-percent claim cites only the two agreeing pages, so its pool never sees the
rival page: measured, the run ends **accepted / exit 0** with the figure
settled as a finding, the rival account recorded as a claim of its own, and the
critical target counted answered (`scratch/r4-lc3.py`, `scratch/r4-lc3.txt`).
The case's decisive assertion - a material conflicting passage past prefix
limits stays visible and blocks false settlement - is exactly what does not
happen.

**Proposal, measured** (`scratch/r4-proposal-pool-link.patch`, 46 lines, **not
committed**): a `_claim_coverage_ids(state, target_ids)` helper resolves a
claim's obligations to the sub-topics they live in, and `by_target` intersects
those coverage ids instead. With it applied, `late-contradiction` passes all
three repetitions: quality `partial`, exit 4, the required gap recorded, both
accounts' rows badged `contradicted`, and the published report carrying the two
filler findings while putting both adoption figures under "Uncertainty and
conflicting evidence / Contradicted by independent sources"
(`scratch/r4-lc6.txt`). That is the case's declared result.

**The proposal is not viable as written**, and this is the part I could not
decide alone: applying it regresses two rows that are green today -
`decision-context-late-candidate` and `reopen-unanswered-target` both flip to
`partial / 4` with `topic-01-target-01` unanswered
(`scratch/r4-proposal-matrix2.txt`). Mechanism, measured: with the widened pool
the sub-topic's evidence-poor note pages enter the claim's adjudication packet,
and the scripted adjudicator takes the verdict from the first declared page
whose declared claim string appears anywhere in the packet
(`ReplayCompleter.claim_source`, `replay.py:643-660`) - measured,
`claim_source -> https://agency12.example.test/method-note | declared verdict
insufficient_evidence` (`scratch/r4-lc5.py`, `scratch/r4-lc5.txt`). So the row
is judged on a page that states no figure.

**Question 1.** Which correction is mine to make here?

- **(a)** repair the pool link in production (the shape above, or a narrower one
  - for instance only units whose passage states the same obligation),
  accepting that the two green fixtures then need a harness that names the page
  the packet is *checking*;
- **(b)** leave production alone and first make the harness read the claim
  under check precisely (packet side), then re-measure the proposal;
- **(c)** leave both as they stand and keep the row red, on the reading that the
  case is correctly catching the defect and the pool's id space is a contract
  Task 5/11 owns.

#### 4.2 `validated-cache-reuse`: boundary-audit ids collide across the passes of one session

**The second fact-checking pass of a session mints an audit id the first pass
already used, and the state merge refuses the whole update - a non-recoverable
halt, not a quality gap.**

The chain:

1. `_record_packet_audit` (`agents/fact_checker.py:2700-2775`) mints each
   manifest's id from `job_id=self._session_id`, `agent_name`, `operation` and
   `sequence=len(self._adjudication_audits) + 1` (line 2748).
2. `_adjudication_audits` is **reset on every pass** (`fact_checker.py:3388`),
   so pass 2's first packet gets sequence 1 again - the id pass 1 used.
3. `merge_boundary_audits` (`agents/evidence.py:1927-1942`) refuses one id with
   two different contents (`raise EvidenceIdentityConflict`), and the reducer
   runs inside `merge_research_state`, so the entire fact-checker update is
   rejected: `graph_invalid_agent_state`, non-recoverable, exit 3.

**Measured repro, current tree** (`scratch/r4-vcr3.py`, output
`scratch/r4-vcr3.txt`) - the two manifests, caught where the product refuses
them; they differ in exactly one field:

```
COLLISION audit-122dc229f4ffe3c51eb105f8
  previous: target_ids=['topic-01-target-01'] ...
  current : target_ids=['topic-02-target-01', 'topic-01-target-01'] ...
  (job_id='replay-validated-cache-reuse-r1', agent_name='fact_checker',
   operation='adjudication_packet', packet_fingerprint identical)
```

Run level without the fix: `exit 3`, quality `partial`,
`error_type=graph_invalid_agent_state`, `exception_type=EvidenceIdentityConflict`
(`scratch/r4-vcr2.txt`).

**Proposal, measured** (`scratch/r4-proposal-audit-sequence.patch`, 32 lines,
**not committed**): a per-session `self._audit_sequence` that rises across
passes, read with `getattr(self, "_audit_sequence", 0)` so the tests' own
`object.__new__(FactCheckerAgent)` stubs still build. With it applied: the halt
is gone and the run reaches **exit 0 / accepted** with all three required
targets answered; `tests/test_agents` is 1716 passed; in the matrix the halt is
gone from `validated-cache-reuse`'s failure list, which now reads the skip
class, the duplicate-claim refusal and the cache invariant
(`scratch/r4-audit-proposal-matrix.txt`).

**What sits behind the halt, measured** (`scratch/r4-vcr1.py`,
`scratch/r4-vcr1.txt`): the run answers both topics' obligations from the
**first** topic's single read (one claim whose `target_ids` are
`['topic-02-target-01', 'topic-01-target-01']`), so no second read is ever
attempted and no read record carries `acquisition_kind == "cache"` - the
invariant `read_downloaded_once` (`replay.py:1820-1839`) is unsatisfied even
though no body was fetched twice. The same probe records
`error:fact_checker_invalid_claim {'rejected': ['claim 2: already checked']}`:
the scripted second extraction hands the fact checker the identical claim,
which the product correctly refuses to check twice. Both are fixture work - the
case's premise is the brief's "known valid original cache artifact", and the
fixture does not yet build the shape in which a later read must come from the
registry - but neither can be validated green while the halt stands.

**Question 2.** Is the boundary-audit id collision mine to fix in Task 12, and
if so which shape: **(a)** a per-session monotone sequence, as proposed; **(b)**
keep the per-pass sequence and namespace the id by pass (or take the pass out
of the session-scoped `job_id`); or **(c)** something the evidence-contract
owner should rule on, with the row left red until then.

### 5. Self-review, concerns and open items

Self-review:

- Every test added this round was RED first, with the failure captured:
  `test_writer_discloses_a_claim_the_packet_badges_contradicted` failed on
  `Left contains one more item: 'the rate was 30 percent in 2024'`
  (`scratch/r4-writer-red.txt`) before the badge was read, and passed after
  (`scratch/r4-writer-green.txt`). No assertion was relaxed to reach green:
  the two new writer assertions are stricter than the behaviour they replaced,
  and no existing case's declaration lost a required target, gap or invariant.
- The one assertion I changed is a *fixture* declaration gaining a tolerated
  failure class (`d5914f9`), not a case losing one; the row stayed red after
  the change for exactly the reason section 4.1 gives
  (`scratch/r4-lc-nodecl-red.txt`).
- Nothing I claim is from memory: every number in sections 2 and 6 is the tail
  of a file under `scratch/` produced by the command printed beside it, and
  the two line-number corrections in this section were re-checked against the
  tree after the first draft.
- `git status` at the end of the round shows no file under
  `src/deep_research/agents/` modified: the two proposals were applied,
  measured and reverted, and re-applying either `scratch/*.patch` reproduces
  the measurements above.

Open items:

- Both remaining reds are rows that were flagged as possibly structural at the
  start of the round, and both turned out to be.
- The pool proposal in its measured form is **not** viable as written (two
  green rows regress); the audit proposal passes the unit suite and unblocks
  the case to its next failure. Both are one case's worth of evidence, not a
  vetted fix.
- Gate item 2 (`suite --tier controlled --repetitions 3`) still runs three
  cases (`broad-constraints`, `comparative-conflict`,
  `refinement-evidence-recovery`) rather than the eighteen-row manifest. Wiring
  it is not cheap and remains open, as round 3 recorded.
- Still not started: the four named mutation tests, and the
  `validate_cached_read`/`cache_reuse_problem` focused test.
- The published report's reference numbering is **session-scoped** (read
  identity is salted by session, so the same fixture cites the same sources
  numbered in the order its own reads landed). The matrix canonicalizes the
  report by URL before comparing repetitions and says so in the test.
- The harness's `claim_source` resolves a packet's verdict by "the first
  declared page whose claim string appears anywhere in this packet". It is
  correct for every green case today, but section 4.1 shows it decides the
  wrong page as soon as a packet carries other pages that mention the claim's
  subject. I have not changed it, because the change is only measurable against
  the pool question.
- Every harness run writes under a caller-supplied `root`, so offline fixture
  output cannot land in the live evaluation namespace (D-14); the gate's own
  artifact went to `output\evaluations\e2e\suite.json`.

### 6. Baseline and the gate, at the final tree `0e53854`

Baseline at the round-start tree (`55a7851`): **11 failed, 4225 passed**, every
failure in `tests/test_e2e_evaluation/test_real_agents.py`.

```
1. python -m pytest tests/test_evaluation tests/test_e2e_evaluation tests/test_cli -q
   2 failed, 1118 passed, 1 warning in 45.54s
   (both failures are the two cases in section 4)

2. python -m deep_research.e2e_evaluation suite --tier controlled --repetitions 3
   broad-constraints: accepted; coverage 1.00; judge 1.00
   comparative-conflict: accepted; coverage 1.00; judge 0.81
   refinement-evidence-recovery: accepted; coverage 1.00; judge 0.86
   Suite: accepted (3 repetitions per case)
   Artifact: output\evaluations\e2e\suite.json
   Network: zero (scripted dependencies only)

3. python -m pytest -q
   2 failed, 4237 passed, 1 deselected, 2 warnings in 55.57s
   (the same two failures; every other test green)

4. python -m ruff check src tests
   All checks passed!

5. git diff --check
   (no output)
```

No pre-existing test regressed: the pass count rose from 4225 to 4237 and the
failure count fell from 11 to 2. Tree state at the end of the round: `0e53854`
on `codex/agent-cli-quality-trace-plan`, nothing pushed, `git status` clean
apart from this report file and the pre-existing untracked `scratch/`.

## Round 3 — **BLOCKED** on the comparison answer form

One structural production defect, diagnosed with file:line evidence and a
measured repro; the specific question is at the end of section 4. Round 2's
report is preserved unedited below.

Dispatch settings: model `deepseek-v4-flash`, reasoning effort `max`. Worktree
`...\.worktrees\agent-cli-quality-trace-plan`, branch
`codex/agent-cli-quality-trace-plan`, started from the tree round 2 left
(`a122ca5`). I did not push, did not dispatch subagents, did not run
`git stash`, and did not invoke the paid individual-agent evaluation CLI.

---

### 1. What landed this round

| commit | what it is |
|---|---|
| `f5f0ca7` `fix(claims): let a faithful primary attribution answer its own target` | Round 2's blocker was the admission gate collapsing all three support policies into `independent_pair`. It is now scoped to what the C4 ruling asked for: `independent_pair` untouched, the weaker policies additionally take `insufficient_evidence` + `source_supported` with at least one supporting publisher, through a new `evidence_status` argument that defaults to the conservative `None`. `agents/claim_clusters.py`, `agents/fact_checker.py`, `tests/test_agents/test_fact_checker.py` |
| `a5e37b1` `fix(claims): re-pin the fact checker fingerprint` | The call-site change moves the pin `246b797b9534` → `7012a186eb59`; the other five targets and the judge did not move. Attributed in the pin comment. `tests/test_evaluation/test_config.py` |
| `627fcbe` `test(claims): pin the zero-publisher invariant the admission floor relies on` | The `no_complete_support` branch is what stops a claim with no complete support from crediting a target; its four facts are now pinned. RED captured against two temporary local mutations, reverted byte-identical. `tests/test_agents/test_fact_checker.py` |
| `0206637` `test(evaluation): read every URL a synthesis packet row carries` | The packet reader expected one URL per row, so every two-source row was dropped and the claim the case exists to corroborate never reached the composition. The composer is now scripted per row. `e2e_evaluation/replay.py` |
| `fc6d2eb` `test(evaluation): pin what each replay case asserts, not just its exit code` | All eighteen builders and declarations; **20 registered checkers** (`_REPLAY_INVARIANTS`, `replay.py:1934`); the new `tests/test_e2e_evaluation/test_real_agents.py` (4 harness tests + the parametrized case test, 3 offline repetitions each). `replay.py`, `replay_matrix.py`, `test_real_agents.py` |
| `fcbedfe` `test(evaluation): keep the replay harness inside the lint gate` | 50 mechanical findings (47 × E501, 2 × I001, 1 × F841). Nothing behavioural: failure lines diffed identical before and after. |
| `7cdb2eb` `test(evaluation): assert the registry of checkers is what the cases declare` | A checker no case declares is an assertion nothing makes, and the registry had one. New harness test (RED: `registered but no case declares it: ['primary_attribution_not_verified']`), the primary-attribution row now declares it — measured non-vacuous by flipping one claim's badge to `verified_pair` — and the `empty-but-clean` fixture that could not be built at all now builds. |

Inventory: `REPLAY_CASE_MANIFEST_VERSION = 1`, `REPLAY_CASE_VERSION = 1`
(`replay_matrix.py:36,41`), 18 builders and 18 manifest rows, `REPLAY_CASE_IDS`
derived from the manifest (`replay_matrix.py:1647`). The test file names the
plan's 18 ids as the *expected* set, so a manifest that silently loses a row
fails rather than shrinking the proof.

### 2. The matrix, measured

```
PYTHONPATH=<worktree>\src python -m pytest tests/test_e2e_evaluation/test_real_agents.py -q
11 failed, 12 passed, 1 warning in 9.93s
```

**Green, three offline repetitions each** (7): `refinement-evidence-recovery`,
`same-work-mirror`, `stalled-refinement`, `primary-attribution`,
`current-versus-forecast`, `judge-failure`, `non-constraint-answer`. Each
repetition runs in its own storage root under its own session, and the three
outcomes must be equal (exit code, quality, answered obligations, the
canonicalized report, claim ids) with three distinct session ids.

**Red** (11), with the case's own reported reason at repetition 1:

| case | reason at r1 |
|---|---|
| `broad-constraints` | `the report does not state '1.2 million supplier records'` |
| `comparative-conflict` | `terminal quality 'partial' != 'accepted'`, `exit 4 != 0`, all three targets unanswered, `hard:unanswered_critical_targets` — **the blocker, section 4** |
| `blocked-html-pdf-fallback` | `partial != accepted`, 2 claims < 3, `error:agent_tool_failed`, `error:agent_tool_policy_rejected`, `topic-01-target-01` unanswered, `mirror_not_double_counted`: "no body was read from two hosts" |
| `semantic-duplicate-claims` | `partial != accepted`, `error:fact_checker_invalid_claim`, `error:synthesizer_invalid_draft`, `paraphrases_merged`: "no cluster absorbed a paraphrase" |
| `unsupported-mechanism` | `error:fact_checker_invalid_claim`, `error:synthesizer_invalid_draft`, `mechanism_obligation_stays_unanswered`: "nothing was ever checked for topic-02-target-01" |
| `late-contradiction` | `accepted != partial`, `exit 0 != 4`, the expected gap was not recorded, `contradiction_recorded`: "no contradicting passage was recorded for any claim" |
| `empty-but-clean` | `ReplayContractError: the synthesis packet carried no checked claims` (harness refuses to compose an empty packet) |
| `memory-is-not-read` | `memory_leads_are_not_reads`: "the remembered lead … never reached a decision packet", plus two tolerated-class gaps |
| `validated-cache-reuse` | `partial != accepted`, `exit 3`, `error:graph_invalid_agent_state`, `read_downloaded_once`: "no later answer reused a read this run had already made" |
| `decision-context-late-candidate` | `partial != accepted`, 2 claims < 3, `error:fact_checker_invalid_claim`, `error:synthesizer_invalid_draft` |
| `reopen-unanswered-target` | `partial != accepted`, 2 claims < 3, `error:fact_checker_invalid_claim`, `error:synthesizer_invalid_draft`, `required_target_reopened`: "the second-round page was never read" |

### 3. Zero-network evidence

`network_denied()` replaces `socket.socket`, `socket.socketpair`,
`socket.create_connection` and `socket.getaddrinfo`, records each attempt and
restores them in a `finally`. Every repetition asserts `attempts == []` before
it reads anything else about the run (`test_real_agents.py:335`), and the
harness test does the same for the class check. Every run in this report,
green or red, printed `attempts []`; no case produced a socket attempt. No
secrets were loaded and no prompts or thoughts were logged.

### 4. The blocker: a comparison question cannot be answered at all

**A plan whose `answer_kind` is `comparison` stamps a binding dimension onto
every one of its targets that no evidence can ever satisfy, so the product
marks every obligation of every comparison question unanswered — while
holding the verified evidence that answers them.**

The chain, all in shipped code:

1. `agents/planner.py:121-132` lists `_COMPARISON_MARKERS` ("compare",
   "compared with", "compared to", "versus", "vs", "difference between",
   "trade-off", "tradeoff", "which is better", "relative to"), and
   `planner.py:861,914` classify any question containing one as
   `answer_kind = "comparison"`.
2. `planner.py:93-118` maps each kind to its requirement text;
   `comparison` is `planner.py:99-102`: *"answer form: the same measured
   dimension for every option compared, on one shared basis and unit"*.
3. `planner.py:1497-1537` `apply_answer_contract` stamps that text onto every
   target's `required_dimensions` (`form = answer_form_requirement(...)` at
   line 1512, `required_dimensions=unique_phrases([..., form, period,
   geography])` at 1523-1524), with `required=True`.
4. A target is answered only when one substantive statement's
   `answered_dimensions` covers **every** required dimension
   (`utils/types.py:2735-2771`).
5. `answered_dimensions` has exactly one writer,
   `answered_required_dimensions` inside `derive_statement`
   (`utils/types.py:2054`, "the one statement derivation both the draft and
   legacy paths use"). It answers a dimension only when one of its word tokens
   appears in a `_DIMENSION_SIGNALS` group (`utils/types.py:1818-1854`) **and**
   a recorded proposition fills that group's atom fields.
6. The comparison form's tokens are `{answer, form, the, same, measured,
   dimension, for, every, option, compared, on, one, shared, basis, and,
   unit}`; the signal vocabulary is `{period, time, date, year, when, horizon,
   recency, geography, region, place, location, country, jurisdiction, scale,
   magnitude, quantity, size, capacity, amount, value, rate, level,
   population, subject, entity, who, mechanism, how, cause, driver, method,
   instrument, attribution, source, issuer, publisher, share, proportion,
   percent, denominator, comparison}`. **The intersection is empty.**
7. The credit side cannot fill it either: `agents/claim_clusters.py:1749`
   excludes `answer form:` (and `evidence period:`) from prose dimensions by
   design, so nothing on that path can answer it.

**Measured** (`scratch/r5-comparison-form-repro.py`, output in
`scratch/r5-comparison-form-repro.txt`) — for each form, one proposition with
**every** atom field filled (value, unit, observation period, geography,
population, denominator, attribution, predicate, quantity noun, forecast
status):

```
answer_kind -> the reporting signal group its requirement words match
  constraints  match=True [(('observation_period', 'forecast_status'), ['date']), (('predicate', 'quantity_noun'), ['instrument'])]
  comparison   match=False []
  explanation  match=True [(('predicate', 'quantity_noun'), ['mechanism'])]
  factual      match=True [(('observation_period', 'forecast_status'), ['date']), (('value', 'unit'), ['value'])]
  historical   match=True [(('observation_period', 'forecast_status'), ['period'])]

answered dimensions from that evidence, per answer form
  constraints  answered=['answer form: a list of the binding constraints, …']
  comparison   answered=[]
  explanation  answered=['answer form: a causal mechanism …, …']
  factual      answered=['answer form: the specific fact asked for, …']
  historical   answered=["answer form: the state of affairs in the period …"]
```

**Run level** (`scratch/r6-why-comparative.txt`, produced by
`scratch/r4_why.py comparative-conflict` against the real six agents and the
real CLI): the case answers all three obligations with `verified_pair` claims
(`independent_pair` policy satisfied, `dims_ok=False` everywhere, no errors
recorded) and still reports `answered= False` for every target, `exit 0
incomplete`, quality `partial`, `hard:unanswered_critical_targets`. The **only**
missing dimension in every statement of every target is the comparison answer
form:

```
TARGET topic-01-target-01 independent_pair answered= False
   required dims: ['answer form: the same measured dimension for every option compared, on one shared basis and unit', …]
   STMT A001 settled substantive= True dims_ok= False policy= True
        missing dims: ['answer form: the same measured dimension for every option compared, on one shared basis and unit']
        claims: [('8cb4e38431', 'verified', 'verified_pair')]
```

**The repository already documents half of this.** `tests/test_agents/test_planner.py:1718-1731`
("A bare inequality is a threshold, not a comparison") says the form "is
stamped into every target's binding dimensions, and Section 2.3 judges a
target answered only when those dimensions are satisfied. Stamping it onto
'how many projects waited more than 5 years?' makes an ordinary quantity
question unsatisfiable" — which is why the classifier narrows the form to
questions that name a referent. The tests pin *which* questions receive the
form; **nothing pins that a question which deliberately receives it can then be
answered**, and today none can. No test mentions
`answered_required_dimensions` (`grep -rn answered_required_dimensions tests/`
→ 0 hits), so the suite cannot see it.

Two rewordings that would make it answerable, measured
(`scratch/r6-comparison-fix-candidate.py`): appending the noun *comparison*
(`… for every option in the comparison, …`) or naming the shared
*denominator* both make the requirement match the
`("share", "proportion", "percent", "denominator", "comparison")` →
`("denominator",)` group, and the same all-fields proposition then answers it.
Both are shipped-text changes in `planner._ANSWER_FORM_REQUIREMENTS`, so I am
not making either on my own authority.

**The question.** Comparison is the one answer kind whose obligation no
evidence can discharge. Which correction is mine to make in Task 12?

- **(a)** reword `_ANSWER_FORM_REQUIREMENTS["comparison"]` so it shares a token
  with `_DIMENSION_SIGNALS` (e.g. name the comparison or the shared
  denominator), keeping `target_is_answered` strict — then a comparison target
  is answerable when the evidence fills that atom field;
- **(b)** add a signal group for the comparison vocabulary
  (`comparison`, `compared`, `basis`, `option`) over an existing atom field;
- **(c)** treat `answer form:` as a reporting-side obligation for comparison
  too, as it already is for the credit side, and stop requiring it of
  `answered_dimensions`;
- **(d)** something else, or leave the classifier's behaviour and the matrix
  row as they stand (the row would then have to declare `partial` and stop
  asserting that a comparison question is answerable at all).

Options (a) and (c) change shipped behaviour that Task 5/11 rulings may already
own; option (d) contradicts the matrix row's declared `accepted / 0`. I have
left all four untouched.

### 5. Concerns and open items

- The published report's reference numbering is **session-scoped**: read
  identity is salted by session (`agents/evidence.py::build_read_id`), evidence
  ids derive from read ids, and the rendered markers follow the order the
  session's reads landed in. The same fixture run twice cites the same sources
  numbered differently. The test canonicalizes the report by URL before
  comparing repetitions and says so; this is a product property I am recording,
  not a defect I am fixing.
- `empty-but-clean` now fails on the harness's own refusal
  (`ReplayContractError: the synthesis packet carried no checked claims`,
  `replay.py:689`) rather than on a fixture that could not be built. Whether
  that case should script an empty draft, or whether the product should reach
  the synthesizer at all with no checked claims, is a case-design question I
  have not decided.
- Three further reds look like product behaviour questions rather than fixture
  work and each needs the same treatment the comparison form got:
  `late-contradiction` (accepted a report while recording no contradicting
  passage), `memory-is-not-read` (the lead never reached a decision packet) and
  `validated-cache-reuse` (`graph_invalid_agent_state`, exit 3, no reuse).
- Not started this round: the four named mutation tests, the
  `validate_cached_read`/`cache_reuse_problem` focused test, and wiring
  `suite --tier controlled --repetitions 3` to the manifest inventory.
- Gate status at `7cdb2eb`. Run: item 1 (`pytest tests/test_evaluation
  tests/test_e2e_evaluation tests/test_cli -q`), item 3 (`pytest -q`), item 4
  (`ruff check src tests`) and item 5 (`git diff --check`). **Not run: item 2**,
  `python -m deep_research.e2e_evaluation suite --tier controlled
  --repetitions 3` — it is not yet wired to the manifest inventory and the
  round stopped at the blocker.
- Every harness run writes under a caller-supplied `root`
  (`run_replay_scenario(..., *, root: Path)`, no default), so offline fixture
  output cannot land in the live evaluation namespace (D-14): the tests pass a
  `tempfile.TemporaryDirectory` per repetition, and the diagnosis scripts used
  for this report wrote under `scratch/`.
- Tree state at the end of the round: `7cdb2eb` on
  `codex/agent-cli-quality-trace-plan`, nothing pushed, `git status` clean
  apart from this report file and the pre-existing untracked `scratch/`.

### 6. Baseline and the gate, at the final tree `7cdb2eb`

Baseline at the round-2 tree (`a122ca5`): **4213 passed, 1 deselected, 2
warnings**.

```
PYTHONPATH=<worktree>\src python -m pytest -q
11 failed, 4225 passed, 1 deselected, 2 warnings in 72.91s (0:01:12)

PYTHONPATH=<worktree>\src python -m pytest tests/test_evaluation tests/test_e2e_evaluation tests/test_cli -q
11 failed, 1106 passed, 1 warning in 52.78s

PYTHONPATH=<worktree>\src python -m ruff check src tests
All checks passed!

git diff --check
(clean; one CRLF warning for this report file, which is checked out with CRLF)
```

All 11 failures are the 11 red matrix cases in the new
`tests/test_e2e_evaluation/test_real_agents.py` — `grep -c '^FAILED'` gives 11
and `grep '^FAILED' | grep -vc test_real_agents` gives 0 — so no pre-existing
test regressed. 4225 − 4213 = 12 new passing tests, + 11 failing = 23 new tests.
Gate item 2 (`python -m deep_research.e2e_evaluation suite --tier controlled
--repetitions 3`) was **not run**: it is not yet wired to the manifest inventory
and the round stopped at the blocker.

---

## Round 2 (preserved unedited)

**Status: BLOCKED** (needs one controller ruling; this is a NEW finding, not a
recurrence of the orientation defect that blocked the previous round)

Dispatch settings: model `deepseek-v4-flash`, reasoning effort `max`.
Worktree `...\.worktrees\agent-cli-quality-trace-plan`, branch
`codex/agent-cli-quality-trace-plan`. Started at HEAD `184d8ad` (clean tree);
landed `a122ca5` this round and stopped. I did not push, did not dispatch
subagents, did not run `git stash`, and did not invoke the paid individual-agent
evaluation CLI.

Baseline at `184d8ad`: 4178 passed, 1 deselected. Final tree re-measured after
the round: **4178 passed, 1 deselected, 2 warnings** — at the floor, nothing
moved.

---

## 1. Headline

The offline replay harness now drives the **real** stack end to end and the
first matrix row is fully authored. On today's tree that row settles at
`partial / 4`, not the declared `accepted / 0`, and the reason is a **claim
admission gate, not a harness defect**:

> A target can only be answered by a claim that was adjudicated `verified`, and
> on the packet path `verified` is written only for a confirmed independent
> pair. `primary_attribution` and `derivation` targets therefore still require a
> second organization's independent corroboration, which plan lines 124-125,
> 128, 146, 404 and amendment item 6 (line 1109), plus the matrix row itself
> (line 945), all forbid.

The gate lives in files outside this task's list (`agents/claim_clusters.py`,
`agents/fact_checker.py`), and Task 5's reviewed ruling C4 pinned it
deliberately, so I stopped rather than re-deciding a semantic product rule.

**Measured, not inferred:** with a 3-line relaxation of that gate the same
scenario reaches `exit 0 / accepted` with all four targets answered and the
honest `primary-source attribution; independent corroboration not established`
badge intact, and the full suite moves exactly one test (a governed prompt
re-pin). The patch is saved, unapplied, at
`scratch/gate-relax-proposal.patch`; it applies cleanly at `a122ca5`.

**The question for the controller is in section 5.**

---

## 2. Why this is BLOCKED — the measured chain

`scratch/probe_gate.py` traces the production functions on a real run
(`PYTHONPATH=<worktree>\src`, worktree venv python 3.12.14). Real output,
abridged only in the claim texts:

```
== planner-stamped targets ==
topic-01-target-01 policy= primary_attribution required= True critical= True dims= ['measured value',
  'answer form: ...', 'evidence period: evidence available as of 2026-09-21', 'geography: unspecified ...']
topic-02-target-01 policy= primary_attribution required= True critical= False ...
topic-03-target-01 policy= primary_attribution required= True critical= False ...
topic-04-target-01 policy= primary_attribution required= True critical= False ...

== claim_attribution (before adjudication filter) ==
credited: ['topic-01-target-01'] {'topic-01-target-01': 'primary_attribution'} | According to Acme Institute the measured efficiency ...
credited: ['topic-02-target-01'] {'topic-02-target-01': 'primary_attribution'} | According to Acme Institute the Acme widget efficiency measured under ...
credited: ['topic-03-target-01'] {'topic-03-target-01': 'primary_attribution'} | According to Acme Institute the efficiency printed on ...
credited: ['topic-04-target-01'] {'topic-04-target-01': 'primary_attribution'} | According to Acme Institute the Acme widget efficiency recorded in ...

== admitted_target_ids (after adjudication) ==
verdict= insufficient_evidence status= source_supported publishers= 1 before= ['topic-01-target-01'] after= [] | ...
verdict= insufficient_evidence status= source_supported publishers= 1 before= ['topic-02-target-01'] after= [] | ...
verdict= insufficient_evidence status= source_supported publishers= 1 before= ['topic-03-target-01'] after= [] | ...
verdict= insufficient_evidence status= source_supported publishers= 1 before= ['topic-04-target-01'] after= [] | ...

== policy gate on each measured shape ==
  independent_pair insufficient_evidence publishers= 1 -> False
  primary_attribution insufficient_evidence publishers= 1 -> False
  derivation insufficient_evidence publishers= 1 -> False
  (the same three rows once per claim, four claims)

answered targets: []
quality: partial 4
network attempts: []
```

So the claim is credited correctly at extraction (its atoms satisfy the required
dimensions **and** the target's `primary_attribution` policy) and the ids are
removed one step later, at adjudication. Each of the four claims is a faithful,
scoped, single-publisher attribution: the fact checker judges it
`insufficient_evidence` with `evidence_status="source_supported"`, which is what
plan line 622 demands ("Faithful scoped primary attribution is source_supported,
not verified").

### Code sites (file:line, current HEAD)

| Site | Role |
| --- | --- |
| `src/deep_research/agents/claim_clusters.py:669-691` | `claim_meets_support_policy`: `if verdict != "verified": return False` runs before any policy branch, so `primary_attribution` and `derivation` are unreachable for anything but a pair |
| `src/deep_research/agents/fact_checker.py:1718-1737` | `admitted_target_ids` rewrites `claim.target_ids` through that predicate; call sites 3093, 3141, 3185, 3233 |
| `src/deep_research/agents/fact_checker.py:1410-1412` | the packet path writes `verdict = "verified"` only when `verified_pair is not None`; its `elif supports` branch writes `insufficient_evidence` + `source_supported` |
| `src/deep_research/utils/types.py:2759+` | `target_is_answered`: the statement must *name* the target id, and statement target ids come only from claim/cluster ids, so the gate removes them everywhere |
| `src/deep_research/utils/types.py:2726-2735` | `statement_satisfies_support_policy` already accepts `source_supported` for `primary_attribution` ("the weaker policies accept that attribution") — unreachable in a real run |
| `src/deep_research/agents/planner.py:892` | `support_policy_for` stamps `primary_attribution` for exactly the official/measurement questions this row is about |

Effect: `claim_meets_support_policy` is currently equivalent to
`verdict == "verified"`, so `ClaimAttribution.target_policies` (plumbed from
`claim_attribution` through `ClaimTask` to the gate) and `atom_satisfies_policy`'s
per-policy distinction cannot change any product outcome.

### What the plan says (the gate contradicts it)

* line 124-125: "**Source-supported** is not verified. A primary report can
  support 'Report X estimates Y for population P in year T' without another
  publisher reproducing X's measurement. Preserve the legacy
  insufficient_evidence verdict when corroboration is absent, add
  evidence_status=source_supported... This deliberately corrects the previous
  blanket requirement that every reader fact have two independent works. It
  does not allow an unqualified settled assertion without the strict pair."
* line 128: "official measurements/definitions may require precise primary
  attribution".
* line 146: "A target is answered only when its reader statement satisfies
  required dimensions and support policy."
* line 404: "Official rule dates use primary-attribution policy, not a
  requirement for another organization's independent model of that date."
* line 945 (this task's own matrix row): `primary-attribution | accepted / 0 |
  Exact official measurement question answered as primary-attributed; not
  falsely verified`.
* line 1109, amendment item 6: "A strict verification badge was conflated with
  faithful primary attribution" — removing that conflation is the plan's point.

With the gate as it stands no `primary_attribution` or `derivation` obligation
can ever be answered from the source that answers the question, so every
official-measurement, official-rule and calculation question is unconditionally
`partial / 4`.

### Reproduce in one command

```
PYTHONPATH=<worktree>\src <venv>\python.exe scratch\drive.py primary-attribution
```

Real output (`scratch/drive-primary-attribution.txt`):

```
case: primary-attribution exit: 4 status: incomplete
quality: partial
hard failures: ['unanswered_critical_targets', 'unaccounted_required_targets']
targets: planned= 4 required= 4 answered= 0 critical= 1 unanswered_critical= ['topic-01-target-01']
         unaccounted= ['topic-01-target-01', 'topic-02-target-01', 'topic-03-target-01', 'topic-04-target-01']
coverage: 1.0 substantive: 0.0
answered targets: []
errors: []
...
    Status: incomplete
    Quality: partial (critic 9/10; 4/4 topics covered, 100%)
    Quality reasons: 2 gate failures (unanswered_critical_targets, unaccounted_required_targets)
    Coverage: 4/4 topics covered (substantive, 0%); 0/4 required targets answered; 0/1 critical targets answered
    Claims: 4 checked; 0 independently corroborated, 4 primary-source attributed, 0 contested, 0 not established
    Review: scored 1.00 (fingerprint 10af47cd9933)
```

Nothing else is wrong with the run: zero errors, zero rejected drafts, the
report renders a Key-facts table with the honest badge
(`primary-source attribution; independent corroboration not established`), the
Critic scores 9/10 and the terminal review is `scored 1.00`. The only hard
failures are the two that follow from the unanswered targets.

---

## 3. Measured GREEN under the proposed 3-line change

Patch: `scratch/gate-relax-proposal.patch` (46 lines, 2 files, applies cleanly
at `a122ca5`; `git apply --check` clean). One keyword added to the predicate,
and the claim's own status passed through from `admitted_target_ids`:

```python
    if verdict == "verified":
        if support_policy == "independent_pair":
            return supporting_publishers >= 2
        return support_policy in ("primary_attribution", "derivation")
    if verdict == "insufficient_evidence" and evidence_status == "source_supported":
        return (
            support_policy in ("primary_attribution", "derivation")
            and supporting_publishers >= 1
        )
    return False
```

Applied to the working tree (not committed), the same scenario
(`scratch/drive-primary-attribution-fixed.txt`):

```
case: primary-attribution exit: 0 status: completed
quality: accepted
hard failures: []
targets: planned= 4 required= 4 answered= 4 critical= 1 unanswered_critical= [] unaccounted= []
coverage: 1.0 substantive: 1.0
answered targets: ['topic-01-target-01', 'topic-02-target-01', 'topic-03-target-01', 'topic-04-target-01']
claims: [('insufficient_evidence', 'source_supported', ['topic-01-target-01'], ...), ... (4 rows)]
    Quality: accepted (critic 9/10; 4/4 topics covered, 100%)
    Coverage: 4/4 topics covered (substantive, 100%); 4/4 required targets answered; 1/1 critical targets answered
    Claims: 4 checked; 0 independently corroborated, 4 primary-source attributed, 0 contested, 0 not established
    Review: scored 1.00 (fingerprint cce172d8bd24)
```

The row's decisive assertion holds exactly as written: the exact official
measurement question is answered as primary-attributed (`attributed` statements,
badge `primary-source attribution; independent corroboration not established`)
and nothing is falsely verified — `0 independently corroborated`, because the
`verified_pair` badge still requires a real pair.

Blast radius, measured, with the patch applied
(`scratch/gate-relax-fullsuite.txt`):

```
1 failed, 4177 passed, 1 deselected, 2 warnings in 34.81s
FAILED tests/test_evaluation/test_config.py::test_every_target_prompt_fingerprint_is_pinned_against_prompt_drift
E       AssertionError: {'fact_checker': '044e4fe968f8'} != {'fact_checker': '246b797b9534'}
```

One failure, and it is the governed re-pin mechanism working as designed (the
`fact_checker` module source moved). The policy tests stay green: a contradicted
claim still earns nothing under any policy; the passage path still publishes
`unverified`/`source_supported` with no target; `independent_pair` still needs
`verified` **and** two supporting publishers, so a mirror or a second work from
one publisher is still refused.

The patch was reverted (`git checkout --` on both files), so the tree at
`a122ca5` carries no production change.

---

## 4. What this round did land

**Commit `a122ca5`** — `wip(task-12): land the offline replay harness and the
matrix's first row` (2 new files, no production module touched):

* `src/deep_research/e2e_evaluation/replay.py` — the offline replay harness.
  External boundaries substituted only: `ReplaySearch` (declared topics),
  `ReplayHTTP` (declared pages), a scripted completer that asserts the
  schema/target/read/evidence packet it is answering, in-memory long-term
  storage and deterministic embeddings. It builds the runtime through
  `runtime.assembly.build_runtime` (never `ScriptedGraphAgent`), drives it
  through the real `cli.main` with `--require-quality`, and captures every
  compiled agent so the production-class assertion is possible.
* `src/deep_research/e2e_evaluation/replay_matrix.py` — the versioned manifest
  (`REPLAY_CASE_MANIFEST_VERSION = 1`, per-row `version`, declared expected
  product result, decisive assertion, builder) with row 1 authored.

### The harness's production classes (measured, `scratch/probe-production-classes.txt`)

```
planner: deep_research.agents.planner.PlannerAgent
researcher: deep_research.agents.researcher.ResearcherAgent
source_evaluator: deep_research.agents.source_evaluator.SourceEvaluatorAgent
fact_checker: deep_research.agents.fact_checker.FactCheckerAgent
synthesizer: deep_research.agents.synthesizer.SynthesizerAgent
critic: deep_research.agents.critic.CriticAgent
publisher: builtins.NoneType
report_reviewer: deep_research.agents.report_review.ReportReviewer
network attempts: []
session: replay-primary-attribution-r1
review status: scored
statements: 16
report chars: 4430
```

`publisher` is `None` by production design — `terminal_publisher` then asks the
Synthesizer to write, which is the path the published artifacts take
(`report-replay-primary-attribution-r1-1.md`).

### Zero-network evidence

`network_denied()` patches `socket.socket`, `socket.create_connection` and
`socket.socketpair` — the Windows Proactor loop's self-pipe goes through
loopback, so only the `socketpair` call itself is allowed the real class — plus
the HTTP, provider and tracker transports, and records every attempt. Every run
quoted here ends with `network attempts: []`. The tracker is built with
`tracing_enabled=False`; `offline_credentials()` sets placeholder
`DEEPSEEK_API_KEY`/`TAVILY_API_KEY` and `LANGSMITH_TRACING=false` for the
duration of the call and restores the previous values afterwards, so no secret
is read and no LangSmith run is created.

### The three prototype harness defects the dispatch named

1. `read_urls:` vs `read_urls=` — fixed (`_context_line` accepts the packet's
   `- ` prefix, so the scripted researcher no longer re-requests a read URL;
   the ~30 `agent_tool_policy_rejected` observations per run are now zero).
2. Claims reaching adjudication with `targets=[]` — **not a harness defect**;
   it is the product gate in section 2. The harness scripts no ids: it parses
   the packets and answers them.
3. A stop condition reaching `accepted` — the same root cause. The run stops
   with "the last repair changed nothing substantive, so another pass would
   repeat it" because the obligation can never settle; under the proposed patch
   it reaches `accepted` in one pass.

Two further harness defects I found and fixed while authoring the row:

4. The scripted `ReportDraft` emitted one point per topic in **both** the
   executive summary and that topic's section; the composer's `(text, cluster)`
   dedupe correctly refused the section copy ("section N point 1: repeats an
   earlier statement", then "section N: no printable point"). The draft now
   returns an empty executive summary and one point per section, which is the
   shape the renderer already handles (`REPORT_SUMMARY_FALLBACK`).
5. Two answer-row label cells named words their row's evidence never states
   ("2025" for the method row, "measured efficiency" for the registry row), so
   the cell was refused. Each row's dimension cell now names what its own
   sentence carries ("official method", "official registry").

---

## 5. The question I need answered

**Ruling requested (R1): does the claim admission gate relax, or does the
matrix row re-scope?**

* **Option A — Task 12 takes the gate fix** (`scratch/gate-relax-proposal.patch`,
  measured in section 3): `primary_attribution`/`derivation` accept a
  `source_supported` claim (one complete, in-scope support; at least one
  publisher); `independent_pair` is untouched and still needs `verified` plus
  two supporting publishers. Cost: two production files outside this task's
  list, one governed `fact_checker` prompt re-pin, and Task 5's C4 ruling
  recorded as superseded. Evidence: the row reaches `accepted / 0` and the whole
  suite moves exactly one test.
* **Option B — the row re-scopes**: keep the gate and re-declare
  `primary-attribution` with an expectation today's product can meet. A positive
  row would then have to supply a genuine independent pair, which makes the
  row's decisive assertion ("answered as primary-attributed; not falsely
  verified") vacuous — and leaves every official-measurement, official-rule and
  calculation question unconditionally `partial / 4`.
* **Option C — a separate repair task owns the gate**, and Task 12 re-runs its
  matrix once that lands.

I recommend **A**, on the plan text quoted in section 2 — but it is a semantic
product rule with a reviewed ruling behind it, so I am not deciding it myself.

---

## 6. Matrix status (18 declared rows)

| | state |
| --- | --- |
| Rows declared in the manifest | 1 — `primary-attribution` (case version 1, manifest version 1) |
| Rows authored | 1 of 18; the other 17 builders are not written |
| Repetitions per case | the row was run 5 times while authoring; the decisive values (exit 4, `answered= 0`, both hard failures, zero network) were identical every time. The three-repetition matrix harness is not landed, so no repetition record is claimed. |
| Reviewed snapshot | the run's review fingerprint is `10af47cd9933` (blocked shape) / `cce172d8bd24` (under the proposed patch); no snapshot is published as release evidence this round |

Not started, all gated on R1: the remaining 17 case builders; the
`tests/test_e2e_evaluation/test_real_agents.py` RED->GREEN evidence with three
offline repetitions per case; the mutation tests (monkeypatched
`claim_evidence_pool` omitting B, `statement_source_urls` returning an invented
URL, `query_memory`-as-read); the cold/warm memory fixtures (repeated obsolete
high-confidence generated claims, valid original cache artifact, forged cache
metadata, fresh changed source) with per-repetition reset and initial-manifest
assertion; the late-candidate/late-section assertions at
`observation_summary_chars = 200`; `--production-parity`; the
`e2e_evaluation/{cases,evaluators,models,runner}.py` and `evaluation/*`
extensions; D-14 (offline fixture output still lands in the live namespace at
`output\evaluations\e2e\suite.json`); the two validation documents.

---

## 7. Gates run this round (real output)

Import target printed before the runs:
`...\agent-cli-quality-trace-plan\src\deep_research\__init__.py`.

```
$ python -m pytest tests/test_evaluation tests/test_e2e_evaluation tests/test_cli -q
1094 passed, 1 warning in 25.21s

$ python -m deep_research.e2e_evaluation suite --tier controlled --repetitions 3
broad-constraints: accepted; coverage 1.00; judge 1.00
comparative-conflict: accepted; coverage 1.00; judge 0.81
refinement-evidence-recovery: accepted; coverage 1.00; judge 0.86
Suite: accepted (3 repetitions per case)
Artifact: output\evaluations\e2e\suite.json
Network: zero (scripted dependencies only)
exit=0

$ python -m pytest -q
4178 passed, 1 deselected, 2 warnings in 34.45s

$ python -m ruff check src tests
All checks passed!

$ git diff --check
clean
```

The controlled suite still runs its three **legacy** `ScriptedGraphAgent` cases
(graph-only historical regressions, as the plan describes them); the new
real-agent matrix is not wired into that command yet, so its green result is not
evidence about the 18 rows.

---

## 8. Files changed

* `src/deep_research/e2e_evaluation/replay.py` — new, committed in `a122ca5`
* `src/deep_research/e2e_evaluation/replay_matrix.py` — new, committed in `a122ca5`
* `scratch/` (untracked, unstaged): `probe_gate.py` (the traced gate run),
  `probe_production_classes.py`, `drive.py` (scenario driver),
  `gate-relax-proposal.patch`, `gate-relax-fullsuite.txt`,
  `gate-focused-final.txt`, `gate-full-final.txt`, `gate-controlled-suite.txt`,
  `drive-primary-attribution.txt`, `drive-primary-attribution-fixed.txt`,
  `probe-production-classes.txt`, `drive-out/` (per-run packet dumps and summary)

No production module carries a change at `a122ca5`.

## 9. Self-review findings

1. My first scripted `ReportDraft` repeated each topic's sentence in the summary
   *and* its section; the composer correctly refused it as duplicate inflation.
2. Row 4's dimension cell originally named "measured efficiency", a phrase that
   row's own sentence never states; the cell attestation caught it, which is the
   check working as designed.
3. `_context_line` needed the packet's `- ` prefix, or the scripted researcher
   re-reads URLs it already read.
4. `socket.socketpair` must keep the real socket class on Windows, or asyncio's
   Proactor loop trips the network guard on its own self-pipe.
5. I applied the proposed production patch, measured it, and reverted it before
   committing, so the landed commit contains no unauthorised semantic change.

## 10. Concerns

1. The blocker is a product-semantics ruling, not something this task's file
   list can settle — and it decides whether any `primary_attribution` row can
   ever be green.
2. Round budget: two rounds of Task 12 have produced a working real-agent
   harness and a precise diagnosis, but no green matrix. If R1 is Option A the
   fix is three lines and this scenario is its regression test.
3. The controlled suite's artifact path shares the live evaluation namespace
   (D-14, mapped to this task) and is still open.
