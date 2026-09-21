# Task 12 report — Prove the real agents and CLI in an offline adversarial matrix

**Status: BLOCKED** (needs a controller ruling before the matrix can be green)

Dispatch settings: model `deepseek-v4-flash`, reasoning effort `max`.
Worktree: `...\.worktrees\agent-cli-quality-trace-plan`, branch
`codex/agent-cli-quality-trace-plan`. Started at HEAD `06ec090` with a clean
tree; this report lands as `wip(task-12): …` (`bd5bc37`) with `src/` and
`tests/` untouched, so the tree is clean again at hand-over.

I did not push, did not dispatch subagents, did not run `git stash`, and did not
invoke the paid individual-agent evaluation CLI.

---

## 1. Why this is BLOCKED

The 18-row matrix cannot produce its required `accepted / 0` rows. The real
graph, driven offline through the real six production agents, aborts inside the
real synthesizer with:

```
deep_research.agents.report.StatementMappingError: a substantive reader
statement carries no evidence link: C005
```

Root cause (a product defect, in files outside this task's file list):
**every reader statement loses the evidence ids its claims selected**, because
four call sites iterate `claim.evidence_selection.values()` where the contract
keys by evidence id and values with the stance. `derive_statement` therefore
sees `"supports"` where it expects `ev-…`, drops it as "not in the evidence
registry", and writes `evidence_ids=[]`.

The written contract is unambiguous (`src/deep_research/utils/types.py:786-789`,
`fact_checker.py:1446` construction, `fact_checker.py:2708`
`selection.get(unit.evidence_id) == "supports"`, and
`tests/test_agents/test_fact_checker.py:5027`
`assert set(claim.evidence_selection) == {"ev-left", "ev-right"}`).
`claim.evidence_selection` is `{evidence_id: stance}`; the four sites below read
it as `{stance: evidence_id}` and therefore never yield an id:

| # | Site | Code |
| - | ---- | ---- |
| 1 | `utils/types.py:2026` `derive_statement` | `for evidence_id in claim.evidence_selection.values():` |
| 2 | `agents/synthesizer.py:914` `_selected_ids` | `return list(claim.evidence_selection.values())` |
| 3 | `agents/synthesizer.py:1441` `_cited_evidence` | `for evidence_id in claim.evidence_selection.values():` |
| 4 | `agents/researcher.py:232` `_cited_read_incidence` | `for evidence_id in claim.evidence_selection.values():` |

Consequences today, all observed on the real stack:

- Reader statements carry no evidence ids, so every substantive statement that
  cannot resolve a claim link raises `StatementMappingError`. Section/summary
  points survive (they resolve through `_statement_points`); **answer-row cells
  do not** — `_statement_points` (`agents/report.py:558`) indexes summary,
  constraints and sections, never `composition.answer_rows`, so a factual
  question whose answer-row labels are attested (mode `attributed`) hard-fails
  the run. The matrix rows `primary-attribution` and `non-constraint-answer`
  are exactly this shape.
- The synthesis packet prints `supports: none recorded` for claims that do carry
  selected evidence (`_selected_ids`), so carry-forward requirement 3 (mutating
  `statement_source_urls` to return an invented URL must fail a positive case)
  is currently unobservable: `validate_report_statements` calls
  `statement_source_urls(statement.evidence_ids, …)` with an always-empty list.
- `_cited_read_incidence` builds `{}` on every run, so the read-cache citation
  guard (Task 3) never constrains cache reuse.

Related observation, verified by grep, not by inference: **nothing in
production ever mints a claim cluster.** `consolidate_claims` is called only
from `tests/test_agents/test_claim_clusters.py`;
`src/deep_research/{agents,graph,runtime,evaluation}` contain only *consumers*
of `claim_clusters`. So `composition.claim_clusters` is empty in every real run
and the cluster branch of `derive_statement` (the branch the synthesizer test
fixtures use) can never fire. That is what hid this defect: the statement-map
tests all inject `claim_clusters={CLUSTER_ID: _cluster()}`.

## 2. Evidence (RED -> measured GREEN)

All runs: `PYTHONPATH=<worktree>\src` with the worktree's venv python
(`...\deep-research\.venv\Scripts\python.exe`, 3.12.14); import target printed
before each run and confirmed as the worktree's `src\deep_research\__init__.py`.

### 2.1 Unit RED (SCRATCH\defect_evidence_selection.py)

```
>   assert statement.evidence_ids == ["ev-1"]
E   AssertionError: assert [] == ['ev-1']
FAILED scratch/defect_evidence_selection.py::test_a_selected_evidence_id_reaches_the_statement
1 failed in 0.23s
```

The claim carries `evidence_selection={"ev-1": "supports"}`, the composition
carries `evidence={"ev-1": unit}`, the statement comes back with no id.

### 2.2 End-to-end RED (SCRATCH\spike3.py — real graph, real agents)

13 statement records dumped at the composition boundary, every one with
`evidence= []`, including a substantive answer-row cell:

```
STM S001 substantive= True evidence= [] claims= ['8fd2c0df…'] | The measured efficiency … | mode= attributed clusters= []
STM C005 substantive= True evidence= [] claims= [] | Acme widget mode= attributed clusters= []
STM A004 substantive= True evidence= [] claims= [] | The measured efficiency … mode= attributed clusters= []
…
deep_research.agents.report.StatementMappingError: a substantive reader statement carries no evidence link: C005
```

(`scratch/spike3-out/run.txt`.)

The exception escapes `run_research_graph` entirely (no node or CLI handler
catches it): in the same run the synthesis packet already showed
`C001 [insufficient_evidence 0.50 | primary-source attribution; independent
corroboration not established] …` with `supports: none recorded` even though the
claim's own record is `evidence_selection ==
{'ev-ae8494cc410b347a8923e3cf': 'supports'}` — the decisive lines from that one
provider request, under both trees, are kept at
`scratch/evidence-packet-supports.txt`. (I dumped every provider request to
`scratch/` while probing and deleted the dumps at hand-over rather than commit
prompt bodies; the two transcripts and the extracted lines are the evidence I
kept.)

### 2.3 Measured GREEN with the 4-line fix

Patch saved verbatim at `scratch/defect-orientation-fix.patch` (48 lines, 4
one-line edits, one per site above; **not applied in the tree** — I reverted it
so the tree stays clean for the ruling).

- Reproduction: `1 passed in 0.07s` (`scratch/repro-green.txt`).
- Same spike scenario (`scratch/spike3-out/run-with-fix.txt`): the graph no longer aborts; the
  synthesizer publishes a reader report (2487 chars) with a Key-facts table,
  citations, and the quality snapshot (`coverage_ratio=1.0 planned_topics=3
  covered_topics=3`, `semantic_review_status='scored'`); statements now carry
  ids (`STM C005 … evidence= ['ev-ae8494cc…']`), and the packet renders
  `supports: ev-ae8494cc410b347a8923e3cf chunk-0 "…"` where it said
  `none recorded` before.
- Full suite on the patched tree: `3 failed, 4163 passed, 1 deselected,
  2 warnings in 30.73s` (baseline `4166 passed`; 4163 + 3 = 4166, so the patch
  moves exactly three tests):

| Failing test | Why, and what it needs |
| --- | --- |
| `tests/test_agents/test_report_review.py::test_a_ranked_report_shows_the_reviewer_its_rows_and_their_evidence` | Fixture at line 1776 writes `evidence_selection={"support": "e1"}` — the mis-oriented form. It must become `{"e1": "supports"}`. |
| `tests/test_evaluation/test_config.py::test_every_target_prompt_fingerprint_is_pinned_against_prompt_drift` | `agent_prompt_fingerprint` hashes the whole agent module source, so any edit to `synthesizer.py` moves the pin: `{'synthesizer': '97cf77acbb15'} != {'synthesizer': '2af90b7ac8a8'}`. |
| `tests/test_evaluation/test_config.py::test_the_synthesizer_repin_is_attributed_to_the_publication_helper` | Same re-pin, pinned literally at line 1053 (`assert agent_prompt_fingerprint("synthesizer") == "2af90b7ac8a8"`). |

Both `test_config` failures are the pin mechanism working as designed (the
docstring says the next author must attribute the move) — but a re-pin is a
governed act on this plan (Task 11 recorded one for the synthesizer), and it
touches the identity Task 13's paid runs record. Site 4 additionally changes
acquisition/cache behaviour (Task 3's seam). That is why I am not ruling on it
myself.

## 3. What I need from the controller

1. **Who owns the fix?** Either (a) Task 12 takes it: the four edits above, plus
   the re-pins in `tests/test_evaluation/test_config.py` recorded with the
   attribution ("Task 12 orientation fix"), plus the fixture correction at
   `tests/test_agents/test_report_review.py:1776` — measured fallout is exactly
   those three tests and nothing else; or (b) it goes to a repair task and Task
   12's positive rows are re-scoped to expected-failure.
2. **Is `claim_clusters` supposed to be produced in the main pass?** If yes, the
   wiring (calling `consolidate_claims` from the fact checker's `consolidate`
   job) is a second, larger gap that the same class of positive case depends on;
   if no, `derive_statement`'s cluster branch and the fixtures that rely on it
   need a comment saying so.
3. Confirmation that site 4 (`_cited_read_incidence`) may be included: it
   changes read-cache reuse, and I have not measured it in isolation.

## 4. What exists in the tree right now

Nothing in `src/` or `tests/` was changed — the measurement patch was reverted
(`git status --short` is empty; `git apply --check
scratch/defect-orientation-fix.patch` reports the patch still applies cleanly to
`bd5bc37`). `scratch/` holds:

- `spike3.py` — the working offline replay prototype this task's `replay.py` is
  to be built from: `ReplaySource`/`ReplayTopic`/`ReplayScenario` fixtures whose
  `__post_init__` refuses an excerpt that is not literally in the page text,
  `ReplayCompleter` (asserts the incoming schema/target/read/evidence packet
  before answering; parses `- target_id=`, `- read_id=… locator=…`,
  `- evidence_id=… excerpt=`, `^C\d+ [ … ] … coverage=…` out of the real
  packets), `ReplaySearch`/`ReplayHTTP`, and a driver that builds the runtime
  via `runtime.assembly.build_runtime` with `tavily_api_key=""`, an in-memory
  Chroma stand-in and deterministic embeddings, tracing off — i.e. six real
  agents, real graph, real reviewer, zero network.
- `spike3-out/` — the RED/GREEN evidence above: `run.txt` (pre-fix: 13
  statements, all `evidence= []`, then `StatementMappingError`),
  `run-with-fix.txt` (patched: report published), the three published artifacts
  under `documents/`, and `evidence-packet-supports.txt`.
- `defect_evidence_selection.py`, `repro-red.txt`, `repro-green.txt`,
  `defect-orientation-fix.patch`, `fullsuite-with-fix.txt`.

Harness defects I found in my own prototype and would fix while porting (listed
so a successor does not rediscover them): the scripted researcher reads
`read_urls:` where the acquisition context prints `read_urls=`, so it re-asks
for an already-read URL and eats 8 `agent_tool_policy_rejected` observations per
run; claims reach adjudication with `targets= []`, so the quality snapshot
reports `unanswered_critical_targets`; and the scenario needs a stop condition
that reaches `accepted` rather than a second refinement pass.

## 5. Not started (all blocked on §3)

The versioned 18-case manifest and three offline repetitions per case;
`tests/test_e2e_evaluation/test_real_agents.py`; the two mutation tests
(`claim_evidence_pool` omitting B, `statement_source_urls` returning an invented
URL); the network-denial guard and zero-network evidence; warm/cold memory
fixtures (obsolete generated claims, valid cache artifact, forged cache
metadata, changed source) and the `query_memory`-as-read mutation; the
late-candidate / late-section assertions at `observation_summary_chars = 200`;
`--production-parity`; the six `evaluation/cases/*.py` modules and
`evaluation/{cli,config,dependencies,models,evaluators,judging,targets}.py`
extensions; D-14 namespace separation; the two validation documents; case
versions, fingerprints and the final gate run.

