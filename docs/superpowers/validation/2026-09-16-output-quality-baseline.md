# Output-quality baseline: frozen evidence, artifacts, and reconciliation

Date: 2026-09-16. Task 0 of
[the evidence-integrity and agent-production-readiness plan](../plans/2026-09-16-evidence-integrity-and-agent-production-readiness.md).
Status: **frozen baseline. Documentation only.**

This document freezes the measured state every later task argues from. It changes
no application code, test, or configuration. It creates no new run, spends no
provider call, and inserts no invented number: every figure below is traceable to
a hashed artifact or to a command recorded in
[the Task 0 report](../../../.superpowers/sdd/2026-09-16-evidence-integrity-and-agent-production-readiness/task-0-report.md).

**Immutability.** The artifacts inventoried here are historical evidence. Later
tasks must not overwrite them, copy replacements into this worktree, or treat
newer source content as if it were these bytes. A newly fetched copy of any cited
page or document would not recreate the historical bytes.

## 0. How to read this document

Every claim carries an evidence class. The classes are not interchangeable, and
the plan requires them kept apart.

| Class | Meaning |
| --- | --- |
| **L** | Local measurement: read directly from a file that exists on disk now, identified by absolute path and SHA-256. |
| **T** | Trace-derived historical count: recorded from the LangSmith trace by the [last-run trace review](2026-09-16-last-run-agent-trace-review.md). Not independently re-measured here. |
| **F** | Missing field: the value was never captured, or the artifact that would carry it is absent. Recorded as `not_diagnosable`, never inferred. |
| **I** | Interpretation: a judgment drawn from L/T/F. Always labelled, never presented as measurement. |

Trace-derived counts (**T**) are historical. They describe what the last run
reported and what the review read from its spans; they are not current agent
scores and are not a release result.

## 1. Commit and code-state inventory

| Item | Value | Provenance |
| --- | --- | --- |
| Branch | `codex/agent-cli-quality-trace-plan` | `git branch --show-current` (**L**) |
| Working branch HEAD / plan-amendment commit | `7fd440fc95674d3f0007fcfe37593838baf37309` — `docs(plan): ground agent quality fixes in last-run traces` | `git rev-parse HEAD` (**L**) |
| Execution base | `b2fa96bd1121bafd3775374e032cf5a817c0814d` — `Merge pull request #22 from RahulKrishGit/merge/planner-parity-into-main` (`origin/main`) | `git log -1 origin/main` (**L**) |
| Historical run application SHA | `2bc6665de59d0cec3d76f0bd0cdc5bd92ba3b180` — `fix(fact-checker): admit upstream-read evidence to the verification pool` | `git cat-file` (**L**); ancestor of HEAD, `git merge-base --is-ancestor` exit 0 |
| Historical trace / docs revision | `935cc9e09dc4f5659d0c3acd44f40236e76bab79` — `docs(validation): predeclare the evidence-pooling run` | `git cat-file` (**L**) |
| Prior plan amendment | `e2cbb8fcd38866f059709bb3c154d035a127289f` — `docs(plan): ground final quality program in cross-run evidence` | `git cat-file` (**L**) |
| Superseded plan revision | `dd1b93f68ee873da4a7dddb4dbe8082fc62c09c0` — `docs(plan): define agent production readiness program` | `git cat-file` (**L**) |
| Working tree | clean (`git status --porcelain` empty) before this task | (**L**) |

`2bc6665` and `935cc9e` identify **evidence**. They are both ancestors of merged
main `b2fa96b` and therefore of this worktree's HEAD, so the code under change in
Tasks 1–14 already contains them — being contained in the base is not the same as
being the execution base. The execution base for this program is `7fd440fc`,
which is `b2fa96b` plus the plan amendment and the trace review
(`git diff --stat b2fa96b..HEAD` → 2 files, 313 insertions, 21 deletions).

### 1.1 Baseline test state

Recorded from the controller's verified run at this commit, not re-run here:
`python -m pytest -q` → **3100 passed, 1 deselected, 2 warnings in 31.74s**
(**L**, recorded by the controller; the trace review records the same offline
baseline as 3,100 passed / 1 live deselected / 2 dependency deprecation
warnings). This is a regression baseline, not report-quality approval.

### 1.2 Code defects referenced by this baseline (anchors at HEAD, **L**)

| Anchor | Location at `7fd440fc` | Used by |
| --- | --- | --- |
| `_read_payload_urls` | `src/deep_research/agents/steps.py:223`, called at `:288` | TR-01 |
| `observation_summary_chars` default | `src/deep_research/utils/config.py:213` (`Field(default=200, ge=1)`); applied at `agents/base.py:284`, `agents/researcher.py:1133`, `agents/fact_checker.py:1385`, `agents/critic.py:1023` | TR-02 |
| Extraction prefix length | `agents/researcher.py:72` `DEFAULT_EVIDENCE_CHARS = 4000`; `agents/fact_checker.py:81` `FACT_CHECK_EVIDENCE_CHARS = 4000` | TR-02 |
| `_refinement_satisfied_sub_topics` | `agents/researcher.py:209`, called at `:1146` | TR-07 |
| Effective configuration | `config.yaml`: `llm.reasoning_effort: high` (l.19), `agents.tool_budget: 10` (l.64), `agents.observation_summary_chars: 200` (l.73), `graph.max_iterations: 3` (l.100), `evaluation.target_reasoning_effort: max` (l.114) with overrides `researcher: high`, `source_evaluator: high` (ll.115–117), `evaluation.output_directory: output/evaluations/` (l.122) | TR-02, TR-04, D-10, D-14 |

These anchors establish that the code paths the trace review names still exist at
this commit. They do **not** establish that any particular historical claim was
lost through them; that is the distinction §8 preserves.

### 1.3 Binding frame recorded from the plan's global constraints

Recorded so that the exit codes and counts in §3 and §4 can be read against the
contract they were produced under (**recorded from the binding constraints;
not re-derived here**):

- **CLI exit contract:** `0` completed non-strict; `1` configuration; `2` usage;
  `3` graph failure; `4` strict quality non-acceptance; `130` interrupt.
  *"Non-strict 0 is not an accepted-quality claim."* Every canary in §3 that
  produced a report exited **4**; the two Q1 aborts exited **2** (usage — argparse
  rejected `--require-quality`) and **3** (graph failure — planner failure). No
  run in this baseline exited 0.
- **pytest configuration:** `testpaths = ["tests"]`, `addopts = "-m 'not live'"`.
  That is why the 3,100-passed baseline reports exactly one deselected test.
- **Import trap:** the shared `.venv` has an editable install whose path file
  points at the **root** checkout's `src`. Any command run from this worktree must
  set `PYTHONPATH` to this worktree's `src` with cwd at the worktree root, or it
  silently runs the wrong tree. Every measurement in this document that used the
  interpreter set `PYTHONPATH` to
  `...\.worktrees\agent-cli-quality-trace-plan\src` first.
- **Counting units are distinct:** tokens, searches, transport attempts, tool
  calls, dropped requests, and event rows are different things. The previous
  plan's 250k/500k-token and 15/30-minute gates are removed and no release SLO is
  invented here.
- **Immutability:** a source denial, failed judge, missing trace, partial answer,
  or seeded diagnostic is never relabeled as a successful unseeded production
  run. §6.5 applies that rule to the `INFRASTRUCTURE FAILURE` records.

## 2. Immutable artifact inventory

### 2.1 Resolution of the plan's illustrative relative path

The Task 0 checklist writes `Get-FileHash -LiteralPath output/report-417fa...-3.md`
with a **relative** path. No such path exists in this worktree, and the plan's own
Global Constraints place these artifacts in the *old* worktree:
"Historical output artifacts remain in `.worktrees/cross-agent-planner-fix-parity`;
locate/hash them explicitly, do not fabricate missing copies in the new worktree."

Therefore the relative path is illustrative, not normative. Each artifact below
was hashed **at its real absolute location**, and no artifact was copied into this
worktree. Both the absolute source path and the SHA-256 are recorded (**L**).

### 2.2 Canonical last-run artifacts

Source directory (all three):
`C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.worktrees\cross-agent-planner-fix-parity\output\`

| Artifact | Bytes | SHA-256 | Matches trace review |
| --- | ---: | --- | --- |
| `report-417fa9338e10450784b459f89af98b1c-3.md` | 12876 | `2e6f60bb3b422a01bcceb001138be0f1e706c4fc6b6e2b604836a4d78a023597` | yes |
| `report-417fa9338e10450784b459f89af98b1c-3-evidence.md` | 38667 | `a482e9ca905109dc7987c638bd10e481043db1692d6265dfa98dddea24b06b8d` | yes |
| `cli-canary-20260915-223507-evidencepooling.log` | 180550 | `5e722bdc9807b03a88ec5885460b8db8d9f0b62c9a98f87e51990c5f6498eafb` | yes |

All three hashes reproduce the values recorded in the trace review's "Immutable
local evidence" table (**L**, independent recomputation).

All eleven canary logs are UTF-16LE with a BOM (`FF FE` at offset 0), so byte
sizes are roughly twice the character count. Hashing is on raw bytes (**L**).

### 2.3 All reader reports and ledgers in the historical output directory

21 `report-*.md` artifacts (**L**), each hashed in place. `bytes` and `sha256`
are raw-file values.

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `report-0a9f733149254f01be3d8bd4b386f400-0.md` | 28233 | `aef78f64fa5fc30a896e3f78a015677d95e8f5e7a71c3d8f1a33358eb0649142` |
| `report-854cddd33db749b3a193fe1f8a125e19-0.md` | 25451 | `329fa88a99a617ed566b325db2b85cf38f7a0c58518e8fd976e3ba05139b63a2` |
| `report-854cddd33db749b3a193fe1f8a125e19-1.md` | 50850 | `96dc103819657ff1c638d6c9029acc0e60356278c01fe67e8b19dff5e3952a90` |
| `report-854cddd33db749b3a193fe1f8a125e19-2.md` | 85464 | `908bb66498f7466cc017fa509ca17fec7cf75919a0c17cbaff2840da2091f94a` |
| `report-854cddd33db749b3a193fe1f8a125e19-3.md` | 127872 | `abe0d61709979d459ea7e2ec14585878c58715780f3c25883363061203603a6c` |
| `report-50cbf5926e8b479ea90793312396e440-3.md` | 10724 | `0867c065c29d369115deabe306a329eb924ecfa14cbf4a894d54ef2ff545be01` |
| `report-50cbf5926e8b479ea90793312396e440-3-evidence.md` | 24119 | `0812c278f0f4a0045a023d4e89abbf3402cc297cdee1303efb9a53dda062b6ef` |
| `report-ce8911eef28847d7ac739be450430ca8-3.md` | 11389 | `c87214f7b9b666f693a365061128c4ed72ba8336f55a1b0108207eb73c1d057b` |
| `report-ce8911eef28847d7ac739be450430ca8-3-evidence.md` | 26545 | `6c5d0843ff13aa7a7a27dbb21bb6ede11b9353ae15f0c7937849e3e6497939a1` |
| `report-b600945da4d54379943f6f81b31d84da-3.md` | 12127 | `75f3c26d118a2c3c5733c4ed56fbde6c8584c4b75d92e9fae8014b5e9da04927` |
| `report-b600945da4d54379943f6f81b31d84da-3-evidence.md` | 36156 | `35201fb9f3ce1dd77b9eff37168ab1a4c6c23af792e283cc72190dcb129a8b62` |
| `report-eb9186f2aa6b4890b9f7b4514bd65930-3.md` | 14442 | `79811c9881a261c9f59c2db85e2afc6ee021e5ef6c08bda42a8e2091661b35cc` |
| `report-eb9186f2aa6b4890b9f7b4514bd65930-3-evidence.md` | 48776 | `cec5d79aaa67d65dd8d432f578bd8ba5143d50a50192b0bf7001e91460810bfc` |
| `report-d8894023f1de48b7b3b3182634a15cb0-3.md` | 15931 | `ef6d65418da8f4e2aefb4374cf2c9d1999e37a9222b9fb9a50e13063bee72da1` |
| `report-d8894023f1de48b7b3b3182634a15cb0-3-evidence.md` | 52353 | `a204ffefdca484d3a411f9f2fc6846f3c59c6a408183b69066e2016af30d4190` |
| `report-97921fe49c89402ab13c5562dbd97b3a-3.md` | 11809 | `17d226ccf5fbb277e9c5ca5f1fd32d4a66e2f4dced668a23b6c77e53c132f3dc` |
| `report-97921fe49c89402ab13c5562dbd97b3a-3-evidence.md` | 42329 | `2931c8a694c6770acb8e1de96b33eb08c9b1c0f1eed388f2fcae9346462a22e7` |
| `report-480fd56130e349208b56307f98f89fab-3.md` | 13529 | `b904d88c395e9bf18ce2f24822dbce2f77b3376ad985f824e6b070fe4485a1e3` |
| `report-480fd56130e349208b56307f98f89fab-3-evidence.md` | 42673 | `3f65444aecf95780dbe76d23535471d760e8d85af74182c1caa5688cb8107d5f` |
| `report-417fa9338e10450784b459f89af98b1c-3.md` | 12876 | `2e6f60bb3b422a01bcceb001138be0f1e706c4fc6b6e2b604836a4d78a023597` |
| `report-417fa9338e10450784b459f89af98b1c-3-evidence.md` | 38667 | `a482e9ca905109dc7987c638bd10e481043db1692d6265dfa98dddea24b06b8d` |

Two non-report, non-log files in the same directory (**L**):
`output/_suite.txt` (7296 bytes, `6e498018601ed0f4bae47343d09e23ec147ea397274666fbb0c5bfe377426e2c`) and
`output/cli-run-2.log` (19222 bytes, `5cb0f01b958b001a07de40303f2ee5f2bf91365fff06f288c0f8153ba1b10bac`).

### 2.4 Where each artifact was searched for

| Location searched | Result |
| --- | --- |
| This worktree (`...\.worktrees\agent-cli-quality-trace-plan\output\`) | Contains **only** two pytest-written planner fixture directories (see §6.4). No report, ledger, or canary log. |
| Root checkout (`...\deep-research\output\`) | Contains **only** `evaluations\`. No report, ledger, or canary log. |
| Old worktree (`...\.worktrees\cross-agent-planner-fix-parity\output\`) | **All** artifacts found here, exactly one copy each. |
| Whole repository recursive search by filename | Each of the six artifacts named in EF-3 / the plan returns exactly **1** hit, in the old worktree. |

### 2.5 Warm-state artifact: the persisted memory store

| Item | Value |
| --- | --- |
| Path (absolute) | `C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.worktrees\cross-agent-planner-fix-parity\memory\chroma\chroma.sqlite3` |
| Bytes | 430080 |
| SHA-256 | `22c6b1f8e75964485c626758c6ac1971b7fa2ca8c859d43c4b441b2416336846` |
| Contents (**L**) | 36 entries, all `agent_id=synthesizer`, `entry_type=finding`, `verdict=verified` |
| Sessions of origin (**L**) | `854cddd33db749b3a193fe1f8a125e19` → 35 entries; `0a9f733149254f01be3d8bd4b386f400` → 1 entry |
| Distinct frozen fact texts (**L**) | 10, each duplicated 3–5 times (5 + 4·4 + 3·5 = 36) |

Duplicate structure (**L**, exact counts of identical stored text):

| Stored text (frozen) | Copies | Canonical URL |
| --- | ---: | --- |
| "China accounts for over 60 percent of global processing of cobalt, lithium, and manganese and over 70 percent of global extraction of natural graphite." | 5 | `rff.org/publications/reports/resource-nationalism-and-the-resilience-of-critical-mineral-supply-chains` |
| "The IEA reports that China retained its dominance over the midstream and downstream EV and storage battery supply chain in 2024, processing 70–95% …" | 4 | `iea.blob.core.windows.net/.../GlobalCriticalMineralsOutlook2025.pdf` |
| "The IEA projects that, looking ahead to 2035, China is set to supply over 60% of refined lithium and cobalt …" | 4 | same IEA PDF |
| "The IEA projects that by 2030 China is set to supply 70% of battery-grade manganese sulphate and 75% of purified phosphoric acid." | 4 | same IEA PDF |
| "The IEA states that the high market concentration of critical mineral supply means there is a risk of significant shortfalls …" | 4 | same IEA PDF |
| "As of the end of 2023, 2,600 GW of proposed U.S. generation and storage projects were waiting for grid access, with typical wait times stretching to five years or more." | 3 | `novoco.com/notes-from-novogradac/resolving-the-interconnection-queue-bottleneck-…` |
| "Lawrence Berkeley National Laboratory's annual interconnection review, published in December 2025, found that U.S. interconnection queue capacity dropped for the first time in 2024." | 3 | same Novoco page |
| "FERC's Order 2023 mandated U.S. interconnection queue reform … with Order 2023-A reaffirming most of it." | 3 | same Novoco page |
| "According to Lawrence Berkeley National Laboratory studies, clean energy projects that withdrew from interconnection queues faced much higher interconnection costs." | 3 | `energy.gov/cmei/i2x/articles/tackling-high-costs-and-long-delays-clean-energy-interconnection` |
| "The total capacity of energy projects in U.S. interconnection queues grew 40% year-over-year in 2022, with more than 1,350 GW of generation and 680 GW of storage waiting for approval to connect, according to Berkeley Lab." | 3 | `utilitydive.com/news/grid-interconnection-queue-berkeley-lab-lbnl-watt-coalition-wind-solar-renewables/647287` |

This store existed **before** the last run and is the pool the run's Planner read
from. The last run was therefore a **warm-memory** run, not a cold-memory one.

**Reconciliation gap (F).** The trace review records that the last run's *initial
state* contained five previous-session findings: three copies of the Novoco
queue/wait-time claim and two copies of the Utility Dive 2022 growth claim (**T**).
The whole-database measurement above finds **three** stored copies of the Utility
Dive text, not two (**L**). The two numbers measure different things — database
contents now versus the initial state of one run — and the local artifacts do not
retain the run's initial state, so the difference cannot be resolved here.
Recorded as an unresolved reconciliation gap, not as a discrepancy in either
source.

## 3. Canary-session inventory

All eleven CLI canary logs live in
`...\.worktrees\cross-agent-planner-fix-parity\output\`. Every one is UTF-16LE
(**L**). "CLI exit" is the exit code the log itself states, or the exit code
recorded in the corresponding validation document.

**Literal question.** Runs 1–10 all used the same standing question, quoted
verbatim in each predeclaration and reproduced in every published report title:

> What are the current constraints on grid-scale battery storage deployment?

(Eleven logs exist for ten authorizations because one Q1 attempt aborted before
any provider call, and one Q1 authorization produced both a paid planner-failure
attempt and a completed pipeline run.)

| # | Log (in `output/`) | Bytes | SHA-256 | Session | Literal question | CLI exit |
| ---: | --- | ---: | --- | --- | --- | --- |
| 1 | `cli-canary-20260915-183058-task15.log` | 196100 | `d6c303d75c8d6001fcb77b2ea60cba002fdcf1bcfd6cfbb453a288e295c8debe` | `ce8911eef28847d7ac739be450430ca8` | standing question (**L**, report title) | 4 (**L**, log tail) |
| 2 | `cli-canary-20260915-192428-agentbaseline.log` | 6148 | `96795e1abe122cb740c5104b49d8f751f521d0d86b7ed0e0f26dd8bfeacd1355` | `083f77bff1b94c929ab1ff7892b2e006` | standing question (**L**, predeclaration) | `Status: failed`; no report or ledger written (**L**) |
| 3 | `cli-canary-20260915-194430-agentfixes.log` | 203312 | `170fdf2c01c8c84b1a84be398d541ea324f87dc0fc04fdabb67cc7a05bf44290` | `b600945da4d54379943f6f81b31d84da` | standing question | 4 |
| 4 | `cli-canary-20260915-201604-corroboration.log` | 191856 | `a453cb8cac096a14c9e6ec6a4e05843ae1c1e82319aae5ce1910dbcb850b24c3` | `eb9186f2aa6b4890b9f7b4514bd65930` | standing question | 4 |
| 5 | `cli-canary-20260915-202101-q1.log` | 1456 | `bacb342af37b8b0e4a1faedd75854dd82acd424cb803c7db2df7fdf2c43f8ee7` | *(none — argparse rejected the invocation)* | standing question | **2** (**L**, `unrecognized arguments: --require-quality`) |
| 6 | `cli-canary-20260915-202152-q1.log` | 2346 | `3ea7a9e361fb1344e4493190dc655ccb57ce84abeaddc625a527d97b3380e1d1` | `20a698db4b8044fab166d6df73414086` | standing question | **3** (**L**, planner failure, recorded in `2026-09-15-cli-q1-failed-validation.md`) |
| 7 | `cli-canary-20260915-204454-q1.log` | 28558 | `da07679972e1e3e54174e12542002483e66d486a8e49539c67a3452210a27be7` | `50cbf5926e8b479ea90793312396e440` | standing question | 4 |
| 8 | `cli-canary-20260915-204851-resource.log` | 197312 | `6bb0e78a999639ff4dcfb1e35a715669727b84803f7e1d5268fc92a87824833d` | `d8894023f1de48b7b3b3182634a15cb0` | standing question | 4 |
| 9 | `cli-canary-20260915-212726-publisherretention.log` | 192932 | `8d31fb412fb10add34c09727c1b93d8ac3366224805116d0945809e90febc5bb` | `97921fe49c89402ab13c5562dbd97b3a` | standing question | 4 |
| 10 | `cli-canary-20260915-215848-corroborationcriterion.log` | 195944 | `520c5020bb0be5a55bb6c5acce9c25004cedfbfdbfb048084a78d415fd98f14f` | `480fd56130e349208b56307f98f89fab` | standing question | 4 |
| 11 | `cli-canary-20260915-223507-evidencepooling.log` | 180550 | `5e722bdc9807b03a88ec5885460b8db8d9f0b62c9a98f87e51990c5f6498eafb` | `417fa9338e10450784b459f89af98b1c` | standing question | 4 |

Every exit-4 log ends with the same sentence, quoted verbatim (**L**):
*"error: the report was not accepted by the terminal quality gates;
`--require-quality` was set, so this run exits 4."*

### 3.1 Measured outcomes of the four latest canary sessions (**L**)

Values below are read from the summary block of each log. Tool counts are tool
invocations, not provider HTTP transport requests; "failed" is the log's own
failure count for that tool.

| Measure | resource `d8894023` | publisher retention `97921fe4` | corroboration criterion `480fd561` | **evidence pooling `417fa933` (latest)** |
| --- | --- | --- | --- | --- |
| Status | `max_iterations` | `max_iterations` | `max_iterations` | `max_iterations` |
| Critic | 4/10 | 4/10 | 4/10 | **5/10** |
| Topics covered | 6/6 (100%) | 5/6 (83%) | 6/6 (100%) | **4/6 (67%)** |
| Cited / scored sources | 6 / 6 | 4 / 4 | 8 / 8 | **8 / 8** |
| Verified / contradicted | 3 / 1 | 4 / 0 | 6 / 0 | **5 / 0** |
| Integrity (dupes / rows / uncited) | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 | **0 / 0 / 0** |
| `web_search` calls | 352 | 312 | 332 | **307** |
| `web_scraper` calls (failed) | 41 (18) | 29 (15) | 48 (23) | **33 (18)** |
| `document_reader` calls (failed) | 28 (2) | 64 (8) | 35 (4) | **45 (1)** |
| `query_memory` calls | 69 | 75 | 75 | **64** |
| `write_document` calls | 2 | 2 | 2 | **2** |
| DeepSeek attempts reserved (ceiling 700) | 258 | 268 | 260 | **245** |
| OpenAI attempts reserved (ceiling 60) | 0 | 0 | 0 | **0** |
| Tavily attempts reserved (ceiling 450) | 352 | 312 | 334 | **307** |
| Tokens reported post-response | 831,551 | 905,364 | 874,059 | **815,664** (655,682 in / 159,982 out) |

The reader-report word counts for the same four runs, measured by whitespace
splitting (**L**): resource **2,332**; publisher retention **1,746**;
corroboration criterion **1,938**; evidence pooling **1,873**. The corroboration
and resource predeclarations quote 2,099 and 2,332 words respectively for their
*previous* runs, and those numbers reproduce against
`report-eb9186f2…-3.md` (**2,099**) and `report-d8894023…-3.md` (**2,332**) (**L**).
Report length is a structural diagnostic, not a semantic score.

### 3.2 Log filename time versus trace `start_time` (**L**, unresolved convention)

Each log's summary carries a LangSmith `Trace:` URL with a `start_time` query
parameter. For nine of the eleven logs that value is exactly **7 hours ahead** of
the timestamp embedded in the log's own filename (e.g. filename
`...-223507-...` ↔ `start_time=2026-09-16T05%3A35%3A10.956109%2B00%3A00`). For the
two Q1 logs the two values agree (`...-204454-q1.log` ↔
`start_time=2026-09-15T20%3A45%3A09.462225%2B00%3A00`), which places them
**earlier** than the nine others by filename and **later** than only
`20260915-183058` by filename ordering. The local artifacts do not state which
clock each field uses, so the offset's cause is unresolved (**F**). Log *order*
below uses the trace `start_time` values, which match the documented narrative;
file modification times were **not** used to establish recency.

Measured order (**L**, by trace `start_time`):
`50cbf592` (09-15 20:45Z) → `ce8911ee` (09-16 01:31Z) → `083f77bf` (02:24Z) →
`b600945d` (02:44Z) → `eb9186f2` (03:16Z) → `d8894023` (03:48Z) →
`97921fe4` (04:27Z) → `480fd561` (04:58Z) → `417fa933` (05:35Z).

**Declared run ordinals conflict (F).** The predeclarations claim:
agent-baseline "this is the fourth";
agent-fixes "Fourth run of the standing authorization";
corroboration "Fifth run"; resource "Sixth run"; heat-pump "Seventh run";
publisher-retention "Eighth run"; corroboration-criterion "Ninth run";
evidence-pooling "Tenth run".
Two documents both claim the fourth slot, and the Q1 authorization is counted as
one run in `2026-09-15-release-status.md` ("Three of ~10 authorized live runs are
spent") but as two in the agent-baseline predeclaration. The measured order above
agrees with the declared sequence only if the agent-fixes run is fifth. This
conflict is recorded, not resolved.

## 4. Reconciliation of the latest result

The plan's headline: **partial, critic 5/10, coverage 4/6, 16 checked claims,
5 provisionally verified, approximately 815,664 tokens.** Each component, with
its class:

| Component | Value | Class | Source |
| --- | --- | --- | --- |
| Terminal status | `max_iterations` | **L** | latest log summary |
| Critic score | 5/10 | **L** | latest log summary + ledger |
| Coverage | 4/6 (67%) | **L** | latest log summary |
| Checked claims | 16 | **L** | ledger "Canonical counts" + 16 registry rows |
| Verified / unverified / insufficient | 5 / 3 / 8 | **L** | ledger registry, counted row by row |
| Contradicted | 0 | **L** | ledger registry |
| Verification passages stored | 15 | **L** | ledger "Canonical counts" + 15 passage rows |
| Sources assessed / cited | 10 / 8 | **L** | ledger registry; 2 listed "reviewed, not cited" |
| Retrieved findings | 36 | **L** | ledger "Canonical counts" |
| Ledger error rows | 78 (rows numbered 1–78) | **L** | ledger "Run errors" table |
| Trace error records | "80 accumulated error records" | **T** | trace review |
| Failed external calls | **not equal to either count** | **I** | trace review: the 80 error rows "are not 80 failed external calls" |
| Tokens | 815,664 total (655,682 in / 159,982 out) | **L** | latest log summary |
| Report references | **8** (not 6) | **L** | counted in `report-417fa933…-3.md` |
| Report words | 1,873 | **L** | whitespace split |
| Backmatter share | 15.9% (boundary: `## Methodology` heading) | **L** | this document's measurement, not a production gate |

**Reconciliation item R1 — error records 78 vs 80 (unexplained, F).** The ledger's
`## Run errors` table has **78** numbered data rows; the trace review records
**80 accumulated error records** in the trace. The trace review states its
tool/LLM counts deliberately exclude "the graph wrapper or aggregated error
rows", which is one plausible reason a trace-level error count exceeds the
ledger's emitted records — but the two artifacts do not share a counting unit
definition and neither states which rows the 80 includes. The 2-row difference is
recorded as unexplained; no cause is asserted.

**Reconciliation item R2 — token figures are post-response usage totals.** The log
labels them explicitly: "Tokens (reported post-response, not a cost estimate)".
They are not pre-request ceilings and not a dollar cost (**I**).

**Reconciliation item R3 — "approximately 815,664" is exact in the artifact.** The
plan writes "approximately 815,664 tokens"; the log states 815,664 exactly. The
approximation is in the plan's prose, not in the measurement.

### 4.1 Prior resource / publisher / corroboration runs

| Run | Declared purpose (predeclaration) | Declared success measure | Measured outcome | Class |
| --- | --- | --- | --- | --- |
| resource `d8894023` | Make an access denial actionable; stop retrying a refused host; publish which sub-topic was skipped and why | Failed page reads fall; claims with ≥2 independent domains rises above 0; critic rises from 3/10 | Failed reads 18 of 41; critic 4/10 (rose); coverage 6/6 but the report still says technical/system-integration constraints are absent | **L** (log + report) / **I** (interpretation) |
| publisher retention `97921fe4` | Group retained findings by publisher, not URL; demand reads over searches in verification | Scrape failure rate falls; claims with ≥2 independent domains rises above 0; critic rises toward 7 | Failed reads 15 of 29; critic 4/10 (unchanged); "Claims with ≥2 independent domains" remains **0 of 18** by the predeclaration's own last-run baseline, and the finished ledger still lists **one** source per claim | **L** (log + ledger) / **I** |
| corroboration criterion `480fd561` | Move the corroboration demand into the criterion the Researcher stops on | Claims with ≥2 independent domains rises above 0; critic rises from 4/10 | Critic 4/10 (unchanged); 6 verified claims, 8 cited sources, coverage 6/6; plan §1 records "no checked safety/performance risk claim and no supported supply-chain-to-deployment mechanism" | **L** (log) / **T** (plan §1) |
| evidence pooling `417fa933` (latest) | Admit the union of the verification loop's reads and upstream-read URLs to the verification pool | Claims verified rises materially from 6 of 18; insufficient falls from 9; critic rises from 4/10 | Verified 5, insufficient 8; critic 5/10 (rose); coverage fell to 4/6 | **L** (log + ledger) |

The predeclarations are pre-committed interpretations, not measurements. Their
success columns are quoted here as declared expectations; the "measured outcome"
column is what the artifacts actually show. Where the two differ, the artifact
wins.

### 4.2 What is a local measurement, what is trace-derived, what is missing

| Statement | Class | Basis |
| --- | --- | --- |
| Last run: 245 LLM requests, 307 searches, 33 HTML reads, 45 PDF reads, 64 memory queries, 19 failed read calls, split by agent | **T** | trace review's per-agent table, from 1,079 spans |
| Last run: 5/10, 4/6, 16 claims, 815,664 tokens, 78 ledger error rows, 15 passages, 8 references | **L** | latest log + latest ledger + latest report |
| TR-01 reproduced offline as a code defect | **L**-equivalent code reproduction | trace review, "An offline reproduction using only a recalled assertion and URL returned: classified_read_urls = (…), independent_publishers = […], included_in_read_only_prompt = True". Independently re-checked here only to the extent that the code anchors still exist at HEAD (§1.2). |
| "21 of 45 document calls read the same 2025 LBNL PDF" | **T** | trace review; not locally re-measurable (no per-read log) |
| Whether the historical upstream bytes for any atom contained the claim's figures | **F** | no fetched document/page bodies exist anywhere on disk (§7) |
| Why the critic scored 5 and not higher | **I** | trace review: "consistent with a broad report still missing important answers" |

## 5. The latest run, read from its own artifacts

Structural facts measured directly from
`report-417fa9338e10450784b459f89af98b1c-3.md` and its ledger (**L**):

- The **Constraint ranking** table has five rows; the "Deployment mechanism"
  cell reads `not stated` in **all five**; `not stated` occurs **7** times in the
  report.
- **8 references** are listed. The ledger's source assessment has **10** scored
  sources, of which `caiso.com/.../2024-special-report-on-battery-storage…` and
  `eia.gov/todayinenergy/detail.php?id=46756` are explicitly "reviewed, not
  cited".
- Internal identifiers leak into reader prose: the strings `C001` and `C011`
  appear in the "Uncertainty and conflicting evidence" section, in the sentence
  "C001 and C011 are duplicates of the same figure rather than independent
  corroboration".
- The uncertainty prose contains a figure it declares it is not reporting:
  "Newer queue figures referenced in review (including **890 GW** of storage in
  interconnection queues) are not present in the checked-claims packet used here
  and are therefore not reported."
- The methodology claims "The limitations this pass discloses are listed with the
  uncertainty above, so no limitation is stated twice", while
  "Some steps of this research pass failed" appears both in the uncertainty list
  and again under "**Limitations recorded for this pass**".
- Two registry rows carry the **same** claim text under **different** claim IDs:
  #1 `381954b8…` (verified 0.78) and #11 `19db7992…` (verified 0.70).

Trace-level facts about the same run (**T**): 93 Researcher LLM requests against
8 HTML + 12 PDF reads; 121 Fact Checker requests against 25 HTML + 33 PDF reads;
29 Critic searches with no page-read capability; PDF reads succeeded 44/45 and
HTML reads 15/33; the same 2025 LBNL PDF was read 7 times by the Researcher and
14 times by the Fact Checker (21 of 45 document calls).

## 6. Per-agent `results.json` inventory

### 6.1 Scope and method

`results.json` files were enumerated under the historical output directory and
every one was parsed for its declared case version, target configuration, status,
per-repetition gate results, judge status, and judge verdict (**L**).

| Location | Count |
| --- | ---: |
| `...\cross-agent-planner-fix-parity\output\evaluations\` (recursive) | 70 |
| `...\cross-agent-planner-fix-parity\output\eval2\` (recursive) | 11 |
| **Total** | **81** |

Two of the 81 are test-harness fixtures, not evaluations (§6.4), leaving **79**
real evaluation records.

### 6.2 Target configuration actually recorded in the files (**L**)

Every record declares a `metadata.target_model_configuration` and fingerprints.
The observed combinations:

| Field | Observed values |
| --- | --- |
| `target_model` | `deepseek-v4-flash` (81 of 81) |
| `judge_model` / `judge_reasoning_effort` | `deepseek-v4-flash` / `max` (81 of 81) |
| `judge_configuration_fingerprint` | `924caf47aa0d` (41), `99234793b79f` (40) |
| `judge_temperature` | `0.0` (all) |
| `rubric_version` / `case_registry_version` | `1` / `1` (all) |
| `git_dirty` | `True` (79), `False` (2 — exactly the two fixtures of §6.4) |

`target_reasoning_effort` varies, and it is tightly paired with the
`configuration_fingerprint` (**L**, 81 files):

| `target_reasoning_effort` | `configuration_fingerprint` | Records |
| --- | --- | ---: |
| `max` | `fddbc542feff` | 14 |
| `max` | `47ca1fe8eb9b` | 10 |
| `max` | `55095b347272` | 9 |
| `max` | `a218a7e14261` | 7 |
| `max` | `8dfd57c9f0aa` | 6 |
| `max` | `963a1dc926c3` | 1 |
| `max` | `5e39b80d6400` | 1 |
| `max` | `2f6e2ea33caf` | 2 *(fixtures)* |
| `high` | `985f91c67e11` | 16 |
| `high` | `132ded95e018` | 9 |
| `high` | `f5182856a67c` | 6 |

So **31 of 81** records were produced at `high` effort and **50** at `max`.

**This is the plan's §1 finding, confirmed locally:** evaluation and production can
resolve **different** reasoning overrides. In the `output/eval2/` set the
researcher and source-evaluator records declare `target_reasoning_effort: high`
under fingerprint `f5182856a67c`, while the planner and fact-checker records in
the *same* set declare `max` under `8dfd57c9f0aa`; the standalone
`production-readiness-v2-planner-r1` record also declares `max` under
`8dfd57c9f0aa`. The mechanism is the evaluation layer's own override resolution:
`config.yaml` sets `evaluation.target_reasoning_effort: max` (l.114) and the
override map (ll.115–117) sets only `researcher: high` and
`source_evaluator: high`, while the base `llm.reasoning_effort` is `high` (l.19).
A `high`-effort evaluation therefore does not validate `max`-effort production
merely because construction is shared; the effective override must be resolved
explicitly at the declared candidate.

`git_dirty: True` on every real record means no evaluation in this inventory was
taken from a clean tree. That is a limitation on reproducibility, not a
correctness claim.

### 6.3 Full inventory

Columns: `case` is `case_id` with its declared `case_version`; `reps` is the
number of repetitions; `avg` is the record's `average_quality`; `failed gates`
lists the **failed** gate ids — for a multi-repetition record, one entry per
repetition in repetition order, separated by `, `, with `/` joining the ids that
failed within one repetition; `judge status` and `judge quality` summarise all
repetitions. `—` means the file declares no value.

The 81 rows cover **240 repetitions** in total. 122 repetitions carry a judge
verdict with `agent_specific` scores; 116 are `judge_not_run`.

| # | `results.json` (relative to the old worktree's `output/`) | agent | tier | case (version) | reps | status | avg | failed gates | judge status | judge quality |
| ---: | --- | --- | --- | --- | ---: | --- | ---: | --- | --- | --- |
| 1 | `output/eval2/fact-checker/pr2-fact-check-r1-fact-checker-live-20260913T184224Z-16d2a8f/results.json` | fact_checker | live | `fact-checker-live-verification` v1 | 1 | REVIEW REQUIRED | 0.886 | none | scored | 0.81 |
| 2 | `output/eval2/fact-checker/pr2-fact-check-r2-fact-checker-live-20260913T184408Z-16d2a8f/results.json` | fact_checker | live | `fact-checker-live-verification` v1 | 1 | REVIEW REQUIRED | 0.8095 | none | scored | 0.6825 |
| 3 | `output/eval2/fact-checker/pr2-fact-check-r3-fact-checker-live-20260913T184706Z-16d2a8f/results.json` | fact_checker | live | `fact-checker-live-verification` v1 | 1 | FAILED | 0.826 | budgets_respected | scored | 0.81 |
| 4 | `output/eval2/planner/pr2-planner-r2-planner-live-20260913T183407Z-16d2a8f/results.json` | planner | live | `planner-live-scope` v1 | 1 | REVIEW REQUIRED | 0.8755 | none | scored | 0.7925 |
| 5 | `output/eval2/planner/pr2-planner-r3-planner-live-20260913T183517Z-16d2a8f/results.json` | planner | live | `planner-live-scope` v1 | 1 | REVIEW REQUIRED | 0.9115 | none | scored | 0.8525 |
| 6 | `output/eval2/researcher/pr2-researcher-r1-researcher-live-20260913T183632Z-16d2a8f/results.json` | researcher | live | `researcher-live-evidence` v1 | 1 | REVIEW REQUIRED | 0.8767 | none | scored | 0.7945 |
| 7 | `output/eval2/researcher/pr2-researcher-r2-researcher-live-20260913T183816Z-16d2a8f/results.json` | researcher | live | `researcher-live-evidence` v1 | 1 | REVIEW REQUIRED | 0.847 | none | scored | 0.745 |
| 8 | `output/eval2/researcher/pr2-researcher-r3-researcher-live-20260913T183922Z-16d2a8f/results.json` | researcher | live | `researcher-live-evidence` v1 | 1 | REVIEW REQUIRED | 0.8485 | none | scored | 0.7475 |
| 9 | `output/eval2/source-evaluator/pr2-source-eval-r1-source-evaluator-live-20260913T184027Z-16d2a8f/results.json` | source_evaluator | live | `source-evaluator-live-ranking` v2 | 1 | REVIEW REQUIRED | 0.904 | none | scored | 0.84 |
| 10 | `output/eval2/source-evaluator/pr2-source-eval-r2-source-evaluator-live-20260913T184108Z-16d2a8f/results.json` | source_evaluator | live | `source-evaluator-live-ranking` v2 | 1 | REVIEW REQUIRED | 0.9115 | none | scored | 0.8525 |
| 11 | `output/eval2/source-evaluator/pr2-source-eval-r3-source-evaluator-live-20260913T184145Z-16d2a8f/results.json` | source_evaluator | live | `source-evaluator-live-ranking` v2 | 1 | REVIEW REQUIRED | 0.922 | none | scored | 0.87 |
| 12 | `output/evaluations/critic/cross-agent-planner-fix-parity-baseline-critic-critic-controlled-20260910T012037Z-d6a082c/results.json` | critic | controlled | `missing-evidence-or-budget-exhausted` v1 | 3 | FAILED | — | required_fields_present, required_fields_present/no_prohibited_calls, required_fields_present/no_prohibited_calls | judge_not_run, scored | 0.828–0.8575 |
| 13 | `output/evaluations/critic/cross-agent-planner-fix-parity-confirmation-critic-critic-controlled-20260910T013054Z-d6a082c/results.json` | critic | controlled | `missing-evidence-or-budget-exhausted` v1 | 3 | FAILED | — | required_fields_present/no_prohibited_calls, required_fields_present, required_fields_present/no_prohibited_calls | judge_not_run, scored | 0.48–0.8055 |
| 14 | `output/evaluations/critic/cross-agent-planner-fix-parity-critic-confirmation-c74a4f2-critic-live-20260911T053652Z-c74a4f2/results.json` | critic | live | `critic-live-review` v1 | 1 | INFRASTRUCTURE FAILURE | — | none | judge_not_run | — |
| 15 | `output/evaluations/critic/cross-agent-planner-fix-parity-judge-native-schema-critic-canary-2e8b25f-critic-live-20260911T191641Z-2e8b25f/results.json` | critic | live | `critic-live-review` v1 | 1 | FAILED | 0.4715 | none | scored | 0.2525 |
| 16 | `output/evaluations/critic/cross-agent-planner-fix-parity-live-20260910-critic-critic-live-20260910T191555Z-d697ff6/results.json` | critic | live | `critic-live-review` v1 | 1 | FAILED | 0.4055 | none | scored | 0.1425 |
| 17 | `output/evaluations/critic/cross-agent-planner-fix-parity-repaired-baseline-critic-critic-controlled-20260910T041514Z-1f790b0/results.json` | critic | controlled | `missing-evidence-or-budget-exhausted` v1 | 3 | FAILED | — | none | judge_not_run, scored | 0.785–0.835 |
| 18 | `output/evaluations/critic/cross-agent-planner-fix-parity-repaired-confirmation-critic-critic-controlled-20260910T042250Z-1f790b0/results.json` | critic | controlled | `missing-evidence-or-budget-exhausted` v1 | 3 | FAILED | — | none | judge_not_run, scored | 0.8–0.839 |
| 19 | `output/evaluations/fact-checker/cross-agent-planner-fix-parity-baseline-fact-checker-fact-checker-controlled-20260910T003848Z-d6a082c/results.json` | fact_checker | controlled | `verification-search-failure` v1 | 3 | FAILED | — | required_fields_present/no_prohibited_calls, required_fields_present/budgets_respected/no_prohibited_calls, required_fields_present/no_prohibited_calls | judge_not_run, scored | 0.5525 |
| 20 | `output/evaluations/fact-checker/cross-agent-planner-fix-parity-confirmation-fact-checker-fact-checker-controlled-20260910T005356Z-d6a082c/results.json` | fact_checker | controlled | `verification-search-failure` v1 | 3 | FAILED | — | required_fields_present/no_prohibited_calls, required_fields_present/no_prohibited_calls, required_fields_present/no_prohibited_calls | judge_not_run, scored | 0.4525–0.48 |
| 21 | `output/evaluations/fact-checker/cross-agent-planner-fix-parity-judge-native-schema-fact-checker-canary-a503a7b-fact-checker-live-20260911T193525Z-a503a7b/results.json` | fact_checker | live | `fact-checker-live-verification` v1 | 1 | INFRASTRUCTURE FAILURE | — | none | judge_not_run | — |
| 22 | `output/evaluations/fact-checker/cross-agent-planner-fix-parity-live-20260910-fact-checker-fact-checker-live-20260910T191351Z-d697ff6/results.json` | fact_checker | live | `fact-checker-live-verification` v1 | 1 | FAILED | 0.691 | none | scored | 0.485 |
| 23 | `output/evaluations/fact-checker/cross-agent-planner-fix-parity-repaired-baseline-fact-checker-fact-checker-controlled-20260910T034149Z-1f790b0/results.json` | fact_checker | controlled | `verification-search-failure` v1 | 3 | FAILED | — | budgets_respected/no_prohibited_calls, budgets_respected/no_prohibited_calls, budgets_respected/no_prohibited_calls | judge_not_run, scored | 0.53 |
| 24 | `output/evaluations/fact-checker/cross-agent-planner-fix-parity-repaired-confirmation-fact-checker-fact-checker-controlled-20260910T035356Z-1f790b0/results.json` | fact_checker | controlled | `verification-search-failure` v1 | 3 | FAILED | 0.5833 | budgets_respected/no_prohibited_calls, budgets_respected/no_prohibited_calls, budgets_respected/no_prohibited_calls | scored | 0.5215–0.615 |
| 25 | `output/evaluations/live-critic-canary/critic/critic-canary-critic-live-20260912T204455Z-bc3f472/results.json` | critic | live | `critic-live-review` v1 | 1 | REVIEW REQUIRED | 0.7653 | none | scored | 0.7755 |
| 26 | `output/evaluations/live-critic-canary/critic/critic-canary-r2-critic-live-20260912T205014Z-bc3f472/results.json` | critic | live | `critic-live-review` v1 | 1 | REVIEW REQUIRED | 0.8512 | none | scored | 0.752 |
| 27 | `output/evaluations/live-critic-canary/critic/critic-canary-r3-critic-live-20260912T205119Z-bc3f472/results.json` | critic | live | `critic-live-review` v1 | 1 | REVIEW REQUIRED | 0.889 | none | scored | 0.815 |
| 28 | `output/evaluations/live-critic-confirm/critic/critic-confirm-96fe8a9-r1-critic-live-20260911T224609Z-6b2c9c3/results.json` | critic | live | `critic-live-review` v1 | 1 | REVIEW REQUIRED | 0.88 | none | scored | 0.8 |
| 29 | `output/evaluations/live-critic-confirm/critic/critic-confirm-96fe8a9-r2-critic-live-20260911T224752Z-6b2c9c3/results.json` | critic | live | `critic-live-review` v1 | 1 | REVIEW REQUIRED | 0.767 | none | scored | 0.745 |
| 30 | `output/evaluations/live-critic-confirm/critic/critic-confirm-96fe8a9-r3-critic-live-20260911T224859Z-6b2c9c3/results.json` | critic | live | `critic-live-review` v1 | 1 | FAILED | 0.7125 | none | scored | 0.6875 |
| 31 | `output/evaluations/live-critic-d2/critic/critic-d2-r1-critic-live-20260912T212835Z-fceb466/results.json` | critic | live | `critic-live-review` v1 | 1 | FAILED | 0.7095 | none | scored | 0.6825 |
| 32 | `output/evaluations/live-critic-d2/critic/critic-d2-r2-critic-live-20260912T212948Z-fceb466/results.json` | critic | live | `critic-live-review` v1 | 1 | REVIEW REQUIRED | 0.8935 | none | scored | 0.8225 |
| 33 | `output/evaluations/live-critic-d2/critic/critic-d2-r3-critic-live-20260912T213058Z-fceb466/results.json` | critic | live | `critic-live-review` v1 | 1 | FAILED | 0.639 | none | scored | 0.565 |
| 34 | `output/evaluations/live-critic-gated/critic/critic-gated-cccc139-r1-critic-live-20260911T225259Z-cccc139/results.json` | critic | live | `critic-live-review` v1 | 1 | REVIEW REQUIRED | 0.7905 | none | scored | 0.8175 |
| 35 | `output/evaluations/live-critic-gated/critic/critic-gated-cccc139-r2-critic-live-20260911T225435Z-cccc139/results.json` | critic | live | `critic-live-review` v1 | 1 | REVIEW REQUIRED | 0.8159 | none | scored | 0.8265 |
| 36 | `output/evaluations/live-critic-gated/critic/critic-gated-cccc139-r3-critic-live-20260911T225625Z-cccc139/results.json` | critic | live | `critic-live-review` v1 | 1 | REVIEW REQUIRED | 0.85 | none | scored | 0.75 |
| 37 | `output/evaluations/live-critic-readiness/critic/critic-readiness-2fe4e32-critic-live-20260911T220455Z-2fe4e32/results.json` | critic | live | `critic-live-review` v1 | 1 | FAILED | 0.728 | none | scored | 0.68 |
| 38 | `output/evaluations/live-critic-readiness/critic/critic-readiness-750472b-critic-live-20260911T221241Z-750472b/results.json` | critic | live | `critic-live-review` v1 | 1 | INFRASTRUCTURE FAILURE | — | none | judge_not_run | — |
| 39 | `output/evaluations/live-critic-readiness/critic/critic-readiness-7f84378-critic-live-20260911T223606Z-7f84378/results.json` | critic | live | `critic-live-review` v1 | 1 | INFRASTRUCTURE FAILURE | — | none | judge_not_run | — |
| 40 | `output/evaluations/live-critic-readiness/critic/critic-readiness-96fe8a9-critic-live-20260911T223836Z-96fe8a9/results.json` | critic | live | `critic-live-review` v1 | 1 | REVIEW REQUIRED | 0.7725 | none | scored | 0.7875 |
| 41 | `output/evaluations/live-critic-readiness/critic/critic-readiness-fe434cf-critic-live-20260911T212856Z-fe434cf/results.json` | critic | live | `critic-live-review` v1 | 1 | FAILED | 0.6995 | none | scored | 0.6325 |
| 42 | `output/evaluations/live-critic-uniform/critic/critic-uniform-035d3c5-r1-critic-live-20260911T230901Z-035d3c5/results.json` | critic | live | `critic-live-review` v1 | 1 | REVIEW REQUIRED | 0.783 | none | scored | 0.805 |
| 43 | `output/evaluations/live-critic-uniform/critic/critic-uniform-035d3c5-r2-critic-live-20260911T231033Z-035d3c5/results.json` | critic | live | `critic-live-review` v1 | 1 | REVIEW REQUIRED | 0.8245 | none | scored | 0.7075 |
| 44 | `output/evaluations/live-critic-uniform/critic/critic-uniform-035d3c5-r3-critic-live-20260911T231205Z-035d3c5/results.json` | critic | live | `critic-live-review` v1 | 1 | REVIEW REQUIRED | 0.8235 | none | scored | 0.8725 |
| 45 | `output/evaluations/live-critic-uniform/critic/critic-uniform-035d3c5-r4-critic-live-20260911T231359Z-035d3c5/results.json` | critic | live | `critic-live-review` v1 | 1 | FAILED | — | review_produced | judge_not_run | — |
| 46 | `output/evaluations/live-rerun-160c334/critic/cross-agent-planner-fix-parity-live-rerun-160c334-critic-critic-live-20260910T223008Z-160c334/results.json` | critic | live | `critic-live-review` v1 | 1 | FAILED | 0.6555 | none | scored | 0.5925 |
| 47 | `output/evaluations/live-rerun-160c334/fact-checker/cross-agent-planner-fix-parity-live-rerun-160c334-fact-checker-fact-checker-live-20260910T222742Z-160c334/results.json` | fact_checker | live | `fact-checker-live-verification` v1 | 1 | INFRASTRUCTURE FAILURE | — | none | judge_not_run | — |
| 48 | `output/evaluations/live-rerun-160c334/planner/cross-agent-planner-fix-parity-live-rerun-160c334-planner-planner-live-20260910T222425Z-160c334/results.json` | planner | live | `planner-live-scope` v1 | 1 | REVIEW REQUIRED | 0.9271 | none | scored | 0.8785 |
| 49 | `output/evaluations/live-rerun-160c334/researcher/cross-agent-planner-fix-parity-live-rerun-160c334-researcher-researcher-live-20260910T222533Z-160c334/results.json` | researcher | live | `researcher-live-evidence` v1 | 1 | FAILED | — | citations_known/no_invented_sources | judge_not_run | — |
| 50 | `output/evaluations/live-rerun-160c334/source-evaluator/cross-agent-planner-fix-parity-live-rerun-160c334-source-evaluator-source-evaluator-live-20260910T222657Z-160c334/results.json` | source_evaluator | live | `source-evaluator-live-ranking` v1 | 1 | FAILED | 0.797 | low_confidence_flagged | scored | 0.795 |
| 51 | `output/evaluations/live-rerun-160c334/synthesizer/cross-agent-planner-fix-parity-live-rerun-160c334-synthesizer-synthesizer-live-20260910T222902Z-160c334/results.json` | synthesizer | live | `synthesizer-live-report` v1 | 1 | INFRASTRUCTURE FAILURE | — | none | judge_not_run | — |
| 52 | `output/evaluations/live-synthesizer-2dfa099/synthesizer/cross-agent-planner-fix-parity-live-synthesizer-2dfa099-synthesizer-live-20260911T204304Z-2dfa099/results.json` | synthesizer | live | `synthesizer-live-report` v1 | 1 | REVIEW REQUIRED | 0.81 | none | scored | 0.85 |
| 53 | `output/evaluations/planner/cross-agent-planner-fix-parity-live-20260910-planner-planner-live-20260910T191110Z-d697ff6/results.json` | planner | live | `planner-live-scope` v1 | 1 | REVIEW REQUIRED | 0.8725 | none | scored | 0.7875 |
| 54 | `output/evaluations/planner/planner-controlled-20260816T101500Z-abc1234/results.json` | planner | controlled | — | — | INFRASTRUCTURE FAILURE | — | — | — | — |
| 55 | `output/evaluations/planner/planner-live-20260816T101500Z-abc1234/results.json` | planner | live | `planner-live-scope` v1 | 1 | FAILED | 0.91 | trace_available | scored | 0.85 |
| 56 | `output/evaluations/production-readiness-v2-planner-r1/planner/prod-readiness-v2-planner-r1-planner-live-20260913T182911Z-16d2a8f/results.json` | planner | live | `planner-live-scope` v1 | 1 | REVIEW REQUIRED | 0.877 | none | scored | 0.795 |
| 57 | `output/evaluations/researcher/cross-agent-planner-fix-parity-baseline-researcher-researcher-controlled-20260909T234501Z-d6a082c/results.json` | researcher | controlled | `partial-search-failure` v1 | 3 | FAILED | — | no_prohibited_calls/sub_topic_covered, budgets_respected/no_prohibited_calls, budgets_respected/no_prohibited_calls | judge_not_run | — |
| 58 | `output/evaluations/researcher/cross-agent-planner-fix-parity-confirmation-researcher-researcher-controlled-20260910T000647Z-d6a082c/results.json` | researcher | controlled | `partial-search-failure` v1 | 3 | FAILED | — | budgets_respected/no_prohibited_calls, budgets_respected/no_prohibited_calls, budgets_respected/no_prohibited_calls | judge_not_run | — |
| 59 | `output/evaluations/researcher/cross-agent-planner-fix-parity-judge-native-schema-researcher-canary-c13dd9d-researcher-live-20260911T185526Z-c13dd9d/results.json` | researcher | live | `researcher-live-evidence` v1 | 1 | REVIEW REQUIRED | 0.8095 | none | scored | 0.6825 |
| 60 | `output/evaluations/researcher/cross-agent-planner-fix-parity-live-20260910-researcher-researcher-live-20260910T191215Z-d697ff6/results.json` | researcher | live | `researcher-live-evidence` v1 | 1 | FAILED | 0.6469 | citations_known/no_invented_sources | scored | 0.6115 |
| 61 | `output/evaluations/researcher/cross-agent-planner-fix-parity-repaired-baseline-researcher-researcher-controlled-20260910T031409Z-1f790b0/results.json` | researcher | controlled | `partial-search-failure` v1 | 3 | FAILED | — | budgets_respected/no_prohibited_calls, budgets_respected/no_prohibited_calls, budgets_respected/no_prohibited_calls | judge_not_run, scored | 0.7575 |
| 62 | `output/evaluations/researcher/cross-agent-planner-fix-parity-repaired-confirmation-researcher-researcher-controlled-20260910T032225Z-1f790b0/results.json` | researcher | controlled | `partial-search-failure` v1 | 3 | FAILED | — | budgets_respected/no_prohibited_calls, budgets_respected/no_prohibited_calls, budgets_respected/no_prohibited_calls | judge_not_run, scored | 0.2975 |
| 63 | `output/evaluations/sequential-live-20260910/researcher/cross-agent-planner-fix-parity-sequential-live-20260910-researcher-researcher-live-20260910T232024Z-c368849/results.json` | researcher | live | `researcher-live-evidence` v1 | 1 | FAILED | — | citations_known/no_invented_sources | judge_not_run | — |
| 64 | `output/evaluations/source-evaluator/cross-agent-planner-fix-parity-baseline-source-evaluator-source-evaluator-controlled-20260910T002727Z-d6a082c/results.json` | source_evaluator | controlled | `reputation-provider-failure` v1 | 3 | FAILED | — | required_fields_present, required_fields_present, required_fields_present | judge_not_run, scored | 0.895 |
| 65 | `output/evaluations/source-evaluator/cross-agent-planner-fix-parity-confirmation-source-evaluator-source-evaluator-controlled-20260910T003442Z-d6a082c/results.json` | source_evaluator | controlled | `reputation-provider-failure` v1 | 3 | FAILED | — | required_fields_present, required_fields_present, required_fields_present | judge_not_run | — |
| 66 | `output/evaluations/source-evaluator/cross-agent-planner-fix-parity-judge-native-schema-source-evaluator-canary-d902c57-source-evaluator-live-20260911T190947Z-d902c57/results.json` | source_evaluator | live | `source-evaluator-live-ranking` v2 | 1 | REVIEW REQUIRED | 0.928 | none | scored | 0.88 |
| 67 | `output/evaluations/source-evaluator/cross-agent-planner-fix-parity-live-20260910-source-evaluator-source-evaluator-live-20260910T191308Z-d697ff6/results.json` | source_evaluator | live | `source-evaluator-live-ranking` v1 | 1 | FAILED | — | low_confidence_flagged | judge_not_run | — |
| 68 | `output/evaluations/source-evaluator/cross-agent-planner-fix-parity-repaired-baseline-source-evaluator-source-evaluator-controlled-20260910T033036Z-1f790b0/results.json` | source_evaluator | controlled | `reputation-provider-failure` v1 | 3 | FAILED | — | none | judge_not_run | — |
| 69 | `output/evaluations/source-evaluator/cross-agent-planner-fix-parity-repaired-confirmation-source-evaluator-source-evaluator-controlled-20260910T033713Z-1f790b0/results.json` | source_evaluator | controlled | `reputation-provider-failure` v1 | 3 | FAILED | — | none | judge_not_run, scored | 0.902 |
| 70 | `output/evaluations/synthesizer/cross-agent-planner-fix-parity-baseline-synthesizer-synthesizer-controlled-20260910T010651Z-d6a082c/results.json` | synthesizer | controlled | `write-or-memory-failure` v1 | 3 | FAILED | 0.9496 | required_fields_present, required_fields_present, required_fields_present | scored | 0.9105–0.92 |
| 71 | `output/evaluations/synthesizer/cross-agent-planner-fix-parity-confirmation-synthesizer-synthesizer-controlled-20260910T011515Z-d6a082c/results.json` | synthesizer | controlled | `write-or-memory-failure` v1 | 3 | FAILED | — | required_fields_present, required_fields_present, required_fields_present | judge_not_run, scored | 0.9165 |
| 72 | `output/evaluations/synthesizer/cross-agent-planner-fix-parity-judge-native-schema-synthesizer-canary-e1b8ae5-synthesizer-live-20260911T191351Z-e1b8ae5/results.json` | synthesizer | live | `synthesizer-live-report` v1 | 1 | REVIEW REQUIRED | 0.804 | none | scored | 0.84 |
| 73 | `output/evaluations/synthesizer/cross-agent-planner-fix-parity-live-20260910-synthesizer-synthesizer-live-20260910T191500Z-d697ff6/results.json` | synthesizer | live | `synthesizer-live-report` v1 | 1 | INFRASTRUCTURE FAILURE | — | none | judge_not_run | — |
| 74 | `output/evaluations/synthesizer/cross-agent-planner-fix-parity-repaired-baseline-synthesizer-synthesizer-controlled-20260910T040509Z-1f790b0/results.json` | synthesizer | controlled | `write-or-memory-failure` v1 | 3 | FAILED | — | none | judge_not_run, scored | 0.925–0.939 |
| 75 | `output/evaluations/synthesizer/cross-agent-planner-fix-parity-repaired-confirmation-synthesizer-synthesizer-controlled-20260910T041019Z-1f790b0/results.json` | synthesizer | controlled | `write-or-memory-failure` v1 | 3 | FAILED | — | none | judge_not_run, scored | 0.929 |
| 76 | `output/evaluations/task19-fact-checker-ef143ef/fact-checker/task19-fact-checker-ef143ef-fact-checker-live-20260911T013600Z-ef143ef/results.json` | fact_checker | live | `fact-checker-live-verification` v1 | 1 | FAILED | 0.691 | none | scored | 0.485 |
| 77 | `output/evaluations/task19-source-evaluator-ce3a706/source-evaluator/task19-source-evaluator-ce3a706-source-evaluator-live-20260911T012237Z-ce3a706/results.json` | source_evaluator | live | `source-evaluator-live-ranking` v1 | 1 | FAILED | — | low_confidence_flagged | judge_not_run | — |
| 78 | `output/evaluations/task19-source-evaluator-confirmation-424ed6c/source-evaluator/task19-source-evaluator-confirmation-424ed6c-source-evaluator-live-20260911T013412Z-424ed6c/results.json` | source_evaluator | live | `source-evaluator-live-ranking` v1 | 1 | FAILED | 0.782 | low_confidence_flagged | scored | 0.77 |
| 79 | `output/evaluations/task19-source-evaluator-readiness-v2-3eab969/source-evaluator/task19-source-evaluator-readiness-v2-3eab969-source-evaluator-source-evaluator-live-20260911T024444Z-3eab969/results.json` | source_evaluator | live | `source-evaluator-live-ranking` v2 | 1 | INFRASTRUCTURE FAILURE | — | none | judge_not_run | — |
| 80 | `output/evaluations/task20-researcher-confirmation-9319024/researcher/task20-researcher-confirmation-9319024-researcher-live-20260911T011711Z-9319024/results.json` | researcher | live | `researcher-live-evidence` v1 | 1 | INFRASTRUCTURE FAILURE | — | none | judge_not_run | — |
| 81 | `output/evaluations/task21-fact-checker-readiness-30d9921/fact-checker/task21-fact-checker-readiness-30d9921-fact-checker-fact-checker-live-20260911T030226Z-30d9921/results.json` | fact_checker | live | `fact-checker-live-verification` v1 | 1 | FAILED | 0.6205 | none | scored | 0.3675 |

### 6.4 Test-harness fixtures inside the evidence directory (**L**)

Two records are **not** evaluations:

| Path | Evidence |
| --- | --- |
| `output/evaluations/planner/planner-live-20260816T101500Z-abc1234/results.json` | `metadata.git_commit = "abc1234def"` (a 10-character placeholder, not a real SHA); judge rationale is literally `"Scripted judge verdict for the runner harness tests."`; `git_dirty=False` |
| `output/evaluations/planner/planner-controlled-20260816T101500Z-abc1234/results.json` | same placeholder commit; top-level error `{"stage": "trace", "reason": "langsmith_unavailable", "message": "langsmith is unreachable"}`; zero cases |

An identical pair of directories exists in **this** worktree under
`output/evaluations/planner/` — written by the offline suite, which resolves its
output directory relative to the working tree. These four directories are test
artifacts.

**Baseline hygiene finding.** The offline suite writes `results.json` into the
same `output/evaluations/<agent>/` namespace that holds historical live
evaluations, under names that look like real experiments. A reader inventorying
that directory cannot separate evaluation evidence from a test run without
opening each file and checking the commit SHA. The plan names no owner for this;
Task 12 (offline adversarial matrix) is the nearest owner.

### 6.5 Substantive reviewer findings (**L**, from judge verdicts)

Per the brief, **REVIEW REQUIRED is not release approval, and an infrastructure
failure is not an agent-quality success.** The following are the judge's own
words, condensed, with the numeric scores the file records.

**`eval2/fact-checker/pr2-fact-check-r3-…-16d2a8f` — status `FAILED`, avg 0.8260,
judge 0.81.** `budget_respected = 0.0`; failed gate `budgets_respected`:
"tool_calls 11 exceed 10". Judge: all four other deterministic gates pass and
claim fidelity is strong; "both claims stopped on `max_iterations` rather than
convergence, so verdicts were cap-terminated"; "`source_urls` record only the two
seed domains … while the independent corroborating domains appear only in prose,
leaving the output non-navigable to its third-party sources". A near-passing
score with a real budget defect — this is why a high average does not close a
defect.

**`evaluations/task21-fact-checker-readiness-30d9921` — status `FAILED`, avg
0.6205, judge 0.3675.** `react_stop_reason = provider_error`, fallback
`{"kind": "output_limit", "operation": "react_decision"}`. All four deterministic
gates pass (`evidence_linked`, `independence_enforced`, `confidence_calibrated`,
`budget_respected` all 1.0). Judge: "The run terminated on a non-recoverable
provider output-limit error at iteration 5, so the fact-check was never
completed"; the one reported verdict is `insufficient_evidence` with confidence
0.0 labelled reason `loop_failed`, "i.e. an artifact of the abort rather than a
reasoned conclusion"; "several [gates] pass **vacuously** (`evidence_linked`/
`independence_enforced` only because no claim was marked verified)"; net "a
partially executed, aborted verification that gives the user no answer".
This is the canonical example of both rules: green gates are not quality, and a
provider failure is not a quality success.

**`evaluations/production-readiness-v2-planner-r1` — status `REVIEW REQUIRED`,
avg 0.877, judge 0.795.** All five deterministic gates pass. Judge: "the question
asks for *current* constraints and no subtopic or query anchors recency (no date
terms, no 'latest/2025-26 announcements' framing)"; "every premise is prior-driven
domain reasoning with nothing in the run supporting it"; calibration is weakest —
"the plan never signals that memory recall yielded nothing". Directly supports
TR-04 (stale anchors) and Task 2.

**`evaluations/live-synthesizer-2dfa099` — status `REVIEW REQUIRED`, avg 0.81,
judge 0.85.** `deterministic_metrics.coverage = 0.0` yet the record passes.
Judge: the report "repeatedly asserts that the DOE finding 'is truncated
mid-sentence at its operating-cost clause' … the recorded DOE finding reads as
complete and explicitly includes the operating-cost comparison"; "That truncation
claim is not supported by the evidence shown". Inputs the plan's §1 entry almost
verbatim: invented "truncated finding" limitation plus a dropped available detail.
Directly supports TR-08 and Tasks 7/10/11.

**`evaluations/live-critic-d2/critic-d2-r3-…-fceb466` — status `FAILED`, avg
0.639, judge 0.565.** `deterministic_metrics.no_spurious_gaps = 0.0`. Judge: two
of four gaps rest on "readily available spot-check data" that "no shown tool call
returned"; "recommended query 2 even asks for cost data gap 2 claims to already
have"; "The rationale is truncated mid-word"; `unsupported_claims` is empty
despite the critique calling a claim unevidenced; "the score of 5 with a continue
sits below the reference's 7/end". Supports TR-09 and Tasks 8/10.

**`evaluations/live-critic-d2/critic-d2-r1-…-fceb466` — status `FAILED`, avg
0.7095, judge 0.6825.** `fallback_provider_diagnostic` records
`{"kind": "schema_output", "operation": "react_decision"}` with two attempts, both
`json_invalid` at field path `$`. Judge: "tool_calls is 0, so the live
tavily/memory verification the live tier requires never happened". Supports
TR-09's "record categorical field errors without inventing historical causes".

**`evaluations/critic/…-c74a4f2-…` and
`evaluations/task19-source-evaluator-readiness-v2-3eab969` — status
`INFRASTRUCTURE FAILURE`.** Both have `judge_status = judge_not_run`,
`not_run_reason = judge_schema_failure`, and `average_quality = None`. Diagnostics:
`string_bounds` on field `rationale` plus `extra_forbidden` at `$` (critic);
`extra_forbidden` at `$` plus `string_bounds` on `rationale` (source evaluator).
Neither record carries a quality result, and neither may be cited as one.

**`evaluations/live-rerun-160c334/researcher/…` — status `FAILED`, avg `None`,
`judge_not_run` (`judge_output_limit`).** Two hard gates failed:
`citations_known` — "unknown source urls: https://afsethmillar.co.uk/…,
https://bonnenbatteries.com/…, https://ffb.fraunhofer.de/…"; and
`no_invented_sources` — "a finding cites a url outside the known sources".
`deterministic_metrics.sources_are_real_urls = 0.0`. This is the only record in
the inventory whose citations fail hard, and it is evidence for Tasks 1/5/6
rather than a reason to relax the gate.

### 6.6 Status and gate rollup (**L**)

Record status by tier (81 records):

| Tier | FAILED | INFRASTRUCTURE FAILURE | REVIEW REQUIRED |
| --- | ---: | ---: | ---: |
| controlled | 20 | 1 | 0 |
| live | 21 | 9 | 30 |

Record status by agent (81 records):

| Agent | FAILED | INFRASTRUCTURE FAILURE | REVIEW REQUIRED | controlled / live |
| --- | ---: | ---: | ---: | --- |
| planner | 1 | 1 | 5 | 1 / 6 |
| researcher | 7 | 1 | 4 | 4 / 8 |
| source_evaluator | 8 | 1 | 4 | 4 / 9 |
| fact_checker | 8 | 2 | 2 | 4 / 8 |
| synthesizer | 4 | 2 | 2 | 4 / 4 |
| critic | 13 | 3 | 13 | 4 / 25 |

Failed gate instances across the 240 repetitions (**L**), split by tier:

| Gate id | Failed repetitions | Records containing it | controlled | live |
| --- | ---: | ---: | ---: | ---: |
| `no_prohibited_calls` | 88 | 12 | 88 | 0 |
| `required_fields_present` | 72 | 8 | 72 | 0 |
| `budgets_respected` | 33 | 9 | 32 | 1 |
| `sub_topic_covered` | 15 | 4 | 15 | 0 |
| `route_consistent` | 5 | 2 | 5 | 0 |
| `low_confidence_flagged` | 4 | 4 | 0 | 4 |
| `citations_known` | 3 | 3 | 0 | 3 |
| `no_invented_sources` | 3 | 3 | 0 | 3 |
| `review_produced` | 1 | 1 | 0 | 1 |
| `trace_available` | 1 | 1 | 0 | 1 |

Two facts about this distribution matter for later tasks:

- The two most frequent failures, `no_prohibited_calls` (88) and
  `required_fields_present` (72), occur **only** in the controlled tier, where
  the case deliberately withholds a dependency. They describe the harness's
  negative-control behaviour, not live agent quality. `sub_topic_covered` (15)
  and `route_consistent` (5) are controlled-only for the same reason.
- In the **live** tier the failures are `low_confidence_flagged` (4),
  `citations_known` (3), `no_invented_sources` (3) and one instance each of
  `budgets_respected`, `review_produced`, and `trace_available`. `budgets_respected`
  is therefore almost entirely a controlled-tier signal, but its single live
  instance is the near-passing run described in §6.5 (`pr2-fact-check-r3`,
  "tool_calls 11 exceed 10"), which is why D-13 is a defect rather than noise.

Judge availability across the same 240 repetitions (**L**): `scored` 124,
`judge_not_run` 116. The 116 non-results split exactly in half:
`judge_schema_failure` 58 and `judge_output_limit` 58. **Nearly half of all
recorded repetitions carry no quality judgment at all**, and 9 of the 81 records
are `INFRASTRUCTURE FAILURE` with `average_quality = None`. Any aggregate over
this directory that ignores judge status averages over a population that is
mostly unjudged.

## 7. F1 payload comparison for the three repeated live atoms

The brief requires, for three repeated live atoms — **queue totals**, **report
cutoff**, and **PJM cycle** — "the cheaper F1 payload comparison where raw data
exists", recording "exact first missing boundary or `not_diagnosable`", and
forbids inventing B from two similar URLs.

### 7.1 What "raw data" exists here (**L**)

| Candidate payload store | Present? |
| --- | --- |
| Fetched document/page bodies (HTML, PDF, extracted text) | **No.** No such file exists anywhere under either `output/` tree. |
| Per-claim extraction packets / drafted findings pre-prefix | **No.** |
| Full LLM request messages and returned drafts | **No** (trace review, "Unavailable in the queried trace"). |
| Session state snapshot of the historical run | **No.** |
| The rendered evidence ledger | **Yes** — but passages are **prefix-capped at 200 characters**; 6 of the 15 passage rows end in `...`. |
| The reader report | **Yes** — prose only, no payload. |

Measured passage-cell length across all 15 rows (**L**): min 82, max **200**
characters; exact lengths in row order
`200, 200, 132, 200, 200, 151, 200, 189, 148, 160, 82, 186, 186, 135, 200`.

**The passage/anchor boundary, measured (**L**).** The ledger records a
verification passage for exactly **8** of the 16 claims — #1, #2, #6, #8, #9, #10,
#11, #15. Those 8 are exactly the 5 `verified` + 3 `unverified` claims. The other
**8** claims — #3, #4, #5, #7, #12, #13, #14, #16 — have **zero** recorded
passage rows, and all 8 are `insufficient_evidence` with confidence **0.00**. In
other words, for this run the ledger never records an excerpt for a claim it
failed to support.

The three atoms therefore split cleanly: **queue totals** has recorded excerpts;
**report cutoff** and **PJM cycle** have none.

### 7.2 Atom 1 — queue totals (claims #1, #11, #15)

Recorded claim text (**L**):
- #1 and #11 (identical text, different IDs): "The total capacity of energy
  projects in U.S. interconnection queues grew 40% year-over-year in 2022, with
  more than 1,350 GW of generation and 680 GW of storage waiting for approval to
  connect." Verdicts verified 0.78 / 0.70. Registry source column: the
  `utilitydive.com` article.
- #15: "As of the end of 2023, 2,600 GW of proposed U.S. generation and storage
  projects were waiting for grid access, with typical wait times stretching to
  five years or more." Verified 0.90. Registry source column: the `novoco.com`
  page.

Recorded excerpts (**L**, verbatim, `…` marks the ledger's own ellipsis):
- #1 → `emp.lbl.gov/.../queued_up_2022_04-06-2023.pdf`, "p. 3, High-Level
  Findings": "Over 10,000 projects representing 1,350 gigawatts (GW) of generator
  capacity and 680 GW of storage actively seeking interconnection … Data collected
  from interconnection queues … Includes proje…" (200 chars).
- #1 → `rtoinsider.com/31969-…`, "article headline and standfirst (Apr 7, 2023,
  James Downing)": "LBNL: Interconnection Queues Grew 40% in 2022 … LBNL's annual
  report on interconnection queues showed continued growth in capacity waiting to
  connect to the grid, even as 2 markets restricted new…" (200 chars).
- #15 → `emp.lbl.gov/.../Queued%20Up%202024%20Edition.pdf`, "p. 3, High-Level
  Findings": "Nearly 12,000 projects representing 1,570 gigawatts (GW) of
  generator capacity and 1,030 GW of storage actively seeking interconnection"
  (135 chars).
- #15 → same PDF, "p. 3, High-Level Findings (Completion rates are generally low;
  wait times are increasing)": "The average time projects spent in queues before
  being built has increased markedly. The typical project built in 2023 took
  nearly 5 years from the interconnection request to commercial operations,…"
  (186 chars).

**Comparable without the missing bytes (L + I):**
- The claim's `2,600 GW` equals exactly the sum recorded in its own LBNL excerpt:
  `1,570 GW` generator capacity `+ 1,030 GW` storage `= 2,600 GW`. The claim
  presents that sum as a single "2,600 GW … waiting for grid access" figure while
  the registry attributes it to `novoco.com`, a page the trace review describes as
  relaying LBNL second-hand. This is a **compound-total** observation, derived
  from arithmetic on one recorded excerpt — not an inference from two similar
  URLs.
- The `40%` component of #1/#11 is supported in the ledger only by an excerpt
  whose locator is *"article headline and standfirst"*, and only by the
  secondary trade-press item. The primary LBNL excerpt recorded for the same
  claim contains the `1,350 GW` / `680 GW` figures but not `40%`.
- Neither recorded excerpt shows what lies beyond the 200-character render cap.

**Exact first missing boundary (F).** The comparison stops at the ledger's
200-character passage cells: for #1 that is character 200 of the
`emp.lbl.gov` excerpt and character 200 of the `rtoinsider.com` excerpt; for #15
it is character 135 / 186 respectively. Behind that, the fetched bytes —
the Queued Up 2022 PDF, the Queued Up 2024 Edition PDF, the RTO Insider article,
and the Utility Dive and Novoco pages — are **not retained anywhere on disk**.
The claim-vs-excerpt comparison is therefore **diagnosable**; the
excerpt-vs-original-bytes comparison is **`not_diagnosable`**.

**Third causal class (per TR-06).** The trace review adds class (c) to the F1
question — "supposed upstream read evidence may be generated memory rather than a
fresh page" — and records that neither Novoco nor Utility Dive was opened by
`web_scraper` anywhere in this trace (**T**). Locally, the memory store contains
the Utility Dive 40%-growth text and the Novoco 2,600 GW text **verbatim**, three
copies each (§2.5, **L**). Class (c) is therefore *admissible and positively
indicated for this atom's publisher attribution*; exact per-claim historical
attribution still cannot be proved from the retained artifacts (**F**). It is
recorded as an admissible cause, not as a proven one.

### 7.3 Atom 2 — report cutoff (claims #4, #12, #14)

All three are `insufficient_evidence`, confidence 0.00, registry source the LBNL
2025 PDF `eta-publications.lbl.gov/.../queued_up_2025_edition_12.15.2025.pdf`,
and **none has any recorded verification passage** (**L**). Claims #12 and #14
carry the reason `no_independent_source`; #4 carries `—`.

The only recorded excerpts touching this proposition belong to **claim #2**
(`unverified`, 0.35), from the same PDF (**L**):
- "p. 1 (cover page)", 132 chars: "Queued Up: 2025 Edition Characteristics of
  Power Plants Seeking Transmission Interconnection As of the End of 2024 …
  December 2025".
- "p. 2 (About This Report)", 200 chars: "The 2025 edition of the report
  summarizes interconnection queue data through the end of 2024. Therefore, any
  updates to the data and trends that occurred since January 2025 would not be
  represented…".

**Result: `not_diagnosable` at the claim level.** The exact first missing
boundary is the claim-to-passage link itself: the ledger records no excerpt
attributed to #4, #12, or #14, so no B exists to compare against any A. The
upstream PDF bytes are additionally absent. What *can* be said (**I**) is that a
200-character excerpt carrying the cutoff proposition does survive in the ledger —
attached to a different claim that the same run marked `unverified`. Whether the
three cutoff claims were derived from that same page, from memory, or from a
prefix of it cannot be determined.

### 7.4 Atom 3 — PJM cycle (claims #3, #16)

Both are `insufficient_evidence`, confidence 0.00, registry source
`rmi.org/resources/pjms-speed-to-power-problem-and-how-to-fix-it` — a **single**
publisher — and **neither has any recorded verification passage** (**L**). #3
carries the reason `no_independent_source`; #16 carries `—`. The ledger's source
assessment records RMI as one scored source at overall 0.68.

**Result: `not_diagnosable`.** The exact first missing boundary is again the
claim-to-passage link: there is no recorded excerpt for either claim, and the
RMI page bytes are not retained. No comparison between an upstream payload and a
retained excerpt is possible at all.

### 7.5 F1 summary

| Atom | Claims | Recorded excerpt exists? | A-vs-B comparison | Exact first missing boundary | Class |
| --- | --- | --- | --- | --- | --- |
| Queue totals | #1, #11, #15 | yes (4 rows) | **possible** | ledger passage cell caps at 200 chars; fetched PDF/article/page bytes absent | claim↔excerpt **L/I**; excerpt↔bytes **F** |
| Report cutoff | #4, #12, #14 | **no** | **impossible** | no claim→passage row; upstream PDF bytes absent | **F** (`not_diagnosable`) |
| PJM cycle | #3, #16 | **no** | **impossible** | no claim→passage row; upstream page bytes absent | **F** (`not_diagnosable`) |

The requirement that these be recorded per-atom rather than summed is respected:
two of the three atoms are `not_diagnosable`, and the third is only partly
diagnosable. No cause is inferred from URL counts, from passage lengths, or from
two similar URLs.

## 8. Trace review TR-01–TR-09, incorporated by reference ID

Reference IDs are the trace review's own. "Code reproduction" means the review
executed code and observed output; "historical inference" means a statement about
what happened in the run that the retained payloads cannot confirm. The two are
kept apart exactly as the brief requires.

| ID | Finding (condensed) | Evidence class | Code anchor at HEAD | Owner tasks |
| --- | --- | --- | --- | --- |
| **TR-01** | Memory is wrongly admitted as source evidence. `_read_payload_urls` treats a `query_memory` match with non-empty content and a `source_url` as a *read*; that result feeds Researcher URL admission, `render_evidence(discovery_payloads=False)`, Fact Checker `retrieved_source_urls`, and `independent_domains`. | **Code reproduction** (offline, in-memory; output quoted in the review) + **L** anchors (§1.2) + **L** memory store (§2.5). Exact historical memory-to-draft attribution remains **F**. | `agents/steps.py:223`, `:288`; `runtime/memory_bridge.py` | 1, 2, 3, 6, 12, 13 |
| **TR-02** | Decision feedback is a 200-character payload prefix; extraction separately uses 4,000-character prefixes, so the next native decision request is rebuilt from notes that cannot inventory unvisited leads or already-read passages. | **L** (config default 200; `DEFAULT_EVIDENCE_CHARS`/`FACT_CHECK_EVIDENCE_CHARS` = 4000; the four `summary_limit` call sites) + **T** (repetition in the trace). What each omitted token contained is **F**. | `utils/config.py:213`; `agents/base.py:284`; `agents/researcher.py:72,1133`; `agents/fact_checker.py:81,1385`; `agents/critic.py:1023` | 3, 6 |
| **TR-03** | Successful document access is spent on repetition: the same 2025 LBNL PDF was read 7× (Researcher) and 14× (Fact Checker) = **21 of 45** document calls; USGS 217-chunk and CAISO 36-chunk reads emitted no findings; Researcher produced zero findings in **11 of 20** subtopic passes and in all four wholesale passes despite 40 tool calls. | **T** counts; **I** for the selection/extraction interpretation. Equal chunk counts do **not** establish identical byte hashes (**F**). | — | 3, 5, 6, 9 |
| **TR-04** | Planner supplies stale anchors — criteria locked to 2024, 2023–2024, 2024–2025 for a September 2026 session — and invents numeric agreement tolerances (10%, 5 pp, 15%, 3 pp). All ten memory calls ran before the plan; its five decision turns discovered no new public evidence. | **T**, with **L** corroboration in the `production-readiness-v2-planner-r1` judge verdict ("no subtopic or query anchors recency"). | — | 2 |
| **TR-05** | Source Evaluator is **not** primarily blocked by a source-count cap: four passes reported 7 → 9 → 10 → 10 assessed sources with zero cap/missing/provider-unscored counts, and the last pass reused assessments with no new LLM request. What it lacks is downstream enforcement of derivative origin, observation date, and source fitness. | **T** + **L** (the latest ledger's own source assessment describes Utility Dive/Novoco as second-hand relaying, Moss Landing as a self-interested company source, and the EIA observations as old — matching the review's description). | — | 4, with 6–7 as consumers |
| **TR-06** | Fact Checker spent 100 ReAct turns over 20 verification jobs, nearly exhausting ten calls each time, and retained 16 canonical claims; all four extraction batches produced exactly five accepted claims. Job verdicts were 6 verified / 5 unverified / 9 insufficient, which are **not** the final canonical 5/3/8. Four jobs had no new independent publisher and bypassed adjudication. Two structured responses failed validation and were retried, both with `finish_reason_category=stop`. | **T**. The review explicitly warns not to diagnose the retries as token truncation or provider outages. | `agents/fact_checker.py` (`valid_verification_passages`, `claim_verification_messages`, `verify_claim`, `DEFAULT_MAX_CLAIMS`) | 1, 5, 6 |
| **TR-07** | `_refinement_satisfied_sub_topics` skips a topic when it has **any** prior raw finding and the Critic did not name that topic as a gap. The log records topic-04 skipped in pass 1, topic-02 skipped in passes 2 and 3, topic-05 skipped in pass 3. | **L**: the latest ledger's own `Run errors` rows 36, 56, 71, 72 carry `researcher_sub_topic_skipped` with `reason=interim_satisfaction` for `topic-04`, `topic-02`, `topic-02`, `topic-05`. **L** anchor §1.2. | `agents/researcher.py:209`, `:1146` | 9, 10, 11 |
| **TR-08** | Synthesis exposes rather than repairs a weak evidence selection: all four synthesis calls returned without provider failure; claim counts grew 5 → 10 → 13 → 16 while section count stalled at four; the reader has eight references, not six; every ranked mechanism says "not stated"; C001/C011 leak; a figure absent from checked claims ("890 GW") appears in prose that says it is not being reported; the scope/methodology assert stronger statement linkage and non-repetition than the prose demonstrates. | **L** — every item re-measured directly in §5 against `report-417fa933…-3.md`. | — | 7, 10, 11 |
| **TR-09** | Critic is an inefficient reviewer, but the low score is not itself the defect. Scores 4 → 4 → 4 → 5 with gaps 5 → 5 → 4 → 6; every pass used ten search/memory calls and it cannot open a page. One `CritiqueDraft` schema error recovered; the exact malformed fields and full gap wording are **absent from the trace**. | **L** (scores 4,4,4,5 in the four canary logs; gap counts **T**) + **T** (schema recovery). Missing payloads are **F** and "must not be diagnosed as a particular prompt error". | — | 8, 10 |

Two findings the review records as **strengths to preserve**, with local support:
the Source Evaluator reused assessments across passes — four passes reporting
7 → 9 → 10 → 10 assessed sources with the last pass making no new LLM request
(**T**) — and the Synthesizer correctly refused to call 2035 projections current
costs, which the latest report repeats verbatim: "the NREL cost figures available
here are 2035 projections for 4-hour lithium-ion systems and were carried as
insufficient evidence, not as settled current costs" (**L**).

**What the review does not claim**, preserved here because later tasks must not
strengthen it: it does not reconstruct private reasoning, does not identify the
exact malformed schema field, does not prove an unseen A+B pair existed, and does
not independently establish the external truth of any grid-storage fact.

**Cold/warm state.** The last run was **warm-memory**: the store in §2.5 predates
it and its Planner issued ten memory queries that all returned results. A
cold-memory repetition is Task 13's, and is declared separately from a
warm-memory challenge. Local cold/warm attribution beyond the store's existence
and session-of-origin is **F**.

## 9. Deficiencies named in plan §1 and current source/prose weaknesses

These are **recorded, not independently re-verified here**. None of the external
facts in this section have been rechecked against the outside world, and nothing
in this section asserts that any cited source is correct.

### 9.1 Code-path deficiencies named in plan §1 (**recorded from the plan**)

1. Ordinary recalled text plus a URL is admitted as a source read, extraction
   evidence, and a candidate independent publisher (TR-01).
2. Next-action feedback is a 200-character payload prefix; extraction uses
   4,000-character prefixes (TR-02).
3. Extraction relies on bounded payload prefixes and URL admission, not exact
   excerpt validation; retention does not reserve each target's support.
4. A default five-claim prefix (`DEFAULT_MAX_CLAIMS`) can starve later topics.
5. `compute_report_quality` can credit a topic from a linked claim without a
   substantive answer.
6. Controlled end-to-end tests replace graph agents, so they test graph behavior,
   not cooperation of the six real agents.
7. `judge_whole_report` grades "readability" as a length band and "actionability"
   as a keyword; judge input clips the report at 16,000 characters.
8. Upstream URLs are admitted to verification, but upstream excerpts are absent
   from the adjudication prompt, and a new-independent-retrieval early return
   remains.
9. Evaluation and production can resolve different reasoning overrides
   (**confirmed locally in §6.2**).

### 9.2 Cross-run weaknesses named in plan §1 (**recorded from the plan**)

| Run | Recorded weakness | Local re-measurement |
| --- | --- | --- |
| resource `d8894023` | Critic 4/10 and reported 6/6 coverage, yet the reader says technical/system-integration constraints are absent | report word count 2,332 (**L**); coverage claim **T** |
| publisher retention `97921fe4` | Critic 4/10, 5/6 coverage; project cost/revenue/financing remain without checked claims | word count 1,746 (**L**) |
| corroboration criterion `480fd561` | Critic 4/10, reported 6/6 coverage; no checked safety/performance risk claim and no supported supply-chain-to-deployment mechanism | word count 1,938 (**L**) |
| evidence pooling `417fa933` | CAISO market-revenue and EIA efficiency sources are scored but uncited while the reader declares those areas unaddressed | **L**: both appear under "Reviewed but not cited" in the ledger and neither appears in the report's 8 references |
| legacy `854cddd3`, iterations 0–3 | Reader length grows ≈ 3,261 → 6,291 → 10,377 → 15,567 words | **L**, exact: **3,261 / 6,291 / 10,377 / 15,567** |
| legacy `0a9f7331-0` | — | word count **3,656** (**L**) |

### 9.3 Current source/prose weaknesses in the latest report (**L**)

Measured directly in §5: all five mechanism cells `not stated`; C001/C011 leaked
into reader prose; "890 GW" present in a sentence that says it is not reported;
the "no limitation is stated twice" claim contradicted by the visible repetition;
wholesale-market (topic-03) and technical-performance (topic-06) questions
unanswered; current constraints resting on 2022-vintage queue figures from a
source scored 0.52 and on a 2035 cost projection carried as insufficient.

## 10. Defect-to-task matrix

Owner tasks are the trace review's own assignments where the finding has a TR id,
and the plan's own repairs otherwise. "Decisive test" names the acceptance
evidence, not a promise.

| Defect | Statement | Class | Owner | Decisive test / acceptance |
| --- | --- | --- | --- | --- |
| D-01 (TR-01) | Recalled memory with a URL is admitted as a read, as extraction evidence, and as a candidate independent publisher | Code reproduction | 1, 2, 3, 6, 12, 13 | `query_memory` never yields read URLs; cache admission requires validated read artifact/version provenance; memory-only A+B, duplicated stale high-confidence memory, corrected fresh evidence, and valid immutable-cache reuse each covered |
| D-02 (TR-02) | Next-decision context is a 200-char prefix, so the model cannot see unvisited leads or already-read passages | L + T | 3, 6 | Inspect the actual next provider request in tests: typed candidates, attempted/read/denied states, selected locators, unresolved obligations, remaining capacity. Larger log limits do not satisfy it |
| D-03 (TR-03) | Successful reads are spent on repetition and low-value extraction; 21 of 45 document calls hit one URL | T | 3, 5, 6, 9 | Versioned reads shared across agents; substantive-section selection; measure new eligible works and completed obligations, not raw read totals |
| D-04 (TR-04) | Planner anchors are stale and success conditions are compound with invented numeric tolerances | T + L | 2 | As-of stamping, atomic obligations, declared scope, source-appropriate support policy; no invented agreement tolerance |
| D-05 (TR-05) | Source Evaluator's useful judgments (derivative origin, observation date, fitness) are not enforced downstream | T + L | 4, with 6–7 as consumers | Typed role/freshness/dependence enforced at claim and statement level; verifier-acquired sources assessed; preserving reuse keyed by content/metadata revision |
| D-06 (TR-06) | Fact Checker repeats atoms, fixes a five-claim batch, and pools URLs without upstream excerpts | T | 1, 5, 6 | Stable atomic clusters, fair scheduling, exact evidence-union packet, evidence-ID adjudication; A+B-without-C and B-loss tests; refusal of incomplete support preserved |
| D-07 (TR-07) | `interim_satisfaction` suppresses a topic on any prior raw finding plus Critic silence | L | 9, 10, 11 | Required-target assessments reopen unmet obligations even when the Critic omits them; CLI wording distinguishes prior completion, deferral, and never-attempted |
| D-08 (TR-08) | Reader prose exposes weak selection: "not stated" mechanisms, leaked IDs, an undeclared number, overstated linkage | L | 7, 10, 11 | Answer-shaped outline, statement-level support checking including caveats/scope/tables/methodology, no unresolved numbers in prose |
| D-09 (TR-09) | Critic is search-only, its gaps rest on data no tool call returned, and its score is poorly calibrated | L + T | 6, 8, 12 | Tool-free review of the exact packet; defect-specific repair actions; schema repair preserves the input packet; calibration against strong source-attributed answers and polished non-answers |
| D-10 (§6.2) | Evaluation and production resolve different reasoning overrides; every real evaluation record is `git_dirty: True` | L | 2, 12, 13 | Evaluation and production resolve the same override at the declared candidate; the release-candidate evaluation runs from a clean tree |
| D-11 (§6.5) | Deterministic gates can pass **vacuously** while the agent produced no answer (task21: 4/4 gates green, judge 0.3675) | L | 6, 10 | Positive controls: a non-vacuous answer must be required; zero-false-pass and zero-false-fail calibration |
| D-12 (§6.5) | Infrastructure failures (judge `schema_output`, provider `output_limit`) are recorded but could be misread as agent quality; `INFRASTRUCTURE FAILURE` records carry `average_quality = None` | L | 6, 8, 10, 12 | Judge status and reason surfaced where scores are read; aborted runs are never aggregated into a quality score |
| D-13 (§6.5) | A near-passing run still overran its budget (`tool_calls 11 exceed 10`) and its `source_urls` were not navigable to the domains actually retrieved | L | 3, 6, 11 | Budget compliance in the acceptance matrix; recorded URLs resolve to the pages actually read |
| D-14 (§6.4) | The offline suite writes fixture `results.json` files into the live-evaluation output namespace | L | 12 *(nearest owner; the plan names none)* | Test output is separable from evaluation evidence without opening each file |
| D-15 (§7) | Two of three repeated live atoms have **no** recorded verification passage, and the third's excerpts are prefix-capped at 200 characters, so historical payload loss is largely `not_diagnosable` | L + F | 1, 3, 5, 6 (bounded evidence manifests) | Every accepted claim carries replayable selected passages with locators; the F1 boundary moves from "not recorded" to "recorded and auditable" |
| D-16 (§3.2) | Log filename timestamps and trace `start_time` values disagree by 7 hours for 9 of 11 logs; declared run ordinals conflict at slot 4 | L + F | 11 *(nearest owner)* | One clock convention per artifact; run ordinals not required to disambiguate evidence |
| D-17 (§3) | The heat-pump run is declared as the "Seventh run of the standing authorization" but no log, report, or ledger for it exists in any checkout | F | *(controller)* | Located, or recorded as never executed |

Two rules bind every row: **REVIEW REQUIRED is not release approval**, and **an
infrastructure failure is not an agent-quality success**. Structural cleanliness
is necessary, not sufficient.

## 11. Verification performed for this document

| Check | Result |
| --- | --- |
| Branch and HEAD | `codex/agent-cli-quality-trace-plan` at `7fd440fc95674d3f0007fcfe37593838baf37309` (**L**) |
| Every named artifact located in exactly one place | yes; all in the old worktree; none fabricated in this worktree (**L**) |
| SHA-256 of the three canonical artifacts | reproduces the trace review's published values exactly (**L**) |
| SHA-256 of the Q1 artifacts | reproduces `2026-09-15-cli-q1-failed-validation.md`'s table exactly — an independent cross-check on the hashing method (**L**) |
| Per-agent `results.json` parsed | 81 files; 79 real records + 2 fixtures (**L**) |
| Ledger counts re-derived from the artifact | 16 claims, 15 passages, 10 sources, 36 findings, 78 error rows, 8 references (**L**) |
| Word counts re-derived | 1,629 / 1,746 / 1,873 / 1,938 / 2,099 / 2,332 / 3,261 / 6,291 / 10,377 / 15,567 / 3,656 (**L**) |
| `git diff --check` | clean (**L**, run before the commit) |
| Secrets | This document contains no credential value. Its only URLs are the published source URLs already present in the historical report and ledger. The historical runs record that credentials were supplied through the repository dotenv launcher by path, with no value printed, logged, or recorded |

## 12. Concerns and unresolved items

1. **Two of the three F1 atoms are `not_diagnosable` at the claim level.** For
   report cutoff and PJM cycle the ledger records no verification passage at all,
   so no A-vs-B comparison exists to perform. The plan's Task 5/6 contracts must
   make this boundary auditable rather than inferred.
2. **Error-record counting units are not reconciled** (78 ledger rows vs 80 trace
   records). R1 above records the gap without asserting a cause.
3. **The memory-store duplicate count does not match the trace-derived initial
   state** (3 Utility Dive copies on disk now vs 2 recorded in the initial state).
   §2.5 records the gap as unresolved.
4. **Run ordinals are internally inconsistent** in the predeclarations, and the
   Q1 authorization is counted as one run in one record and two in another. §3.2.
5. **The heat-pump run is declared but unlocated** (D-17).
6. **Every real evaluation record was taken from a `git_dirty: True` tree.** No
   evaluation in this inventory is reproducible from a clean commit.
7. **No raw payload exists for any historical run.** This is the single largest
   limitation on every later task's ability to attribute a defect to a specific
   historical loss rather than to the class of loss. Tasks 1–6 must create the
   bounded evidence manifests that make future attribution possible; they cannot
   retroactively repair this baseline.
8. **The claim that the external facts in the reports are true is not made
   anywhere in this document.** The baseline records what the pipeline produced
   and what the agents and judges did with it.
