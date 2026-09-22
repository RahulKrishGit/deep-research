# Controlled real-agent validation: the Task 12 evaluation harness

Date: 2026-09-22.
Branch: `codex/agent-cli-quality-trace-plan`.
Plan: [evidence integrity and agent production readiness](../plans/2026-09-16-evidence-integrity-and-agent-production-readiness.md), Task 12.
Status: **documentation only.** This document changes no application code, no
test, and no fixture. It runs no live provider call and spends no budget.

**Verdict: CONTROLLED READY / LIVE NOT VALIDATED.**

Every figure below was produced by a command run in this worktree on 2026-09-22
at candidate `27c45b9`, and the command's own output is quoted verbatim. No
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
§3.1, which are deterministic replays and not model outputs either.

The `CONTROLLED READY` half of the verdict below therefore means exactly this:
**the harness is built, wired, inventoried, versioned, network-isolated, and
green at a named candidate.** It does not mean any agent has been shown to work.

## 1. Candidate and import target

| Item | Value | Source |
| --- | --- | --- |
| Candidate SHA | `27c45b9c1b6e2e32e26420c72b4befd16eb48da5` | `git rev-parse HEAD` |
| Working tree | clean — `git status --porcelain` produced **zero** lines | `git status --porcelain` |
| Import target | `C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.worktrees\agent-cli-quality-trace-plan\src\deep_research\__init__.py` | `python -c "import deep_research; print(deep_research.__file__)"` |

The import-target line is recorded because this environment carries a known
trap: `PYTHONPATH` is empty by default and a global editable-install `.pth`
silently resolves bare `python -m deep_research...` to a **different worktree**.
Every `deep_research` command in this document was run with `PYTHONPATH` set to
this worktree's `src`, and the printed path above is the proof that it took
effect. The tree was confirmed clean **before** the first line of this document
was written, and a command run against a dirty tree would not have been
countable here.

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

The e2e matrix pins its 18 rows to `REPLAY_CASE_VERSION = 1` uniformly
(`replay_matrix.py:250,322,393,488,548,660,746,919,967,1044,1128,1173,1217,1306,1380,1506,1570,1659`),
declared beside `REPLAY_CASE_MANIFEST_VERSION` so a recorded result names the
semantics it was produced under.

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

This inventory is **manifest-derived**, not a literal. An earlier state of this
branch ran 3 of these 18 rows and printed `Suite: accepted` regardless; the
rewiring that made the command enumerate its own manifest is one of the five
items this round closes (§9).

### 3.2 Graph-historical mode — 3 rows, NOT release evidence

`python -m deep_research.e2e_evaluation suite --tier controlled --mode graph-historical --repetitions 3`.
The command discloses its own status in its header, and this document repeats
that disclosure rather than restating it more favourably:

```
Mode: graph-historical (3 legacy ScriptedGraphAgent cases)
Agents: SCRIPTED DOUBLES, not production classes — historical regression only.
        This mode is not release evidence for the real agents.
```

| Case id | Result |
| --- | --- |
| `broad-constraints` | accepted; coverage 1.00; judge 1.00 |
| `comparative-conflict` | accepted; coverage 1.00; judge 0.81 |
| `refinement-evidence-recovery` | accepted; coverage 1.00; judge 0.86 |

Suite line: `Suite: accepted (3 repetitions per case)`.
Artifact: `output\evaluations\e2e\suite.json`.

**These three rows are not release evidence for the real agents.** They run
scripted doubles, not the production agent classes. They are retained as a
historical regression check on the pre-existing cases. The coverage and judge
figures above describe scripted doubles executing a script, and no inference
about the real agents' quality may be drawn from them.

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

See **§0**: all six new cases are unexecuted against a real model.

## 4. Test counts

| Scope | Result | Command |
| --- | --- | --- |
| Full suite | **`4375 passed, 1 deselected, 2 warnings in 91.21s (0:01:31)`** | `python -m pytest -q` |
| `tests/test_evaluation` + `tests/test_e2e_evaluation` + `tests/test_cli` | **`1240 passed, 1 warning in 75.83s (0:01:15)`** | `python -m pytest tests/test_evaluation tests/test_e2e_evaluation tests/test_cli -q` |
| `tests/test_e2e_evaluation/test_real_agents.py` (the real-agent matrix) | **35 tests collected** | `python -m pytest tests/test_e2e_evaluation/test_real_agents.py -q --collect-only` |
| `tests/test_evaluation/test_evaluators_agents.py` (the guard-proof file for the six new cases) | **79 tests collected** | `python -m pytest tests/test_evaluation/test_evaluators_agents.py -q --collect-only` |

The single deselected test is pre-existing and unrelated to this round. The two
warnings are third-party deprecations (`ast.Str` via `langsmith`; `httpx` via
`starlette.testclient`) and neither originates in this repository.

The 79 guard-proof tests are the ones §0 refers to. They prove the new metrics
discriminate; they say nothing about a model. The real-agent matrix file holds
35 tests over the 18 manifest rows.

## 5. Fingerprints

For a representative controlled resolution — `typed-gap-calibration`, the new
critic case — resolved at this candidate via `build_runtime_config`:

| Fingerprint | Value | What it hashes |
| --- | --- | --- |
| `configuration_fingerprint` | `0eaf6fcb3a39` | application settings, target model + effort + profile source, ReAct transport, thinking mode, dataset and rubric versions, package version — `src/deep_research/evaluation/config.py:472` |
| `judge_configuration_fingerprint` | `924caf47aa0d` | provider, structured transport, judge model + effort + temperature, rubric version — `src/deep_research/evaluation/config.py:487` |
| `prompt_fingerprint` (= `target_prompt_fingerprint`) | `9694e44926d3` | `agent_prompt_fingerprint("critic")` — the agent's own module source plus the shared `agents.prompts` source — `src/deep_research/evaluation/config.py:537`, `config.py:269-277` |
| judge prompt fingerprint | `74b9cddfbbee` | `judge_prompt_fingerprint(rubric_version=1)` |

The two prompt fingerprints are **not** ad-hoc values: they match the pinned
constants in `tests/test_evaluation/test_config.py` exactly.
`prompt_fingerprint` `9694e44926d3` equals `PINNED_TARGET_PROMPT_FINGERPRINTS["critic"]`
(`tests/test_evaluation/test_config.py:731-738`) and
`CRITIC_PROMPT_FINGERPRINT` (`test_config.py:257`); the judge value
`74b9cddfbbee` equals `PINNED_JUDGE_PROMPT_FINGERPRINT`
(`test_config.py:743`). The full pinned target set at this candidate:

| Agent | `PINNED_TARGET_PROMPT_FINGERPRINTS` |
| --- | --- |
| `planner` | `4fab1aa863d8` |
| `researcher` | `70d8d679ea89` |
| `source_evaluator` | `6c12c0fffc92` |
| `fact_checker` | `97de796ffb4a` |
| `synthesizer` | `97cf77acbb15` |
| `critic` | `9694e44926d3` |

`test_config.py:1113-1116` asserts that the pinned dict's key set equals
`AGENT_NAMES` and that the computed fingerprints equal the pinned values, so a
prompt drift fails the suite rather than silently invalidating this table.

Resolution also confirmed `production_parity: True` with source
`configuration` for this case — the `--production-parity` flag exists
(`src/deep_research/evaluation/cli.py:189`, with `--no-production-parity` at
`:201`) and this resolution records where the value came from.

## 6. Reviewed snapshots

Every output quoted in this document was read by a human in the session that
produced it and confirmed correct **before** this document was written. The
three CLI outputs that constitute the snapshot reviewed here:

| Snapshot | What was confirmed |
| --- | --- |
| `python -m deep_research.evaluation list` | All 30 registered cases present with the correct owning agent and tier; all six new case ids present exactly once (`scoped-evidence-targets`, `read-bearing-acquisition`, `work-role-independence`, `upstream-independent-pair`, `canonical-evidence-report`, `typed-gap-calibration`); nothing filed under the wrong agent; header lines naming dataset and repetitions correct |
| `python -m deep_research.e2e_evaluation suite --tier controlled --repetitions 3` | 18 rows enumerated, none missing, none repeated; every row `passed`; the suite line agrees with the row count; the mode header names the manifest and case-semantics versions |
| `python -m deep_research.e2e_evaluation suite --tier controlled --mode graph-historical --repetitions 3` | 3 rows, matching the legacy case count; the mode's disclosure header read in full and reproduced verbatim in §3.2 |

Reviewed snapshot limitations, stated plainly: these are **inventory and
verdict** outputs. No agent transcript, report body, or score artifact was
inspected in this round — the individual-agent cases have produced no artifacts
to inspect, because they have never been executed against a model (§0).

## 7. Zero-network evidence

The real-agent suite printed, at the end of the run in §3.1:

```
Network: zero (socket layer denied; 0 attempts recorded)
```

**This is enforced at the socket layer, not by an HTTP client's own
accounting.** `src/deep_research/e2e_evaluation/replay.py:1192` replaces
`socket.socket`, `socket.create_connection` and `socket.getaddrinfo` with
denying equivalents for the duration of a row, then restores them
(`replay.py:1202-1248`); the runner opens that scope around every repetition
(`src/deep_research/e2e_evaluation/runner.py:485`). A client that bypasses its
own accounting, or one that never had any, is still denied at the syscall
boundary — which is the only reason a count of `0 attempts recorded` means
what it says. Had any attempt been recorded, the reported line would instead
read `Network: NOT zero` (`runner.py:805`).

The graph-historical mode's separate line, `Network: zero (scripted
dependencies only)`, is a weaker statement and is reported as such: that mode
has nothing to dial, so it exercises no denial boundary.

## 8. Known limitations

Two items remain open beyond this round's scope. They are recorded here so that
a reader of this document does not need the ledger to know what is not proven.

**1. The cache-provenance shape is unit-tested only, not exercised
end-to-end.** `content_version_changed` / `content_hash_changed` — the fourth
cache-provenance shape from Task 12's original real-agent matrix work — is
covered by unit tests but is **not** exercised end-to-end by the matrix. The
matrix's `validated-cache-reuse` row does not drive that transition. This is a
deliberately disclosed gap, not a discovered defect: the behaviour is believed
correct on the strength of its unit tests, and no end-to-end evidence supports
that belief.

**2. Three findings from the whole-branch re-review remain open, all
non-blocking.** From the branch review's `## Re-review` findings table:

| # | Severity | Location | Summary |
| --- | --- | --- | --- |
| 1 | Minor (new) | `src/deep_research/agents/critic.py:602-609, 1072` | `CriticTarget.open` pools `answered_dimensions` with no mode filter and no support-policy test, so a target answered only by a `contested` statement renders as "answered" in the Critic packet while `target_is_answered` calls it unanswered — the repair loop is not told about the target failing the gate |
| 2 | Minor (new) | `src/deep_research/agents/report.py:2608-2624`; `src/deep_research/graph/state.py:503-509` | `dimension_support` is now the load-bearing input to `target_is_answered` but appears in neither the published quality record's statement rows nor `composition_fingerprint`, so the artifact an auditor reads cannot reconstruct why a target was or was not answered |
| 3 | Informational (new) | `src/deep_research/utils/types.py:2178-2191` | `_fill_statement_map` copies `answered_dimensions` but not `dimension_support`, taking the stricter legacy fallback; verified unreachable in production (`synthesizer._build_constraint` always supplies both cells via `_build_cell`, so the `existing is not None` guard skips it) |

The re-review's own verdict on these three: they are Minor and Informational,
**none is a merge blocker**, and findings 1 and 2 are routed to Task 14. The
re-review additionally established that finding 1 widens a *pre-existing*
divergence between `CriticTarget.open` and `target_is_answered` (neither has
ever applied a support-policy test) rather than creating a new one, and widens
it only in the direction of the strict side being stricter.

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
each of which was produced by a command run at candidate `27c45b9` on a
confirmed-clean tree with the import target proven: the candidate is named, its
case versions are named, its suite inventory is enumerated (18 real-agent rows,
3 explicitly-non-evidence historical rows, 24 controlled + 6 live individual
cases), its test counts are exact (4375 / 1240 / 35 / 79), its fingerprints are
pinned and cited, its reviewed snapshots are listed, and its zero-network claim
is enforced at the socket layer rather than self-reported.

No item in this round's required-content list was found missing, and none was
written around. One figure in the round's brief — "30 controlled" — was found
to be wrong on measurement and is corrected in §3.3 rather than reproduced.

`LIVE NOT VALIDATED` is asserted because it is true: no live run has cleared
the release criteria, no live run was attempted in this round, and the six new
cases have never been executed against a real model (§0). **The controlled half
of this verdict certifies the harness. It does not certify the agents.**
