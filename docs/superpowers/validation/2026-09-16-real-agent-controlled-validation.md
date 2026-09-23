# Controlled real-agent validation: the Task 12 evaluation harness

Date: 2026-09-22.
Branch: `codex/agent-cli-quality-trace-plan`.
Plan: [evidence integrity and agent production readiness](../plans/2026-09-16-evidence-integrity-and-agent-production-readiness.md), Task 12.
Status: **documentation only.** This document changes no application code, no
test, and no fixture. It runs no live provider call and spends no budget.

**Verdict: CONTROLLED READY / LIVE NOT VALIDATED.**

Every figure below was produced by a command run in this worktree on 2026-09-22
at candidate `7f5f362`, and the command's own output is quoted verbatim. No
number here is copied from an earlier document, inferred, or rounded.

## 0. What this document does NOT prove

Read this before reading any table below.

**The six new individual-agent evaluation cases have never been executed against
a real model.** `scoped-evidence-targets`, `read-bearing-acquisition`,
`work-role-independence`, `upstream-independent-pair`,
`canonical-evidence-report` and `typed-gap-calibration` are registered,
inventoried, and validated as *cases*; not one of them has been run against
`deepseek-v4-flash` or any other model. What exists for them is a set of
guard-proof unit tests in `tests/test_evaluation/test_evaluators_agents.py`
(Rounds 4-6) which establish that each new metric is **load-bearing** — that the
score moves when the behaviour it names is present or absent, and that it does
not move when it should not. That is a property of the *metric*. It is not a
statement about how a real model performs against these cases, and it is not
evidence of agent production readiness.

**What the six new cases have is a proof that their metrics can discriminate.
What they do not have is a measurement of anything a real model did.** Running
those six against a real model is Task 13, and Task 13 is paid. Until it runs,
the controlled tier's real-agent claim rests on the 18 replay-manifest rows in
§3.1, which are deterministic replays and not model outputs either. Those rows
now *require* determinism rather than merely reporting it (§3.1), which
strengthens what they prove about the harness and changes nothing about what
they prove about the agents — they still exercise scripted provider and tool
boundaries, not a model.

The `CONTROLLED READY` half of the verdict below therefore means exactly this:
**the harness is built, wired, inventoried, versioned, network-isolated, and
green at a named candidate.** It does not mean any agent has been shown to work.

## 1. Candidate and import target

| Item | Value | Source |
| --- | --- | --- |
| Candidate SHA | `7f5f36299e1ba9176b4ba7fe9b5a9ee9f7cf0c1b` | `git rev-parse HEAD` |
| Working tree | clean — `git status --porcelain` produced **zero** lines | `git status --porcelain` |
| Import target | `C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.worktrees\agent-cli-quality-trace-plan\src\deep_research\__init__.py` | `python -c "import deep_research; print(deep_research.__file__)"` |

**The named candidate is the state measured, not the commit this file lives
in.** This document is itself a later, documentation-only commit on the same
branch, so `HEAD` when it is read is a different SHA. That commit changes no
application code, no test and no fixture —
`git diff 7f5f362..HEAD -- src tests config.yaml pyproject.toml` is empty — and
every measurement below is named against `7f5f362` because that is the state it
was taken on.

The import-target line is recorded because this environment carries a known
trap: `PYTHONPATH` is empty by default and a global editable-install `.pth`
silently resolves bare `python -m deep_research...` to a **different worktree**.
Every `deep_research` command in this document was run with `PYTHONPATH` set to
this worktree's `src`, and the printed path above is the proof that it took
effect. The tree was confirmed clean **before** the first line of this document
was written, and a command run against a dirty tree would not have been
countable here.

The candidate is not incidental to these measurements: the two suite artifacts
quote their own `graph_revision`, and both print
`graph_revision: 7f5f36299e1ba9176b4ba7fe9b5a9ee9f7cf0c1b` — the artifact names
the commit it was produced at, so a reader does not have to take this table's
word for the provenance.

## 2. Case versions

| Constant | Value | Location |
| --- | --- | --- |
| `CASE_REGISTRY_VERSION` | **2** | `src/deep_research/evaluation/cases/__init__.py:46` |
| `REPLAY_CASE_MANIFEST_VERSION` | **1** | `src/deep_research/e2e_evaluation/replay_matrix.py:37` |
| `REPLAY_CASE_VERSION` | **1** | `src/deep_research/e2e_evaluation/replay_matrix.py:42` |

`CASE_REGISTRY_VERSION` is the version of the registry's *semantics*, not of a
file: v1 was three controlled and one live case per agent, v2 is the
declared-inventory registry carrying Task 12's high-risk cases. The bump is
deliberately ahead of the six cases it names, and the module says why — an
artifact stamped `case_registry_version: 2` beside a v1 case set would fail
loudly, while a stale `1` beside six unrecognised cases would fail silently.

**Per-case versions.** Every registered case is at `version=1` except one:
`source-evaluator-live-ranking` is at **`version=2`**
(`src/deep_research/evaluation/cases/source_evaluator.py:571`). It is the only
per-case version that differs from the registry default, and it is a **live**
case. No resolved case in the controlled tier is above v1 — which is what makes
the registry-version bump the load-bearing carrier of "these cases are new"
rather than the per-case field.

The e2e matrix pins its 18 real-agent rows to `REPLAY_CASE_VERSION = 1`
uniformly (`replay_matrix.py:1769,1780,1791,1802,1813,1824,1835,1846,1857,1868,1879,1890,1901,1912,1923,1935,1947,1959`)
and its 5 graph-historical rows the same way
(`replay_matrix.py:2010,2021,2032,2043,2056`), declared beside
`REPLAY_CASE_MANIFEST_VERSION` so a recorded result names the semantics it was
produced under.

## 3. Suite inventory

### 3.1 Real-agent e2e suite — 18 rows

`python -m deep_research.e2e_evaluation suite --tier controlled --repetitions 3`.
The command's own header names the mode and the provenance:

```
Mode: real-agent (18 cases from replay manifest v1, case semantics v1)
Agents: production classes through the real graph
```

The 18 rows, in the order the command printed them, each `passed (3
repetitions, deterministic)`:

| # | Case id | Result |
| --- | --- | --- |
| 1 | `broad-constraints` | passed (3 repetitions, deterministic) |
| 2 | `comparative-conflict` | passed (3 repetitions, deterministic) |
| 3 | `refinement-evidence-recovery` | passed (3 repetitions, deterministic) |
| 4 | `blocked-html-pdf-fallback` | passed (3 repetitions, deterministic) |
| 5 | `same-work-mirror` | passed (3 repetitions, deterministic) |
| 6 | `semantic-duplicate-claims` | passed (3 repetitions, deterministic) |
| 7 | `stalled-refinement` | passed (3 repetitions, deterministic) |
| 8 | `primary-attribution` | passed (3 repetitions, deterministic) |
| 9 | `current-versus-forecast` | passed (3 repetitions, deterministic) |
| 10 | `unsupported-mechanism` | passed (3 repetitions, deterministic) |
| 11 | `judge-failure` | passed (3 repetitions, deterministic) |
| 12 | `non-constraint-answer` | passed (3 repetitions, deterministic) |
| 13 | `late-contradiction` | passed (3 repetitions, deterministic) |
| 14 | `empty-but-clean` | passed (3 repetitions, deterministic) |
| 15 | `memory-is-not-read` | passed (3 repetitions, deterministic) |
| 16 | `validated-cache-reuse` | passed (3 repetitions, deterministic) |
| 17 | `decision-context-late-candidate` | passed (3 repetitions, deterministic) |
| 18 | `reopen-unanswered-target` | passed (3 repetitions, deterministic) |

Suite line: `Suite: accepted (3 repetitions per case, 18/18 rows)`.
Artifact: `output\evaluations\e2e\replay-suite.json`.

**Determinism is required, not reported.** A row passes only when its three
repetitions agree on the whole outcome — exit code, terminal quality, answered
targets, and the published report's fingerprint — and every expectation holds:
`passed=deterministic and all(not item.expectation_failures ...)`
(`src/deep_research/e2e_evaluation/runner.py:631-633`). A row whose repetitions
disagree fails as `NON-deterministic`; it is not reported as passing with a note
beside it, and a suite holding such a row is not accepted — the suite is
`accepted` only when every row `passed` **and** the guard recorded zero network
attempts across the whole campaign
(`accepted=all(case.passed for case in results) and attempts == 0`,
`runner.py:720`). That requirement is why the harness now stamps a replay row's
dates from its own pinned instant rather than the wall clock (§8.2, N1).

The artifact records `accepted: true` beside `rows_accepted: false`. That is the
matrix's own design rather than a shortfall: several rows declare a `partial`
product verdict deliberately, so `rows_accepted` — the stricter product-level
fact, which asks whether every repetition's terminal quality was `accepted`
(`runner.py:724-728`) — is false while every row met the result it declared. The
suite line counts the former, and `18/18 rows` is that count.

This inventory is **manifest-derived**, not a literal. An earlier state of this
branch ran 3 of these 18 rows and printed `Suite: accepted` regardless; the
rewiring that made the command enumerate its own manifest is one of the five
items this round closes (§9).

### 3.2 Graph-historical mode — 5 rows, NOT release evidence

`python -m deep_research.e2e_evaluation suite --tier controlled --mode graph-historical --repetitions 3`.
The command discloses its own status in its header, and this document repeats
that disclosure rather than restating it more favourably:

```
Mode: graph-historical (5 scripted-double cases)
Agents: SCRIPTED DOUBLES, not production classes — historical regression only.
        This mode is not release evidence for the real agents.
```

Each row line states three facts — what the row *declared*, whether it *met*
that declaration, and what the product's own graph quality status was — and the
lines are quoted exactly as printed:

```
broad-constraints: declared accepted, met; campaign accepted; product graph_quality_status partial; coverage 1.00; judge 1.00
comparative-conflict: declared accepted, met; campaign accepted; product graph_quality_status partial; coverage 1.00; judge 0.81
refinement-evidence-recovery: declared accepted, met; campaign accepted; product graph_quality_status partial; coverage 1.00; judge 0.86
claimed-coverage-open-obligation: declared partial, met; campaign not accepted; product graph_quality_status partial; coverage 0.75; judge 0.82
declared-obligations-answered: declared accepted, met; campaign accepted; product graph_quality_status partial; coverage 1.00; judge 0.86
```

Suite line: `Suite: accepted (3 repetitions per case; 4/5 rows accepted)`.
Artifact: `output\evaluations\e2e\suite.json`.

**Four of the five rows are accepted by design; the fifth is expected not to
be.** `claimed-coverage-open-obligation-graph` declares
`partial / coverage_below_0.80` (`replay_matrix.py:2041-2053`) and
`declared-obligations-answered-graph` declares `accepted / 0`
(`replay_matrix.py:2054-2066`). The campaign passes when every row meets its own
declaration — `accepted = all(case.met_expectation for case in results)`
(`runner.py:443`) — so `4/5 rows accepted` beside a suite verdict of `accepted`
is the intended outcome, not a failure. A reader who sees `4/5` without that
reason would read it as one. The pair exists to prove the stricter reading is
not "always fails": one row leaves a declared obligation unanswered and is
expected to be caught, and the control answers every declared obligation and is
expected to pass.

The two rows are new in this round and are the only rows here that exercise the
coverage grading described in §3.4.

**These five rows are not release evidence for the real agents.** They run
scripted doubles, not the production agent classes. They are retained as a
historical regression check on the pre-existing cases. The coverage and judge
figures above describe scripted doubles executing a script, and no inference
about the real agents' quality may be drawn from them. Every one of them also
reports `product graph_quality_status partial` **by construction**, for the
reason given in §8.4(b).

Note the differing network line: the real-agent suite reports
`socket layer denied` (§7); this mode reports `zero (scripted dependencies
only)`, because it dials nothing at all.

### 3.3 Individual-agent evaluation harness

`python -m deep_research.evaluation list`, measured at this candidate:

| Tier | Count |
| --- | --- |
| controlled | **24** |
| live | **6** |
| **total** | **30** |

**Correction against the task brief.** The brief for this round described this
harness as "30 controlled + 6 live cases". The measured inventory is **24
controlled + 6 live = 30 total**; the brief's figure double-counts the total as
the controlled tier. The number recorded here is the one the command printed,
verified independently by enumerating the registry in Python
(`all_cases()` → `Counter({'controlled': 24, 'live': 6})`). Four controlled and
one live per agent across six agents is what the registry holds.

The controlled tier per agent, with the **six NEW cases** named explicitly and
the agent each belongs to:

| Agent | Controlled case ids | New in Rounds 4-6 |
| --- | --- | --- |
| `planner` | `focused-decomposition`, `ambiguous-scope`, `planning-tool-failure`, **`scoped-evidence-targets`** | **scoped-evidence-targets** |
| `researcher` | `multi-source-coverage`, `conflicting-evidence`, `partial-search-failure`, **`read-bearing-acquisition`** | **read-bearing-acquisition** |
| `source-evaluator` | `strong-and-weak-sources`, `corroboration-recency-reputation`, `reputation-provider-failure`, **`work-role-independence`** | **work-role-independence** |
| `fact-checker` | `mixed-verdicts`, `independent-domain-evidence`, `verification-search-failure`, **`upstream-independent-pair`** | **upstream-independent-pair** |
| `synthesizer` | `complete-cited-report`, `conflict-and-limitations`, `composition-no-publication`, **`canonical-evidence-report`** | **canonical-evidence-report** |
| `critic` | `approve-strong-report`, `request-more-research`, `missing-evidence-or-budget-exhausted`, **`typed-gap-calibration`** | **typed-gap-calibration** |

The live tier, one case per agent: `planner-live-scope`,
`researcher-live-evidence`, `source-evaluator-live-ranking`,
`fact-checker-live-verification`, `synthesizer-live-report`,
`critic-live-review`. The `evaluation list` header for each agent names the
dataset and its repetition count (`deep-research-<agent>-controlled-v1`,
3 repetitions; `deep-research-<agent>-live-v1`, 1 repetition).

The live tier is **declared only and has no runner**: asking the e2e command for
a live row is refused by name rather than silently substituted —

```
error: live tier is declared only and has no runner; running it requires a separately authorized canary
```

— and the process exits **2**.

See **§0**: all six new cases are unexecuted against a real model.

### 3.4 Substantive coverage on the new obligation row

The harness's coverage grading is now part of what is being certified, so the
one row that exercises it is quoted with both readings. From the row's own
repetition record (`output\evaluations\e2e\claimed-coverage-open-obligation\case.json`):

```
planned_topics: 4, attempted_topics: 4, covered_topics: 3, coverage_ratio: 0.75,
claimed_covered_topics: 4, claimed_coverage_ratio: 1.0
```

and the CLI summary the row produced:

```
Quality: partial (critic 9/10; 4/4 topics claimed, 100%)
Coverage: 3/4 topics covered (substantive, 75%); 3/4 required targets answered; 0/0 critical targets answered
```

**Claimed and substantive coverage disagree on this row, and the substantive
reading is the one that is graded.** A checked claim recorded `topic-01` as
consumed — so every topic is *claimed* covered, `4/4 = 1.00` — while the plan's
declared obligation for that topic ("the plan's independent pair stands open")
was not answered by the evidence, leaving `3/4 = 0.75` substantive. The campaign
grades the substantive ratio whenever the plan declares targets
(`e2e_evaluation/evaluators.py:505-513`), which is why this row records
`coverage_below_0.80` and is `not accepted`. The claimed reading is published
beside it rather than discarded, because the campaign record has to show both.
This is the harness refusing to let a consumed topic id stand in for an answered
obligation, which is the behaviour the new case exists to pin.

## 4. Test counts

| Scope | Result | Command |
| --- | --- | --- |
| Full suite | **`4518 passed, 1 deselected, 2 warnings in 72.23s (0:01:12)`** | `python -m pytest -q -p no:cacheprovider` |
| `tests/test_evaluation` + `tests/test_e2e_evaluation` + `tests/test_cli` | **`1275 passed, 1 warning in 64.02s (0:01:04)`** | `python -m pytest tests/test_evaluation tests/test_e2e_evaluation tests/test_cli -q` |
| `tests/test_e2e_evaluation/test_real_agents.py` (the real-agent matrix) | **35 tests collected** | `python -m pytest tests/test_e2e_evaluation/test_real_agents.py -q --collect-only` |
| `tests/test_evaluation/test_evaluators_agents.py` (the guard-proof file for the six new cases) | **88 tests collected** | `python -m pytest tests/test_evaluation/test_evaluators_agents.py -q --collect-only` |

The single deselected test is deselected by configuration rather than by this
round: `pyproject.toml:44` sets `addopts = "-m 'not live'"`, and the suite
reports `4518/4519 tests collected (1 deselected)`. The two warnings are
third-party deprecations (`ast.Str` via `langsmith`; `httpx` via
`starlette.testclient`) and neither originates in this repository.

The 88 guard-proof tests are the ones §0 refers to. They prove the new metrics
discriminate; they say nothing about a model. The real-agent matrix file holds
35 tests over the 18 manifest rows. The full suite was run with
`-p no:cacheprovider` so that the run writes no cache directory into the tree it
is measuring; `output/` is git-ignored, so the suite artifacts the commands in
§3 write do not make the tree dirty either.

## 5. Fingerprints

For a representative controlled resolution — `typed-gap-calibration`, the new
critic case — resolved at this candidate via `build_runtime_config`:

| Fingerprint | Value | What it hashes |
| --- | --- | --- |
| `configuration_fingerprint` | `6e92eb61475a` | application settings, target model + effort + profile source, ReAct transport, thinking mode, dataset and rubric versions, package version — `src/deep_research/evaluation/config.py:493` |
| `judge_configuration_fingerprint` | `924caf47aa0d` | provider, structured transport, judge model + effort + temperature, rubric version — `src/deep_research/evaluation/config.py:508` |
| `prompt_fingerprint` (= `target_prompt_fingerprint`) | `aedce1ccca9e` | `agent_prompt_fingerprint("critic")` — the agent's own module source plus the shared `agents.prompts` source — `src/deep_research/evaluation/config.py:558`, `config.py:280` |
| judge prompt fingerprint | `74b9cddfbbee` | `judge_prompt_fingerprint(rubric_version=1)` |

The two prompt fingerprints are **not** ad-hoc values: they match the pinned
constants in `tests/test_evaluation/test_config.py` exactly.
`prompt_fingerprint` `aedce1ccca9e` equals `PINNED_TARGET_PROMPT_FINGERPRINTS["critic"]`
(`tests/test_evaluation/test_config.py:804-811`) and `CRITIC_PROMPT_FINGERPRINT`
(`test_config.py:258`); the judge value `74b9cddfbbee` equals
`PINNED_JUDGE_PROMPT_FINGERPRINT` (`test_config.py:816`). The full pinned target
set at this candidate:

| Agent | `PINNED_TARGET_PROMPT_FINGERPRINTS` |
| --- | --- |
| `planner` | `d2d7dec17bcd` |
| `researcher` | `ec5244f2ba7f` |
| `source_evaluator` | `ad9e2afac12c` |
| `fact_checker` | `00e2229ad4fa` |
| `synthesizer` | `a41d1f86be3b` |
| `critic` | `aedce1ccca9e` |

`test_config.py:1186-1189` asserts that the pinned dict's key set equals
`AGENT_NAMES` and that the computed fingerprints equal the pinned values, so a
prompt drift fails the suite rather than silently invalidating this table.

The same six values are also recorded inside each suite artifact's own metadata
(`metadata.target_prompt_fingerprints`, §1), so the fingerprints a run was
scored under travel with its result instead of living only in this document.

Resolution also confirmed `production_parity: True` with source
`configuration` for this case — the `--production-parity` flag exists
(`src/deep_research/evaluation/cli.py:189`, with `--no-production-parity` at
`:201`) and this resolution records where the value came from
(`config.py:363`, `config.py:435-437`). The resolved target and judge model are both
`deepseek-v4-flash`.

## 6. Reviewed snapshots

Every output quoted in this document was read by a human in the session that
produced it and confirmed correct **before** this document was written. The
three CLI outputs that constitute the snapshot reviewed here:

| Snapshot | What was confirmed |
| --- | --- |
| `python -m deep_research.evaluation list` | All 30 registered cases present with the correct owning agent and tier; all six new case ids present exactly once (`scoped-evidence-targets`, `read-bearing-acquisition`, `work-role-independence`, `upstream-independent-pair`, `canonical-evidence-report`, `typed-gap-calibration`); nothing filed under the wrong agent; header lines naming dataset and repetitions correct |
| `python -m deep_research.e2e_evaluation suite --tier controlled --repetitions 3` | 18 rows enumerated, none missing, none repeated; every row `passed` **and** `deterministic`; the suite line agrees with the row count; the mode header names the manifest and case-semantics versions |
| `python -m deep_research.e2e_evaluation suite --tier controlled --mode graph-historical --repetitions 3` | 5 rows, each line carrying the three facts; four rows accepted and the declared-partial row `not accepted` with its declaration met; the mode's disclosure header read in full and reproduced verbatim in §3.2 |

Reviewed snapshot limitations, stated plainly: these are **inventory and
verdict** outputs, together with the single row artifact whose own fields §3.4
quotes. No agent transcript and no report body was reviewed for correctness in
this round — the individual-agent cases have produced no artifacts to inspect,
because they have never been executed against a model (§0). The coverage figures
in §3.4 are read from the record a row wrote about itself, not from a reader's
judgement of the report it published.

## 7. Zero-network evidence

The real-agent suite printed, at the end of the run in §3.1:

```
Network: zero (socket layer denied; 0 attempts recorded)
```

**This is enforced at the socket layer, not by an HTTP client's own
accounting.** `src/deep_research/e2e_evaluation/replay.py:1215-1275` replaces
`socket.socket`, `socket.socketpair`, `socket.create_connection` and
`socket.getaddrinfo` with denying equivalents for the duration of a row
(`replay.py:1227-1229`, `replay.py:1265-1267`), then restores them
(`replay.py:1272-1274`); the runner opens that scope around every repetition
(`src/deep_research/e2e_evaluation/runner.py:582`). A client that bypasses its
own accounting, or one that never had any, is still denied at the syscall
boundary — which is the only reason a count of `0 attempts recorded` means what
it says. Had any attempt been recorded, the reported line would instead read
`Network: NOT zero` (`runner.py:993-997`).

The graph-historical mode's separate line, `Network: zero (scripted
dependencies only)`, is a weaker statement and is reported as such: that mode
has nothing to dial, so it exercises no denial boundary.

## 8. Known limitations

### 8.1 The cache-provenance shape is unit-tested only, not exercised end-to-end

`content_version_changed` / `content_hash_changed` — the fourth cache-provenance
shape from Task 12's original real-agent matrix work — is covered by unit tests
but is **not** exercised end-to-end by the matrix. At this candidate the two
names appear only in `src/deep_research/agents/acquisition.py:527,529` and in
`tests/test_agents/test_acquisition.py:747,755`; nothing in
`src/deep_research/e2e_evaluation/` produces or asserts them. The matrix's
`validated-cache-reuse` row does not drive that transition. This is a
deliberately disclosed gap, not a discovered defect: the behaviour is believed
correct on the strength of its unit tests, and no end-to-end evidence supports
that belief.

### 8.2 What closed since the last certification of this document

The previous version of this document listed three findings from an earlier
whole-branch re-review as open, alongside the cache-provenance gap retained in
§8.1. None of those three is open at `7f5f362`; neither is the one finding the
*final* whole-branch review raised against this round's own work (N1), which
this candidate closes:

| Item | Status | Closed by |
| --- | --- | --- |
| Whole-branch re-review finding 1 — `CriticTarget.open` pooled `answered_dimensions` with no mode filter or support-policy test, so a target answered only by a `contested` statement rendered as "answered" in the Critic packet while `target_is_answered` called it unanswered | **CLOSED** | `bab55d6` — *fix(critic): the target view is the coverage gate, not a second definition* (52 lines of `agents/critic.py`, 26 of `utils/types.py`). The packet no longer derives its own answer: `CriticTarget.answered` is documented as read straight from `target_is_answered`, "the gate that decides coverage", and `open` is now `not self.answered` (`src/deep_research/agents/critic.py:603-608,616-618`). |
| Whole-branch re-review findings 2 and 3 (`dimension_support` absent from the published quality record and from `composition_fingerprint`; `_fill_statement_map` copying `answered_dimensions` without it) | **CLOSED** | The remediation series, recorded closed at `663def2`. Both are visibly addressed at this candidate: `dimension_support` is now published on the statement rows and consumed by the gate (`src/deep_research/utils/types.py:2942-2948`), and the legacy fallback no longer hand-copies the cells — it derives its statement through the same builder that supplies both (`utils/types.py:2196-2219`, `utils/types.py:2119-2158`). |
| The remediation's F1-F7 | **CLOSED** | `663def2`, per the final whole-branch review. Its F4-F7 were each re-verified by the reviewer at that commit rather than read from a report: the claimless-statement comment matches the code, `not_comparable` is documented legacy/read-only, the determinism requirement is enforced, and the untracked scratch file is untracked. |
| Final-review finding **N1** — requiring determinism made the replay suite fail if a row's repetitions straddled UTC midnight, because the reader printed the real run date on `Generated on` and the replay harness injected no clock | **CLOSED** | `7f5f362` — *fix(evaluation): stamp a replay row from the harness clock, not the wall clock*. The clock is now a dependency of the run: `build_runtime` takes a `clock`, `REPLAY_CLOCK_INSTANT = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)` is injected through `replay_clock` (`src/deep_research/e2e_evaluation/replay.py:117,120-122`), and the planner, researcher and synthesizer all take it — so all three date lines in a replay report are the harness's, and the fingerprint keeps `Generated on` on purpose because which day a run says it printed its report is part of what it published. Pinned by `tests/test_e2e_evaluation/test_runner.py:1316-1317`. |

### 8.3 Open and non-blocking at this candidate

The final whole-branch review's two remaining findings are **open**, both
**non-blocking**, and both carried forward rather than fixed here. Each location
below was re-checked in this worktree at `7f5f362` (`7f5f362` touches only
`README.md`, `e2e_evaluation/replay.py`, `e2e_evaluation/runner.py`,
`runtime/assembly.py` and `tests/test_e2e_evaluation/test_runner.py`, so neither
site moved):

| # | Severity | Location | Summary |
| --- | --- | --- | --- |
| **N2** | P2 (next cycle) | `src/deep_research/graph/nodes.py:1258-1274`; `src/deep_research/agents/source_evaluator.py:1010` | An `assess_source` repair cannot re-assess a source that only the Fact Checker read. `invalidation_update` drops evaluated-source rows by the invalidated claims' own `claim.source_urls`, and the Source Evaluator's next build scores only groups derived from `raw_findings`, so a URL that reached the read registry through verification is never re-scored. The consequence is an **ineffective repair, not a false acceptance**: the repair re-scores the other source, the claim is re-verified against an unchanged assessment, and the loop ends through `no_progress` with the report partial. It is also the flip side of the guarantee that such a read cannot be dropped. |
| **N3** | P2 (already present on `main`, not a regression) | `src/deep_research/agents/synthesizer.py:1516` vs `src/deep_research/agents/quality.py:439-442` | A point citing two claims that each carry their own URL is accepted by the synthesizer's union rule (`approved_urls` unions every named claim's `source_urls`) and then hard-fails the gate's per-claim rule (`if any(url not in claim_urls for url in point_urls)` builds `claim_urls` per claim, inside the loop). Identical at `b2fa96b` (`synthesizer.py:561-574`, `quality.py:203-207`). |

Neither is a measured failure of anything in §3: no row in either suite produces
either shape, and no acceptance in this document rests on one.

### 8.4 Residuals and differences a certifying reader needs

**(a) The campaign grades a 4-topic plan more strictly than the product does.**
The product's own hard failure fires only on a broad plan:
`planned_topics >= BROAD_PLAN_MIN_TOPICS` (`= 5`,
`src/deep_research/agents/quality.py:39`) **and**
`broad_ratio < BROAD_PLAN_COVERAGE_THRESHOLD` (`= 0.80`, `quality.py:40`), which
is what appends `broad_plan_coverage_below_0.80` (`quality.py:531-535`). The
campaign's leg has no plan-size guard at all:
`if planned_topics and coverage_ratio < 0.80: integrity.append("coverage_below_0.80")`
(`src/deep_research/e2e_evaluation/evaluators.py:631-632`). The new obligation
row plans **4** topics, so the campaign fires a coverage failure the product's
own broad-plan gate would not — deliberately, and the row declares that leg
(`replay_matrix.py:2041-2053`). A reader comparing this row's
`coverage_below_0.80` against production behaviour should not expect the product
to have raised the same failure on the same plan.

**(b) Every scripted row reports `graph_quality_status partial` by
construction.** Acceptance is a property of the review, not of the route:
`graph_quality_status` (`src/deep_research/graph/state.py:371-406`) returns
`accepted` only when the route is `critique_satisfied` **and**
`semantic_review_passes(state.report_review)`; a run no quality pass ever judged
is `partial`. The graph-historical rows wire no semantic reviewer, so their
`report_review` cannot pass and `partial` is the only status they can produce.
The `product graph_quality_status partial` printed on all five rows in §3.2 is
therefore a fact about the scripted harness, not a quality verdict on anything.

**(c) The live tier is declared only.** Six live cases are registered and
listed; none can be run (`§3.3`, refused by name, exit 2). `LIVE NOT VALIDATED`
is not a cautious reading of a measured result — it is the measurement.

**(d) The review's open residuals are §8.3's N2 and N3**, itemised there with
their locations, plus the unit-tested-only cache-provenance shape in §8.1.

## 9. What this round closed

This is Round 7 of 7 of a dedicated follow-up that closed the five
evaluation-harness items Task 12's own completion record left outstanding:

| # | Item | Closed by |
| --- | --- | --- |
| 1 | `--production-parity` flag absent from `evaluation/cli.py` | Round 1 — now present at `cli.py:189`, with `--no-production-parity` at `:201` and provenance recorded (§5) |
| 2 | Six high-risk cases unregistered | Rounds 4-6 — all six registered and enumerated by `evaluation list` (§3.3) |
| 3 | Suite inventory not manifest-derived | Round 2 — `suite --tier controlled` now enumerates 18/18 rows from the manifest and discloses its mode (§3.1) |
| 4 | Stale hard-coded test | Round 2 — the literal assertion replaced by the declared inventory contract |
| 5 | This validation document absent | Round 7 — this document |

`CASE_REGISTRY_VERSION` moved `1` → `2` in the same series (§2), so artifacts
produced from here record the registry semantics they were scored under.

## 10. Verdict

**CONTROLLED READY / LIVE NOT VALIDATED.**

`CONTROLLED READY` is asserted on the strength of the measurements in §1-§7,
each of which was produced by a command run at candidate `7f5f362` on a
confirmed-clean tree with the import target proven: the candidate is named, its
case versions are named, its suite inventory is enumerated (18 real-agent rows
with determinism required, 5 explicitly-non-evidence historical rows whose row
lines state their declarations, 24 controlled + 6 live individual cases), its
test counts are exact (4518 / 1275 / 35 / 88), its fingerprints are pinned and
cited, its reviewed snapshots are listed, and its zero-network claim is enforced
at the socket layer rather than self-reported.

No item in this round's required-content list was found missing, and none was
written around. One figure in the round's brief — "30 controlled" — was found
to be wrong on measurement and is corrected in §3.3 rather than reproduced.
Every figure the superseded version of this document carried was re-measured
rather than carried over; where a re-measurement changed a value (the candidate,
the suite inventory, the counts, the target fingerprints and the configuration
fingerprint) the new measurement is what appears here.

`LIVE NOT VALIDATED` is asserted because it is true: no live run has cleared
the release criteria, no live run was attempted in this round, and the six new
cases have never been executed against a real model (§0). **The controlled half
of this verdict certifies the harness. It does not certify the agents.**
