# Task 12 report — Prove the real agents and CLI in an offline adversarial matrix

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
