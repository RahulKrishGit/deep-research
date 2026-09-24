# Evidence Verifier Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. This plan runs its tasks in **parallel waves**: read "Execution model" before dispatching anything. It overrides one rule of that skill ("never dispatch implementation subagents in parallel") on the user's instruction; per-task worktrees and file ownership prevent the conflict that rule guards against.

**Goal:** Replace the fact checker with the Evidence Verifier (Figure Match plus one batched Context Check), make verified findings the only thing a report cites, merge the critic into one Report Reviewer, allow at most one targeted extra research pass, and slim the planner, so that `deep_research` answers the benchmark question with a great report in about 30 minutes (45 at most).

**Architecture:** The researcher's findings now carry a verbatim snippet, the read and locator it came from, and structured figures. A deterministic Figure Match and one batched, tool-free AI Context Check turn them into verified findings, each figure with a confirmed period, scope, attribution and kind. The Report Writer cites verified findings only, by label; code builds the key facts table, duplicates, revisions and the Not found list from fields; the Report Reviewer makes the single quality judgement, and the code-computed list of missing required targets drives one targeted extra pass.

**Tech Stack:** Python (repo venv), Pydantic v2, LangGraph, DeepSeek V4 Flash through the repo's provider layer, pytest.

**Spec:** `docs/superpowers/specs/2026-09-24-evidence-verifier-pipeline-design.md` (approved 2026-09-24). Read it with this plan. Evidence behind it: `agent://ArchAudit`, `agent://FableAudit3`, `agent://Audit3Report`, `agent://Preflight4`.

## Global Constraints

- Worktree `.worktrees/evidence-verifier`, branch `codex/evidence-verifier-pipeline`. Every path in this plan is relative to the worktree root.
- Clean cutover (D6): no feature flag, no retained old path, no compatibility shim, alias, or re-export of a deleted name.
- No corroboration step (D2): nothing searches for a second source, and nothing in a report says one source confirms another.
- Honesty rules (spec §2), binding on every task:
  - A relay is never presented as the organisation it relays.
  - No provenance, scope or date that the page does not carry.
  - Every number in the report is traceable to its cited page.
  - Forecasts are reported with issuer and release. Actuals are labelled as actuals.
  - No as-of cutoff is inferred from a year in the question; an explicit "as of" still freezes the date.
  - Only 1-iteration live runs until the report is judged great (see PD-2).
- `graph.max_extra_passes` defaults to 1. An extra pass runs only when required targets are missing, and only for those targets (D4, §6.5).
- Context Check: one call per batch of `agents.verifier_batch_size` (5) findings, batches run concurrently (at most `agents.verifier_concurrency`, 8, at once), no tools and no web search, reasoning effort `high` (§5.2, §7.3, D8/D9). The Statement Check (§5.4, D8) judges one batch of 5 drafted sentences per call under the same concurrency.
- Researcher per sub-topic: tool budget 20 (`agents.tool_budget_overrides.researcher`, §7.2) and at most 7 model turns (`agents.max_iterations: 7`, the cap that actually bounds a sub-topic's loop); at most 5 sub-topics (`agents.max_sub_topics: 5`). Both set in Task 1.3 (F1).
- Researcher sub-topics run concurrently (D9, §7.2): at most `agents.sub_topic_concurrency` (5) in flight, each with its own scratchpad and acquisition context, while the tool section (policy decision, fetch, admission) runs under one run-wide lock so a page two loops request is downloaded once; findings and events fold in plan order. Source-evaluator scoring batches run at most `agents.source_scoring_concurrency` (3) in flight, each batch standing alone. Every cap is config (§7.3), never a module constant; Task 4.13 sets them and the telemetry of Task 4.14 reports them.
- Snippet: one or two sentences copied verbatim, enforced as at most 600 characters (`MAX_SNIPPET_CHARS`).
- Report Reviewer acceptance: mean ≥ 0.80 over the seven dimensions, no material defect, no gate failure (§6.3).
- Out of scope (§12): UI changes (README line 747 records that the Streamlit app was removed; there is no UI code to compile), other benchmark questions (they are exercised by the e2e matrix only), and automatic adjustment of concurrency or budgets during a run (§7.3 only advises; Task 4.14 prints the advice and never tunes).
- Never read `.env`. Live model calls happen off-peak only: never start one if any minute of the next 50 minutes falls inside Mon–Fri 01:00–04:00 or 06:00–10:00 UTC. A started live run is never hard-stopped.
- New public names in `src/deep_research/agents/*.py` are imported by `src/deep_research/agents/__init__.py` and listed in its `__all__` (`tests/test_imports.py::test_agent_submodule_public_names_all_reach_all`). Public names in `src/deep_research/utils/types.py` that other packages use are re-exported from `src/deep_research/utils/__init__.py` the way `Finding` is today.
- No formatter, linter, or whole-suite run inside a task. Whole-suite runs happen only at the phase gates that name them.

## Review Focus

1. **A forecast sentence the writer drafts is kept by the Statement Check with a correct code-built label.** D8 deletes the code rewrite: the Statement Check (§5.4) judges the wording once, and code attaches the figure's verified organisation, kind and release as the reader label, so a kept forecast still reads as a forecast, with its issuer, whatever the prose says. The 2026-09-24 pre-flight lost two of its three 2025 forecasts to the old code refusal; the eight live G3 sentences — all forecasts — are kept now. Pinned by Task 3.4 `test_the_eight_live_g3_sentences_are_kept_when_the_checker_says_consistent` and `test_every_kept_sentence_still_ends_with_its_figures_code_built_label`; the reviewer side by Task 4.12 RV1–RV3.
2. **The Context Check call fails for a batch** (outage, timeout, truncation twice): those findings stay citable with the "unchecked context" label, the run records the error, and the report still publishes. Pinned by Task 2.1 `test_failed_batch_marks_findings_context_unchecked` and Task 4.8 `test_run_publishes_when_the_context_check_fails`.
3. **The extra pass finds nothing for the missing target**: the report publishes once with the target under Not found; there is no second extra pass and no crash. Pinned by Task 4.8 `test_extra_pass_that_finds_nothing_publishes_with_not_found` and e2e row `extra-pass-finds-nothing` (Task 4.9).
4. **An all-segment figure written as grid-scale** (audit-2's 18.9 GW is "utility, C&I, and residential"): the sentence is refused and logged; it never reaches the reader. Under D8 the Statement Check refuses it (an `inconsistent` verdict whose reason names the scope), and the figure's own label still states the verified scope. Pinned by Task 4.9's e2e row `scope-corrected-to-all-segments`, the harness check C4 (Task 3.6), and Task 3.4 `test_an_inconsistent_verdict_refuses_with_its_reason`.
5. **Cosmetic differences between a snippet and its page** (curly quotes, soft hyphens, a word broken across a line, capital letters) must not drop a correct finding, while a snippet whose words are not on the page is dropped. Pinned by Task 1.2 `test_excerpt_matches_is_cosmetic_only`.

---

## How to run things

Every command in this plan assumes this shell setup (Git Bash on Windows):

```bash
W="C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.worktrees/evidence-verifier"
PY="C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.venv/Scripts/python.exe"
cd "$W"
export PYTHONPATH='src;.'
```

Named commands used by the gates:

```bash
# FULL SUITE (two invocations; tests/test_state.py must run on its own)
"$PY" -m pytest -q tests --ignore=tests/test_state.py
"$PY" -m pytest -q tests/test_state.py

# E2E MATRIX (network-zero, scripted boundaries)
"$PY" -m deep_research.e2e_evaluation suite --tier controlled --repetitions 3

# OFF-PEAK CHECK (prints OK or REFUSE; required before every live call)
"$PY" -c "import datetime as d;n=d.datetime.now(d.timezone.utc);bad=[m for m in range(51) if (t:=n+d.timedelta(minutes=m)).weekday()<5 and (1<=t.hour<4 or 6<=t.hour<10)];print('REFUSE' if bad else 'OK', n.strftime('%a %H:%MZ'))"

# IMPORT SMOKE (every package a user or a harness imports)
"$PY" -c "import deep_research.agents, deep_research.graph, deep_research.runtime, deep_research.api, deep_research.cli, deep_research.main, deep_research.evaluation, deep_research.e2e_evaluation"
```

Task worktrees (see "Execution model", rule R1). The controller creates one per implementation task; the implementer runs every command of its task from inside it:

```bash
ID=t1-3                                   # the task id, dots as dashes
git -C "$W" worktree add "../ev-$ID" -b "ev/$ID"
cd "$W/../ev-$ID" && export PYTHONPATH='src;.'
```

`PYTHONPATH='src;.'` puts the task worktree's own `src` ahead of anything installed in the venv, so every worktree tests its own code with the one shared venv.

**Roles.** Architecture-sensitive tasks go to `sp-hard-implementer`; standard tasks to `sp-implementer`; operator tasks (live calls) are run by the main session; task reviews go to a reviewer that did not write the code; the final audit is run by a fresh, read-only reviewer that has not seen the implementation.

**Commits.** One commit per task (plus one per fix round), made in the task's own worktree, adding only the task's owned files by explicit path (`git add <paths>`; never `git add -A`, `git add .` or a directory). Never commit `scratch/` or `output/` (both are gitignored). The controller merges task branches into the plan branch (rule R4); implementers never merge.

**Tests.** Follow the repo's existing test style (plain pytest functions, `pytest.mark.asyncio` for async). New test helpers for findings and reads live in `tests/evidence_fakes.py` (created in Task 1.1) and are imported as `from tests.evidence_fakes import ...`. A task's scoped test command never includes `tests/test_imports.py` or `tests/test_evaluation/test_config.py` unless the task owns that file (rule R3).

## Plan decisions (where the spec leaves a choice)

- **PD-1 One prerequisite pulled forward.** §6.6 target answering needs structured target fields from step 3 on. Task 1.1 therefore adds them to `EvidenceTarget` and Task 1.4 to the planner draft, in step 1, additively. Step 5 then removes the old target fields and changes which targets are required. The six spec steps and their proofs otherwise run unchanged and in order.
- **PD-2 "1-iteration live run".** The old `--max-iterations 1` meant "first pass plus at most one refinement pass", which is exactly `max_extra_passes: 1`. The live run is one CLI invocation with the default config.
- **PD-3 Additive steps 1–3.** Steps 1–3 add code beside the old pipeline, which keeps running (and its tests stay green) until step 4 switches the graph. Step 4 is the only phase whose tasks leave the whole suite red mid-phase; its gate needs the whole suite and the e2e matrix green.
- **PD-4 Finding identity and storage.** A finding's id is `deep_research.agents.identity.finding_fingerprint(finding)` (existing). Verified findings live in `ResearchState.verified_findings`: the complete snapshot for the run so far, replaced on each write (like `evaluated_sources`), each finding carrying its `verification`.
- **PD-5 Missing required targets are computed by code.** The Report Writer node's quality pass computes them (§6.6), and the Report Reviewer node stamps them on the `ReportReview` record as `missing_required_target_ids`. The AI reviewer never produces them, and routing reads the record. (Listed in the review section.)
- **PD-6 The writer cites labels, not URLs.** The draft carries finding labels only; citations are derived from the cited findings, so "a citation URL not carried by the cited findings" cannot occur. (Listed in the review section.)
- **PD-7 Qualitative targets.** A target with no unit dimension is answered by a verified finding that names it in `target_ids`, provided the organisation matches when the target names one. Every other §6.6 check needs a figure. (Listed in the review section.)
- **PD-8 Attribution is resolved by code** from the Context Check's proposal and the page's own words, per the table in Task 2.1.
- **PD-9 Duplicates and revisions.** The same value for the same organisation, unit dimension, period and kind is one fact, with or without a target. A revision needs two findings that answer the same target, both carrying a release key (`release_date`, `vintage` or `statement_date`) that differ. Anything else stays two rows and is never called a revision.
- **PD-10 Gate set (§6.4).** `unresolved_citations`, `uncited_settled_points`, `duplicate_fact_rows`, `missing_as_of`, `missing_scope`, `unjudged_sentences`, `unaccounted_required_targets`, `missing_reader_report`, `missing_evidence_ledger`. Removed: `duplicate_claims`, `duplicate_source_rows`, `unscored_cited_sources`, `contradicted_settled_claims`, `broad_plan_coverage_below_0.80`, `unanswered_critical_targets`, and `untraced_figures` (D8 deletes the code wording checks that computed it). `duplicate_fact_rows` is an invariant: `fact_rows()` already merges same-fact rows, so it guards hand-built compositions and future producers, and no test fixture is spent on it (F11). `unjudged_sentences` replaces it as the same kind of invariant: `compose_written_report` records every kept sentence's Statement Check outcome in `ReportComposition.statement_verdicts` (`consistent`, `corrected` or `unchecked` when its batch failed), so the gate fires only when a kept sentence has no verdict and no recorded batch failure — exactly §6.4's "every kept sentence was judged by the Statement Check, or its batch failure is recorded".
- **PD-11 Names (D5).** The renames apply to what an operator or reader sees: module `agents/report_writer.py` (class `ReportWriterAgent`, node and config key `report_writer`) and module `agents/report_reviewer.py` (class `ReportReviewer`, node `report_reviewer`, service role and `model_overrides` key `report_reviewer`). Unchanged: the artifact names (`report-<session>-<n>.md`, `-evidence.md`, `-quality.json`), the code name "evidence ledger", the config key `agents.report_review_max_tokens`, and the graph status value `max_iterations` (API and CLI vocabulary, now meaning "extra passes spent and the report not accepted", PD-23).
- **PD-12 Context Check bounds (§7.3, D8/D9).** The batch size (5) and the concurrency (8) are the config values `agents.verifier_batch_size` and `agents.verifier_concurrency`, read by the Evidence Verifier from its `AgentRuntimeConfig`; `CONTEXT_CHECK_BATCH_SIZE` and `CONTEXT_CHECK_CONCURRENCY` stay only as the module defaults (and as the constructor defaults the verifier's tests may pin to 1). The 3,000-character passage window stays a module constant: it is a prompt bound, not a concurrency cap. The Statement Check shares the same two bounds (§5.4).
- **PD-13 An unscored review** (provider failure or invalid reply) routes to publication with graph status `incomplete` and quality `partial`. It never fails the run.
- **PD-14 Graph-historical e2e harness: retired (decided by the user, review item 8).** Its scripted six-agent doubles replay a graph that no longer exists, and the real-agent matrix covers the new graph end to end. Task 4.11 deletes its manifest, its `--mode graph-historical` and its scripted doubles with their tests; Task 4.6 deletes its README subsection; no gate runs it.
- **PD-15 CLI and API names stay.** The CLI flag `--max-iterations` and the API request field `max_iterations` keep their names, so existing invocations keep working (priority 1); they now set `max_extra_passes`, with the same numbers as before (PD-2). The config key (`graph.max_extra_passes`, env `GRAPH_MAX_EXTRA_PASSES`) and the state field (`ResearchState.max_extra_passes`) are renamed, as D4 names them. (Renaming the flag too is listed in the review section.)
- **PD-16 When the old target fields leave.** `support_policy` leaves in step 4, because §8 removes "pair and support-policy fields" with the fact checker and nothing reads it after that. `required_dimensions` and `critical` leave in step 5 (§7.1), with the planner rewrite. In step 4 the planner stops validating `required_dimensions` against claim checkability (the only reason it imported `claim_clusters`), and nothing reads `critical` once the `unanswered_critical_targets` gate is gone.
- **PD-17 Fingerprint pins.** `agent_prompt_fingerprint` hashes an agent's whole module plus `agents/prompts.py` (`evaluation/config.py`), so any edit to `researcher.py`, `planner.py` or `synthesizer.py` moves a pin. Each phase's integration task re-pins `PINNED_TARGET_PROMPT_FINGERPRINTS` (and `PINNED_JUDGE_PROMPT_FINGERPRINT` if it moved) to the values the merged tree computes, and adds one comment line per re-pin naming the task. A historical test that asserts one past pin value (for example `assert PINNED_TARGET_PROMPT_FINGERPRINTS["researcher"] == "d81af60c3103"`) and fails because of this plan's intended edit is deleted, not re-pinned: it records history, not a contract. The drift-alarm tests (`test_every_target_prompt_fingerprint_is_pinned_against_prompt_drift`, `test_the_judge_fingerprint_is_pinned_beside_the_six_target_pins`) stay. (Listed in the review section.)
- **PD-18 Naming a page's own organisation, the last fallback (after PD-25).** When the Source Evaluator recorded no validated issuer for the read, an `own` figure is credited to the organisation the Context Check names only when code confirms the page is that organisation's own: the existing `first_party_host_evidences_issuer`, or a government/education host whose registrable label spells the name (initials or the name run together, a leading "U.S." dropped) while the page names it (`eia.gov` and "U.S. Energy Information Administration"). Otherwise the figure is credited to the host (`woodmac.com`). A lookalike host (`eia.news`) is never credited with the organisation. (Listed in the review section.)
- **PD-19 One home for the shared wording rules.** The hedge, forecast-versus-actual and attested-name rules the Report Writer keeps (§6.2) are also needed by the Evidence Verifier (the kind fallback for an unchecked figure). They move out of `synthesizer.py` into a new `agents/wording.py` by Task 3.3, which runs in wave 1B (the approved R8 exception), so the Evidence Verifier imports `stated_role` from it from the start, never imports the writer, and step 4 can delete `synthesizer.py` whole. (Listed in the review section.)
- **PD-20 Labels are rendered, not stored.** Reader labels (§6.1) are computed by `report.py` from the fact row or figure context fields at render time, by one function (`figure_label`), which the writer's prompt uses too. No label text is stored in state.
- **PD-21 Step 4 deletes last.** In step 4, tasks running in parallel never delete a name that `agents/__init__.py` or `utils/__init__.py` exports, or that `fact_checker.py`, `claim_clusters.py`, `critic.py` or `synthesizer.py` imports; they stop calling it. Task 4.10 deletes those modules, their tests and every name left without a caller, in one sweep. This keeps every package importable while six tasks change it at once.
- **PD-22 The capped pre-flight is the real CLI.** Step 6's pre-flight runs `python -m deep_research` through `scratch/run_live_proof.py` with caps (two sub-topics, no extra pass, a search ceiling), not a hand-wired node chain. `scratch/preflight_downstream.py` is claim-era; it is not reworked and not copied into this worktree.
- **PD-23 Status after the extra pass (F4; decided).** An accepted report (no gate failure, including `unaccounted_required_targets`, and `semantic_review_passes`) finishes `completed` with quality `accepted`, even when passes are spent and a required target is listed under Not found (§6.4: "or is listed under Not found"; §6.5). `graph_route` checks, in order: halted; extra pass (missing targets and a pass left); review unavailable; report accepted; then `extra_passes_exhausted` (status `max_iterations`) when targets are still missing, else `report_not_accepted`. So `max_iterations` means "passes spent and the report not accepted".
- **PD-24 A forecast with no release says so (F5).** `figure_label` prints "forecast (release not stated on the page)" when a forecast carries no release, and `ReportQualitySnapshot.forecasts_without_release` counts such fact rows. It is printed on the CLI Integrity line and must be 0 in the pre-flight; it is not a gate (§6.4's gate list is fixed).
- **PD-25 The page's own organisation comes from the Source Evaluator first (F13; decided).** Spec §3 keeps the Source Evaluator's owning-organisation identification. When the evaluated source for a read carries a validated `identity_anchors["issuer"]`, the Evidence Verifier uses it as the page's own organisation (before PD-18's host rule), and the Report Writer adds the evaluated source's title and issuer to its attested corpus, so "Wood Mackenzie projects …" on `woodmac.com` is attested.
- **PD-26 An unchecked finding keeps its Figure Match status (F9).** A finding whose Context Check reply is missing or whose batch failed keeps status `verified` or `verified_corrected` from Figure Match and carries `context_unchecked`; it is never promoted by a Context Check it did not get, and its reader label says "unchecked context". The label is the reader's signal (§5.2).
- **PD-27 Concurrency is config with module defaults (D9, §7.3; from `agent://FableParallel`).** `agents.sub_topic_concurrency: 5`, `agents.source_scoring_concurrency: 3`, `agents.verifier_batch_size: 5` and `agents.verifier_concurrency: 8` (each with its `AGENTS_*` environment override) are the only caps; the module constants Task 2.1 left stay as defaults, and the researcher takes `sub_topic_concurrency=` as a constructor argument defaulting to the configured value, so a test that pins order can pin 1. The tool section of a turn (policy decision, fetch, admission) runs under one run-wide lock, and sub-topic findings and events fold in plan order, never completion order. (Listed in the review section; Task 4.13 implements it, Task 4.14 reports it.)

## Shared interfaces

Every task honours these names and types. A task that needs a name not listed here defines it itself and keeps it private (leading underscore) unless the task says otherwise.

### `src/deep_research/utils/types.py`

Added in Task 1.1, the step-1 contract task. Task 1.1 adds **every additive type of steps 1–3** at once (this block and the next two), so the parallel waves of Phases 1–3 build against fixed types; nothing reads the step-2 and step-3 types until their phases.

```python
FigureKind: TypeAlias = Literal["actual", "forecast"]
UnitDimension: TypeAlias = Literal["power", "energy", "percent"]
MAX_SNIPPET_CHARS = 600


class FindingFigure(ContractModel):
    """One figure a finding states, exactly as its snippet writes it (spec §4)."""

    value: str = Field(min_length=1)
    unit: str = Field(min_length=1)
    period: str | None = None
    kind: FigureKind | None = None


class Finding(ContractModel):  # existing fields unchanged, plus:
    snippet: str | None = None       # verbatim, <= MAX_SNIPPET_CHARS
    read_id: str | None = None
    locator: str | None = None
    figures: list[FindingFigure] = Field(default_factory=list)


class EvidenceTarget(ContractModel):  # existing fields unchanged until Task 5.1, plus:
    measure: str | None = None           # "battery storage power capacity added"
    unit_dimension: UnitDimension | None = None   # None: a qualitative target
    period: str | None = None            # "2024"
    kind: FigureKind | None = None
    geography: str | None = None         # "United States"
    organisation: str | None = None      # None: any organisation may answer
```

Also added in Task 1.1 (first read in step 2):

```python
FigureAttribution: TypeAlias = Literal["own", "relayed", "unattributed"]
FindingStatus: TypeAlias = Literal["verified", "verified_corrected", "dropped"]
FigureDropReason: TypeAlias = Literal[
    "evidence_not_on_page",     # §5.2: evidence_words are not in the read
    "figure_not_in_evidence",   # §5.2: a not_matched figure the evidence_words do not carry
    "correction_not_on_page",   # §5.2: corrected period or scope not in evidence_words or passage
    "context_rejected",         # §5.2: the Context Check said reject
    "context_unavailable",      # §5.2: a not_matched figure whose Context Check failed
]
FindingDropReason: TypeAlias = Literal[
    "read_not_found", "snippet_not_on_page", "all_figures_dropped"
]


class FigureContext(ContractModel):
    period: str | None = None
    scope: str | None = None
    attribution: FigureAttribution
    organisation: str = Field(min_length=1)  # own: the publisher; relayed: the originator;
                                             # unattributed: the page's owner (host)
    kind: FigureKind


class FigureResult(ContractModel):
    figure: FindingFigure
    matched: bool                          # Figure Match (§5.1 step 2)
    context: FigureContext | None = None   # set on every kept figure
    evidence_words: str | None = None
    corrected: bool = False
    dropped_reason: FigureDropReason | None = None
    reason: str | None = None              # the Context Check's own reason text

    @property
    def kept(self) -> bool:
        return self.dropped_reason is None


class FindingVerification(ContractModel):
    status: FindingStatus
    figure_results: list[FigureResult] = Field(default_factory=list)
    dropped_reason: FindingDropReason | None = None
    context_unchecked: bool = False


class Finding(ContractModel):  # plus:
    verification: FindingVerification | None = None   # None until verified


class ResearchState(ContractModel):  # plus:
    verified_findings: list[Finding] = Field(default_factory=list)  # complete snapshot, replaced
```

Also added in Task 1.1 (first read in step 3):

```python
class EarlierEdition(ContractModel):
    value: str               # "10.3 GW", as written
    release: str | None      # the earlier finding's release text
    finding_id: str


class FactRow(ContractModel):
    row_id: str                         # "K001"
    organisation: str
    attribution: FigureAttribution
    relay_host: str | None = None       # the relaying site when attribution == "relayed"
    measure: str                        # the answered target's measure, else the unit label
    period: str | None = None
    value: str                          # "10.4 GW", as written
    kind: FigureKind
    scope: str | None = None
    release: str | None = None
    finding_id: str                     # the cited finding
    duplicate_finding_ids: list[str] = Field(default_factory=list)
    earlier: list[EarlierEdition] = Field(default_factory=list)
    target_ids: list[str] = Field(default_factory=list)
    context_unchecked: bool = False


class NotFoundTarget(ContractModel):
    target_id: str
    question: str
    queries: list[str] = Field(default_factory=list)      # the sub-topic's planned queries
    pages_read: list[str] = Field(default_factory=list)   # URLs its acquisition read
    searched: bool = False                                 # the acquisition ran at all


class ReportStatement(ContractModel):   # plus (Task 1.1); Task 4.10 removes the claim fields
    finding_ids: list[str] = Field(default_factory=list)


class RejectedDraftPoint(ContractModel):   # plus (Task 1.1); Task 4.10 removes claim_ids/source_urls
    finding_labels: list[str] = Field(default_factory=list)


class ReportComposition(ContractModel):   # plus (Task 1.1)
    fact_rows: list[FactRow] = Field(default_factory=list)
    not_found: list[NotFoundTarget] = Field(default_factory=list)
    finding_labels: dict[str, str] = Field(default_factory=dict)  # label -> finding id
    # `findings` (existing field) carries the verified registry, dropped findings included
```

Changed in step 4, final shape. Task 4.1 (the step-4 contract task) adds the new types and fields and makes the renames; Task 4.10 removes the old ones (PD-21):

```python
class ReviewDefect(ContractModel):          # replaces CritiqueGap; GapKind and GapSeverity stay
    defect_id: str = Field(min_length=1)    # "review-01"
    kind: GapKind
    severity: GapSeverity
    target_ids: list[str] = Field(default_factory=list)
    statement_ids: list[str] = Field(default_factory=list)
    problem: str = Field(min_length=1)

    @property
    def material(self) -> bool:
        return self.severity in GAP_MATERIAL_SEVERITIES


StatementReviewDisposition: TypeAlias = Literal["supported", "unsupported", "not_reviewed"]


class ReportReview(ContractModel):          # Task 4.1
    status: ReportReviewStatus = "incomplete"
    dimensions: dict[str, UnitScore] = Field(default_factory=dict)
    defects: list[ReviewDefect] = Field(default_factory=list)
    per_statement_dispositions: dict[str, StatementReviewDisposition] = Field(default_factory=dict)
    reviewed_statement_ids: list[str] = Field(default_factory=list)
    unreviewed_statement_ids: list[str] = Field(default_factory=list)
    derived_defect_statement_ids: list[str] = Field(default_factory=list)
    missing_required_target_ids: list[str] = Field(default_factory=list)   # stamped by code (PD-5)
    input_fingerprint: str = ""
    composition_fingerprint: str = ""
    rubric_version: int = Field(default=REVIEW_RUBRIC_VERSION, ge=1)      # bumped to 3
    rationale: str = ""


class ReportQualitySnapshot(ContractModel):  # new fields (Task 4.1); the review fields it has today stay;
                                             # Task 4.10 removes every claim/coverage field not listed here
    required_target_ids: list[str] = Field(default_factory=list)
    answered_target_ids: list[str] = Field(default_factory=list)
    missing_required_target_ids: list[str] = Field(default_factory=list)
    unaccounted_target_ids: list[str] = Field(default_factory=list)
    verified_findings: int = Field(default=0, ge=0)
    corrected_findings: int = Field(default=0, ge=0)
    dropped_findings: int = Field(default=0, ge=0)
    context_unchecked_findings: int = Field(default=0, ge=0)
    dropped_figures: int = Field(default=0, ge=0)
    cited_findings: int = Field(default=0, ge=0)
    cited_sources: int = Field(default=0, ge=0)
    duplicate_fact_rows: int = Field(default=0, ge=0)      # an invariant: fact_rows() already merges (PD-10)
    uncited_settled_points: int = Field(default=0, ge=0)
    unresolved_citations: int = Field(default=0, ge=0)
    unjudged_sentences: list[str] = Field(default_factory=list)  # "S003": kept, no verdict, no batch failure
    refused_sentences: int = Field(default=0, ge=0)
    forecasts_without_release: int = Field(default=0, ge=0)   # PD-24: counted and printed, not a gate
    hard_failures: list[str] = Field(default_factory=list)


class ResearchState(ContractModel):         # Task 4.1 renames and adds; Task 4.10 removes
    # renamed: max_iterations -> max_extra_passes: int = Field(default=1, ge=0)
    # added:   extra_pass_target_ids: list[str] = Field(default_factory=list)   (replaced on write)
    # removed: verified_claims, claim_clusters, critique, refinement_targets,
    #          progress_history, repair_stop_reason, unique_claim_count


class ReportComposition(ContractModel):     # Task 4.1 renames max_iterations -> max_extra_passes;
    ...                                     # Task 4.10 removes claims, claim_clusters, evidence_units,
                                            # constraints, answer_rows, uncertainty_statements,
                                            # statement_dispositions, returned_to_fact_checker
    # added: statement_verdicts: dict[str, str] = {}   # statement id -> "consistent" | "corrected" |
                                            # "unchecked" (its batch failed); Task 4.3 fills it and gates on it


class EvidenceTarget(ContractModel):        # Task 4.1 removes support_policy (PD-16)
    ...
```

`ReportStatement` final fields: `statement_id`, `text`, `finding_ids` (at least one), `target_ids`. `ReportPoint` final fields: `text`, `finding_ids`, `source_urls`, `statement`. `EvidenceTarget` final fields (Task 5.1): `target_id`, `coverage_id`, `question`, `measure` (required), `unit_dimension`, `period`, `kind`, `geography`, `organisation`, `required`.

### `src/deep_research/agents/evidence.py` (Tasks 1.2 and 2.1)

```python
def cosmetic_text(text: str, *, join_hyphenation: bool = True) -> str: ...
def excerpt_matches(text: str, excerpt: str) -> bool: ...          # now cosmetic (§5.1 step 1)
def neighbouring_passage_text(read: ReadRecord, locator: str) -> str: ...   # moved from researcher
ATTRIBUTION_CUE_PATTERN: re.Pattern[str]                                    # moved; gains "source:"
def attribution_cue_adjacent(phrase: str, name_match: re.Match[str]) -> bool: ...  # moved
def relay_attribution_on_page(read: ReadRecord, locator: str, organisation: str) -> bool: ...
def own_organisation_on_page(read: ReadRecord, organisation: str) -> bool: ...     # PD-18
```

### `src/deep_research/agents/figures.py` (Task 1.2, new)

```python
@dataclass(frozen=True)
class Quantity:
    number: Decimal                 # the value with grouping removed
    unit: str                       # "kw" "mw" "gw" "tw" "kwh" "mwh" "gwh" "twh" "%" or the cosmetic unit text
    dimension: UnitDimension | None
    base: Decimal | None            # number x scale for known units, else None
    value_text: str                 # the value as written ("10,400")
    unit_text: str                  # the unit as written ("megawatts")
    start: int                      # offsets into cosmetic_text(text)
    end: int

def unit_dimension(unit: str) -> UnitDimension | None: ...
def parse_figure(value: str, unit: str) -> Quantity | None: ...
def quantities_in(text: str) -> list[Quantity]: ...
def same_quantity(left: Quantity, right: Quantity) -> bool: ...
def figure_in_text(value: str, unit: str, text: str) -> bool: ...
def bare_numbers(text: str) -> list[str]: ...                 # skips ISO and day-first dates (F2)
def dates_in(text: str) -> list[str]: ...                     # the dates a sentence states (F2)
def without_dates(text: str) -> str: ...                      # the text with its dates blanked (F2)
```

### `src/deep_research/agents/evidence_verifier.py` (Tasks 1.2 and 2.1, new)

```python
EVIDENCE_VERIFIER_NAME = "evidence_verifier"
CONTEXT_CHECK_BATCH_SIZE = 5       # D8: the default for agents.verifier_batch_size (PD-12)
CONTEXT_CHECK_CONCURRENCY = 8      # D8: the default for agents.verifier_concurrency (PD-12)
CONTEXT_PASSAGE_CHARS = 3000       # a prompt bound, not a config cap

@dataclass(frozen=True)
class FigureMatch:
    read_found: bool
    snippet_on_page: bool
    matched: tuple[bool, ...]        # one per finding.figures, in order

def read_text(read: ReadRecord) -> str: ...
def figure_match(finding: Finding, reads: Mapping[str, ReadRecord]) -> FigureMatch: ...

class FigureCheckDraft(ContractModel): ...     # provider-facing, Task 2.1
class ContextCheckDraft(ContractModel): ...    # figures: list[FigureCheckDraft]

@dataclass(frozen=True)
class ContextItem:
    label: str                 # batch-local "F01"
    finding: Finding
    read: ReadRecord
    passage: str
    match: FigureMatch
    issuer: str | None = None  # evaluated_issuer(...) for the read (PD-25)

def page_owner(read: ReadRecord) -> str: ...
def evaluated_issuer(sources: Sequence[ScoredSource], read: ReadRecord) -> str | None: ...   # PD-25
def resolve_attribution(*, proposed: FigureAttribution | None, organisation: str | None,
                        finding: Finding, read: ReadRecord, issuer: str | None) -> tuple[FigureAttribution, str]: ...
def unchecked_context(finding: Finding, figure: FindingFigure, read: ReadRecord, issuer: str | None) -> FigureContext: ...
def verify_finding(item: ContextItem, replies: Mapping[int, FigureCheckDraft] | None) -> FindingVerification: ...
class VerifiedFindings(ContractModel): findings: list[Finding]
class EvidenceVerifierAgent(BaseAgent[VerifiedFindings]): ...
```

### `src/deep_research/agents/verified_facts.py` (Task 3.1, new)

```python
@dataclass(frozen=True)
class VerifiedFigure:
    finding: Finding
    finding_id: str
    index: int
    figure: FindingFigure
    context: FigureContext
    quantity: Quantity | None
    unchecked: bool

def citable_findings(findings: Sequence[Finding]) -> list[Finding]: ...
def verified_figures(findings: Sequence[Finding]) -> list[VerifiedFigure]: ...
def same_organisation(left: str, right: str) -> bool: ...
def same_period(left: str | None, right: str | None) -> bool: ...
def finding_answers(finding: Finding, target: EvidenceTarget) -> bool: ...
def answered_target_ids(findings: Sequence[Finding], targets: Sequence[EvidenceTarget]) -> dict[str, list[str]]: ...
def release_text(finding: Finding) -> str | None: ...
def release_key(finding: Finding) -> tuple[int, int, int] | None: ...
def fact_rows(findings: Sequence[Finding], targets: Sequence[EvidenceTarget]) -> list[FactRow]: ...
def not_found_targets(sub_topics: Sequence[SubTopic], answered: Mapping[str, list[str]],
                      acquisition: Mapping[str, AcquisitionState]) -> list[NotFoundTarget]: ...
def untraced_numbers(text: str, cited: Sequence[Finding]) -> list[str]: ...
```

### `src/deep_research/agents/wording.py` (Task 3.3, new; PD-19)

```python
# moved unchanged from synthesizer.py (with the private helpers they use):
def hedge_marker(text: str) -> str: ...
def hardened_modality(text: str, corpus: str) -> str: ...
def unattested_atoms(text: str, corpus: str, raw_corpus: str = "") -> list[str]: ...
def clause_around(text: str, position: int) -> str: ...          # was _clause_around
def stated_role(text: str) -> Literal["forecast", "actual", "mixed"]: ...   # was _stated_role
# new:
def unattested_names(text: str, corpus: str, raw_corpus: str = "") -> list[str]: ...
def stated_years(text: str) -> list[str]: ...
SCOPE_TERMS: tuple[str, ...]
def stated_scopes(text: str) -> list[str]: ...
def page_modal(text: str) -> str: ...                                  # "could", "might", "may" or ""
def hedge_forecast(text: str, organisation: str, *, marker: str = "") -> str: ...   # F3
```

### `src/deep_research/agents/report.py` additions (Task 3.2)

```python
def figure_label(*, organisation: str, attribution: FigureAttribution, relay_host: str | None,
                 kind: FigureKind, release: str | None, unchecked: bool) -> str: ...
def written_citations(composition: ReportComposition) -> list[Citation]: ...
def render_written_report(composition: ReportComposition) -> str: ...
def render_finding_log(composition: ReportComposition) -> str: ...
```

### `src/deep_research/agents/report_writer.py` (Task 3.4, new; Task 4.1 moves the filename helpers in)

```python
REPORT_WRITER_NAME = "report_writer"
class WriterPointDraft(ContractModel): text: str; finding_labels: list[str]
class WriterSectionDraft(ContractModel): title: str; points: list[WriterPointDraft]
class ReportWriterDraft(ContractModel):
    executive_summary: list[WriterPointDraft]
    sections: list[WriterSectionDraft]
class ReportWriterTask(AgentTask): ...
@dataclass(frozen=True)
class PointCheck:
    reasons: tuple[str, ...]          # empty: the sentence may be printed
    forecast_as_fact: bool            # the only reasons are a forecast stated as fact or hardened (F3)
    organisation: str | None          # the forecast's organisation, for hedge_forecast
    marker: str                       # the page's own modal ("could") for hedge_forecast, or ""
class WrittenReport(ContractModel):   # the agent's result; replaces SynthesizedReport
    markdown: str; evidence_markdown: str; composition: ReportComposition
    statement_count: int; citation_count: int; refused_count: int
def finding_registry(findings: Sequence[Finding], targets: Sequence[EvidenceTarget]) -> list[tuple[str, Finding]]: ...
def writer_messages(task: ReportWriterTask) -> list[ChatMessage]: ...
def check_point(text: str, cited: Sequence[Finding], *, geographies: Sequence[str],
                sources: Sequence[ScoredSource] = ()) -> PointCheck: ...
def compose_written_report(task: ReportWriterTask, draft: ReportWriterDraft | None) -> ReportComposition: ...
def finding_memory_payload(finding: Finding, *, session_id: str) -> tuple[str, dict[str, JsonValue]]: ...
class ReportWriterAgent(BaseAgent[WrittenReport]): ...   # also the terminal ReportPublisher
def report_filename(*, session_id: str, iteration: int) -> str: ...            # moved by Task 4.1
def evidence_report_filename(*, session_id: str, iteration: int) -> str: ...   # moved by Task 4.1
def quality_report_filename(*, session_id: str, iteration: int) -> str: ...    # moved by Task 4.1
```

### Graph, runtime and entry points (Task 4.8; the CLI and API side is Task 4.6)

```python
# graph/state.py
PLANNER_NODE = "planner"; RESEARCHER_NODE = "researcher"; SOURCE_EVALUATOR_NODE = "source_evaluator"
EVIDENCE_VERIFIER_NODE = "evidence_verifier"; REPORT_WRITER_NODE = "report_writer"
REPORT_REVIEWER_NODE = "report_reviewer"; EXTRA_PASS_NODE = "extra_pass"; FINALIZE_NODE = "finalize_report"
ROUTE_EXTRA_PASS = "extra_pass"; ROUTE_FINALIZE = "finalize"; ROUTE_END = "end"
GRAPH_ROUTES: dict[str, str]   # report_accepted, report_not_accepted, review_unavailable,
                               # extra_pass_requested, extra_passes_exhausted, halted
def graph_route(state: ResearchState) -> tuple[str, str]: ...
def graph_status(state: ResearchState) -> str: ...            # completed | max_iterations | incomplete | failed
def graph_quality_status(state: ResearchState) -> str: ...
def initial_graph_state(*, session_id: str, question: str, max_extra_passes: int = 1,
                        memory_context: MemorySnapshot | None = None) -> ResearchGraphState: ...

# graph/nodes.py
def report_writer_node(agent: ResearchAgent, *, node_name: str = REPORT_WRITER_NODE) -> GraphNode: ...
def report_reviewer_node(reviewer: ReportReviewerLike | None) -> GraphNode: ...
async def extra_pass_node(channel: ResearchGraphState) -> ResearchGraphState: ...
def route_after_review(channel: ResearchGraphState) -> str: ...
class ReportPublisher(Protocol):                 # keyword-only, as today; publish_claim is renamed
    async def publish_document(self, *, filename: str, content: str) -> ToolResult: ...
    async def publish_finding(self, *, content: str, metadata: Mapping[str, JsonValue]) -> ToolResult: ...

# main.py (called by cli.py and api/sessions.py, Task 4.6)
async def run_research(question: str | None = None, *, ..., max_extra_passes: int | None = None, ...) -> ResearchOutcome: ...
    # every other parameter unchanged; `max_iterations=` becomes `max_extra_passes=`,
    # and its default is read from `settings.graph.max_extra_passes`
```

CLI and API (PD-15): the flag `--max-iterations N` and the request field `max_iterations` keep their names and pass `max_extra_passes=N`. The graph status value `max_iterations` (PD-11, PD-23) means "extra passes spent, required targets still missing, and the report not accepted"; an accepted report with targets under Not found finishes `completed`.

## File map

| Phase | Create | Modify | Delete |
|---|---|---|---|
| 0 | `scratch/run_live_proof.py` (copy, untracked), `scratch/baseline-*.txt` | — | — |
| 1 | `agents/figures.py`, `agents/evidence_verifier.py`, `agents/wording.py` (Task 3.3, wave 1B), `tests/evidence_fakes.py`, `tests/test_agents/test_figures.py`, `tests/test_agents/test_evidence_verifier.py`, `tests/test_agents/test_wording.py`, `scratch/ev_rebuild_audit2.py` | `utils/types.py`, `utils/__init__.py`, `agents/identity.py`, `agents/evidence.py`, `agents/researcher.py`, `agents/planner.py`, `agents/synthesizer.py`, `agents/__init__.py`, `e2e_evaluation/replay.py`, `config.yaml`, tests | — |
| 2 | `agents/verified_facts.py` and `tests/test_agents/test_verified_facts.py` (Task 3.1), `tests/test_agents/test_report_layout.py` (Task 3.2), `scratch/ev_verify_audit2.py` | `agents/evidence.py`, `agents/researcher.py`, `agents/evidence_verifier.py`, `agents/report.py` (Task 3.2), `agents/__init__.py`, tests | — |
| 3 | `agents/report_writer.py`, `tests/test_agents/test_report_writer.py`, `scratch/ev_compose_audit2.py` | `agents/__init__.py` | — |
| 4 | `agents/report_reviewer.py` and `tests/test_agents/test_report_reviewer.py` (git mv), `evaluation/cases/evidence_verifier.py`, `evaluation/cases/report_writer.py` (git mv), `tests/test_evaluation/test_cases_evidence_verifier.py`, `tests/test_evaluation/test_cases_report_writer.py` (git mv), `tests/test_e2e_evaluation/test_replay_doubles.py`, `observability/run_telemetry.py` and `tests/test_observability_run_telemetry.py` | `utils/*`, `config.yaml`, `agents/{quality,report,report_writer,researcher,planner,evidence,identity,prompts,__init__}.py`, `graph/*`, `runtime/*`, `main.py`, `cli.py`, `api/*`, `providers/*`, `evaluation/*`, `e2e_evaluation/*`, `README.md`, tests | `agents/fact_checker.py`, `agents/claim_clusters.py`, `agents/critic.py`, `agents/synthesizer.py`, `utils/claims.py`, `evaluation/cases/fact_checker.py`, `evaluation/cases/critic.py`…
| 5 | `scratch/ev_plan_probe.py` | `utils/types.py`, `tests/evidence_fakes.py`, `agents/planner.py`, `agents/researcher.py`, `agents/report_reviewer.py`, `evaluation/cases/planner.py`, `evaluation/evaluators.py`, `e2e_evaluation/{replay,replay_matrix}.py`, `agents/__init__.py`, tests | — |
| 6 | — | `scratch/run_live_proof.py` | — |

(`agents/` means `src/deep_research/agents/`; likewise for `utils/`, `graph/`, `runtime/`, `api/`, `evaluation/`, `e2e_evaluation/`.)

## Caller inventory (what step 4 must migrate)

Grepped in this worktree on 2026-09-24 (`(from|import) … (fact_checker|claim_clusters|critic|synthesizer|report_review)`). A task that deletes a module first re-runs the grep in its own Step 1 and migrates every hit it prints, including any added since.

| Deleted or renamed module | Importers outside the module and its own tests | Owning task |
|---|---|---|
| `agents/fact_checker.py` | `agents/__init__.py:198-293`; `e2e_evaluation/replay.py:45-49`; `evaluation/dependencies.py:38`; `evaluation/evaluators.py:24-27`; `evaluation/cases/__init__.py:16`; `runtime/assembly.py:18`; tests: `test_agents/test_evidence_quality_seam.py:17`, `test_agents/test_native_react_boundary.py:18`, `test_agents/test_source_evaluator.py:17`, `test_agents/test_synthesis_seam.py:25`, `test_agents/test_tool_free_prompts.py:40`, `test_config.py:773`, `test_e2e_evaluation/test_real_agents.py:868,1241`, `test_evaluation/test_cases_critic.py:15`, `test_evaluation/test_cases_registry.py:9` | 4.8 (assembly), 4.7 (evaluation), 4.9 and 4.11 (e2e), 4.1 (`test_config.py`), 4.10 (the rest, and the module) |
| `agents/claim_clusters.py` | `agents/__init__.py:31-71`; `agents/fact_checker.py:45`; `agents/planner.py:23`; `agents/synthesizer.py:40`; `utils/types.py:2630,4066`; `e2e_evaluation/replay.py:42`; tests: `test_agents/test_planner.py:14`, `test_state.py:1565` | 4.4 (planner and its tests), 4.9 (e2e), 4.10 (the rest, `utils/types.py`, and the module) |
| `agents/critic.py` | `agents/__init__.py:72-197`; `agents/report_review.py:50,1205,1558`; `graph/state.py:30`; `utils/types.py:1601`; `e2e_evaluation/replay.py:43`; `evaluation/dependencies.py:37`; `evaluation/evaluators.py:23`; `evaluation/cases/critic.py:8`; `runtime/assembly.py:16`; tests: `graph_fakes.py:19`, `test_agents/test_planner.py:21`, `test_agents/test_report_review.py:22,753`, `test_agents/test_synthesis_seam.py:18`, `test_agents/test_tool_free_prompts.py:30`, `test_cli/test_render.py:7`, `test_cli/test_report_quality_acceptance.py:51`, `test_e2e_evaluation/test_real_agents.py:867`, `test_evaluation/test_cases_critic.py:9`, `test_evaluation/test_evaluators_agents.py:5`, `test_graph/test_orchestrator.py:10`, `test_graph/test_state.py:9` | 4.2 (reviewer and its tests), 4.4 (`test_planner.py`), 4.6 (CLI tests), 4.7 (evaluation), 4.8 (graph, assembly, `graph_fakes.py`), 4.9 and 4.11 (e2e), 4.10 (the rest, and the module) |
| `agents/synthesizer.py` | `agents/__init__.py:616-660`; `graph/nodes.py:49-55`; `e2e_evaluation/cases.py:39`; `e2e_evaluation/replay.py:70`; `evaluation/dependencies.py:47`; `runtime/assembly.py:26`; tests: `test_agents/test_fact_checker.py:110`, `test_agents/test_synthesis_seam.py:45`, `test_agents/test_synthesizer.py:39`, `test_agents/test_tool_free_prompts.py:74`, `test_e2e_evaluation/test_real_agents.py:872`, `test_evaluation/test_config.py:11`, `test_graph/test_nodes.py:15`, `test_graph/test_orchestrator.py:16` | 3.3 (wording rules move out), 4.1 (filename helpers move out), 4.7 (evaluation), 4.8 (graph, assembly), 4.9 and 4.11 (e2e), 4.10 (the rest, and the module) |
| `agents/report_review.py` (renamed `report_reviewer.py`) | `agents/__init__.py:477`; `agents/report.py:3690`; `graph/nodes.py:44`; `graph/state.py:31`; `runtime/assembly.py:20`; `utils/types.py:4134`; `e2e_evaluation/evaluators.py:21`; `e2e_evaluation/replay.py:58`; tests: `test_agents/test_quality.py:681`, `test_agents/test_report_review.py` (whole file), `test_cli/test_report_quality_acceptance.py:80`, `test_graph/test_nodes.py:14`, `test_graph/test_orchestrator.py:1090` | 4.1 (the rename and every import line) |

Deleted `utils/types.py` symbols and their acceptance grep. Task 4.10 runs it over everything except `src/deep_research/e2e_evaluation` and `tests/test_e2e_evaluation` (Task 4.11 runs it over those two); Gate G4 runs it over everything. It must print nothing:

```bash
grep -rnwE "Claim|ClaimVerdict|ClaimProvenance|ClaimCluster|AtomicProposition|ConflictAssessment|EvidencePassage|SubjectState|StatementMode|SUBSTANTIVE_STATEMENT_MODES|ANSWERING_STATEMENT_MODES|EVIDENCE_BADGE_LABELS|statement_mode_for_claims|clusters_for_claims|derive_statement|statement_for_point|statement_claims|statement_claims_by_cluster|statement_satisfies_support_policy|answering_statement_for|target_is_answered|unanswered_required_targets|qualifier_matches_requirement|answered_atom_dimensions|answered_required_dimensions|required_dimensions_for_targets|dimensions_by_target|SubstantiveCoverage|Critique|CritiqueGap|CritiqueReviewStatus|CriticScore|RepairAction|REPAIR_ACTIONS|QUERY_BEARING_REPAIR_ACTION|gap_contract_problem|RefinementTarget|RefinementOrigin|RepairStopReason|REPAIR_STOP_REASONS|ResearchProgress|progress_improved|sub_topic_owes_evidence|ReportConstraint|ReportAnswerRow|MAX_CONSUMED_FINDING_FINGERPRINTS|MAX_CONSUMED_COVERAGE_IDS|verified_claims|claim_clusters|refinement_targets|repair_stop_reason|progress_history|unique_claim_count" src tests
```

(`claim_fingerprint` in `agents/identity.py` survives only if a surviving module still reads it after Task 4.10; Task 4.10 checks with `grep -rn claim_fingerprint src tests`.)

---

## Execution model: waves, ownership and reviews

The user asked for maximum parallel dispatch of implementation and review. Run the plan with superpowers:subagent-driven-development (briefs, one task review per task, fix rounds with scoped re-reviews, the ledger, the final whole-branch review), with these rules on top. **Ruling for the controller:** the skill's "never dispatch multiple implementation subagents in parallel" is overridden by the user's instruction; rules R1–R5 remove the conflict it guards against.

### Rules

- **R1 One worktree per implementation task, named absolutely in every brief (F12).** The controller records `BASE=$(git -C "$W" rev-parse HEAD)` and creates `../ev-<id>` on branch `ev/<id>` (see "How to run things"). The implementer works and commits only there. Harness tasks and gates run in `$W` itself, because `scratch/` is untracked and exists only there. Every brief (implementer, reviewer and fix round) opens with the absolute path of its tree (`C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.worktrees/ev-<id>` for a task, `$W` for a harness or gate) and says that every read, grep, glob and edit takes an absolute path under it: a relative path resolves against the session's directory, which is the main checkout, where `src/deep_research/utils/types.py` has 510 lines and none of this plan's modules exist. The brief's first action is a tree check: `git -C <tree> rev-parse --abbrev-ref HEAD` prints `ev/<id>` (or `codex/evidence-verifier-pipeline` for `$W`), `<tree>/docs/superpowers/plans/2026-09-24-evidence-verifier-pipeline.md` exists, and, before Task 4.10, `wc -l <tree>/src/deep_research/utils/types.py` prints at least 4158. A brief whose check fails stops and reports; it edits nothing.
- **R2 Owned files.** Each task lists the files it owns and edits nothing else. A failure it meets in a file it does not own is reported to the controller (test id and the first lines of the traceback), not fixed.
- **R3 Shared files have one owner per phase** (table below). Parallel tasks import new code from its module (`from deep_research.agents.figures import ...`), never from the `deep_research.agents` package, and do not run `tests/test_imports.py` or `tests/test_evaluation/test_config.py`; the phase's integration task does.
- **R4 Merge on DONE, review in parallel.** When an implementer reports DONE, the controller merges its branch at once (`git -C "$W" merge --no-ff ev/<id> -m "merge: Task N.M"`) so dependent tasks can start, and dispatches the task review on `BASE..ev/<id>` (the skill's `scripts/review-package PLAN_FILE BASE ev/<id>`, run in `$W`). Reviews of wave N run while wave N+1 implements.
- **R5 File lock.** A task's owned files stay locked from its dispatch until its review is clean (or parked at the fix-round cap). A task is dispatched only when every task it depends on is merged and none of its owned files is locked. The wave tables name the few places where this makes a task wait for a review.
- **R6 Fix rounds** run in the task's own worktree (resume the implementer), are committed on its branch, merged again, and re-reviewed on the fix range only. A finding that needs a change in a file another running task owns goes to that owner through `hub`; a finding in a file nobody owns goes to the phase's integration task.
- **R7 No merge during a harness or a gate.** The controller does not merge into `$W` while a harness task or gate command runs there; merges wait for it.
- **R8 Gates are barriers.** A gate runs when every task of its phase is merged and review-clean. No task of the next phase is dispatched before the gate passes (spec §9: "Each step's proof must pass before the next step starts"), with one approved exception: Task 3.3 runs in wave 1B (it only moves `synthesizer.py` helpers into `wording.py`), and Tasks 3.1 and 3.2 run in wave 2A beside Task 2.1 (they need only Task 1.1's types and Task 1.2's `figures.py`). The controller merges 3.1 and 3.2 into the plan branch only after Gate G2 passes, so G2 tests the Phase-2 tree alone and `tests/test_imports.py` stays green; their proof is Gate G3, and Task 3.4 still waits for G2. A fix merged after a gate passed re-runs that gate's offline commands.
- **R9 Dispatch by readiness.** Waves are the planning view. A task is dispatched as soon as R5 allows, even while other tasks of its wave run.
- **R10 Cleanup.** After a clean review: `git -C "$W" worktree remove "../ev-<id>" && git -C "$W" branch -d "ev/<id>"`.

### Shared-file boundaries

| Shared file | Its single owner in each phase | Contract fixed before the wave |
|---|---|---|
| `src/deep_research/utils/types.py`, `src/deep_research/utils/__init__.py` | 1.1; 4.1 (additions and renames), then 4.10 (removals) and 4.14 (`ResearchState.run_telemetry`); 5.1 | "Shared interfaces", `utils/types.py` blocks |
| `tests/evidence_fakes.py` | 1.1; 4.1; 5.1 | the builders in Task 1.1 |
| `src/deep_research/agents/__init__.py` (re-exports) | 1.5; 2.2; 3.5; 4.10; 5.4 | none needed: names are exported after the wave that creates them |
| `tests/test_evaluation/test_config.py` (fingerprint pins) | 1.5 (researcher, planner, synthesizer); 2.2; 4.7 (agent names), then 4.10 (pins); 5.4 | PD-17 |
| `config.yaml`, `src/deep_research/utils/config.py`, `tests/test_config.py` | 1.3; 4.1 | the config block in Task 4.1 (the four §7.3 caps included) |
| Graph wiring: `graph/*.py`, `runtime/assembly.py`, `runtime/__init__.py`, `main.py` | 4.8, then 4.14 (the telemetry collector only) | node names, routes, `ReportPublisher`, `run_research` in "Shared interfaces" |
| `src/deep_research/providers/*.py` | 4.14 | `RunTelemetryCollector`, the retry loop's 429 accounting and `_record_tokens` |
| `src/deep_research/cli.py` | 4.6, then 4.14 (one summary line) | Task 4.14's Telemetry line and advice format |
| `src/deep_research/e2e_evaluation/replay.py`, `replay_matrix.py` | 1.3; 4.9, then 4.11; 5.3 | request formats: Task 2.1 (Context Check), Task 3.4 (writer registry lines), Task 4.2 (reviewer) |
| `src/deep_research/agents/report.py` | 3.2 (wave 2A); 4.5, then 4.10 and 4.14 (the telemetry block) | |
| `src/deep_research/agents/synthesizer.py` | 3.3 (wave 1B); 4.1 (filename helpers out); 4.10 (deletes it) | the `wording.py` block of "Shared interfaces" |
| `src/deep_research/agents/evidence_verifier.py` | 1.2; 2.1; 4.13 (its two config reads) | the `evidence_verifier.py` block of "Shared interfaces"; PD-12 |
| `src/deep_research/agents/researcher.py` | 1.3; 2.1; 4.4, then 4.13 and 4.10; 5.3 | |
| `README.md` | 4.6 | the node, route, flag and case names in "Shared interfaces" and Task 4.9 |

### Waves

Sizes: S about 20 minutes of implementation, M 45, L 75, XL 120.

| Wave | Task | Role | Starts when | Owns | Size |
|---|---|---|---|---|---|
| 0A | 0.1 Stage the runner, record the baseline | sp-implementer | now | `scratch/` only | S |
| 1A | 1.1 Contracts: finding, verification and report types | sp-hard-implementer | 0.1 done | `utils/types.py`, `utils/__init__.py`, `agents/identity.py`, `tests/evidence_fakes.py`, `tests/test_types.py`, `tests/test_agents/test_identity.py` | L |
| 1B | 1.2 Cosmetic matching, figure normalisation, Figure Match | sp-implementer | 1.1 merged | `agents/evidence.py`, `agents/figures.py`, `agents/evidence_verifier.py`, `tests/test_agents/test_evidence.py`, `tests/test_agents/test_figures.py`, `tests/test_agents/test_evidence_verifier.py` | L |
| 1B | 1.3 Researcher persists evidence; budget 20; own page first | sp-implementer | 1.1 merged | `agents/researcher.py`, `e2e_evaluation/replay.py`, `config.yaml`, `tests/test_config.py`, `tests/test_agents/test_researcher.py`, `tests/test_agents/test_planner_researcher_seam.py`, `tests/test_agents/test_evidence_quality_seam.py`, `tests/test_agents/test_acquisition.py`, `tests/test_evaluation/conftest.py` | L |
| 1B | 1.4 Planner emits structured target fields | sp-implementer | 1.1 merged | `agents/planner.py`, `tests/test_agents/test_planner.py` | M |
| 1B | 3.3 Shared wording rules (R8 exception) | sp-hard-implementer | 1.1 merged | `agents/wording.py`, `agents/synthesizer.py`, `tests/test_agents/test_wording.py` | L |
| 1C | 1.5 Phase-1 integration: exports and pins | sp-implementer | 1.2, 1.3, 1.4, 3.3 merged | `agents/__init__.py`, `tests/test_evaluation/test_config.py`, and any test file routed to it under R6 | S |
| 1C | 1.6 Step-1 proof harness and live extraction probe | sp-implementer; `--live` by the operator | 1.2, 1.3 merged | `scratch/ev_rebuild_audit2.py` | M |
| 2A | 2.1 Context Check and the Evidence Verifier agent | sp-hard-implementer | G1 | `agents/evidence.py`, `agents/researcher.py`, `agents/evidence_verifier.py`, `tests/test_agents/test_evidence_verifier.py`, `tests/test_agents/test_evidence.py`, `tests/test_agents/test_researcher.py`, `tests/test_agents/test_tool_free_prompts.py` | XL |
| 2A | 3.1 Verified facts (R8 exception) | sp-hard-implementer | G1; merged only after G2 passes | `agents/verified_facts.py`, `tests/test_agents/test_verified_facts.py` | L |
| 2A | 3.2 Reader report and evidence log layout (R8 exception) | sp-implementer | G1; merged only after G2 passes | `agents/report.py`, `tests/test_agents/test_report_layout.py` | L |
| 2B | 2.2 Phase-2 integration | sp-implementer | 2.1 merged | `agents/__init__.py`, `tests/test_evaluation/test_config.py` | S |
| 2B | 2.3 Step-2 proof harness | sp-implementer; `--live` by the operator | 2.1 merged | `scratch/ev_verify_audit2.py` | M |
| 3B | 3.4 The Report Writer agent | sp-hard-implementer | G2 passed and 3.1, 3.2 merged (3.3 merged in 1B) | `agents/report_writer.py`, `tests/test_agents/test_report_writer.py` | XL |
| 3C | 3.5 Phase-3 integration | sp-implementer | 3.4 merged | `agents/__init__.py` | S |
| 3C | 3.6 Step-3 proof harness | sp-implementer; `--live` by the operator | 3.4 merged | `scratch/ev_compose_audit2.py` | L |
| 4A | 4.1 Contract and mechanical renames | sp-hard-implementer | G3 | `utils/types.py`, `utils/__init__.py`, `utils/config.py`, `config.yaml`, `tests/test_config.py`, `tests/test_types.py`, `tests/test_state.py`, `tests/evidence_fakes.py`, `agents/report_writer.py`, `agents/synthesizer.py`, `tests/test_agents/test_report_writer.py`, `tests/test_agents/test_synthesizer.py`, the renamed `agents/report_reviewer.py` and `tests/test_agents/test_report_reviewer.py`, and import lines only in the files its Step 1 grep lists | L |
| 4B | 4.2 Report Reviewer | sp-hard-implementer | 4.1 merged | `agents/report_reviewer.py`, `tests/test_agents/test_report_reviewer.py` | XL |
| 4B | 4.3 Quality gates | sp-hard-implementer | 4.1 merged | `agents/quality.py`, `tests/test_agents/test_quality.py` | L |
| 4B | 4.4 Researcher and planner: targeted extra pass | sp-hard-implementer | 4.1 merged | `agents/researcher.py`, `agents/planner.py`, `tests/test_agents/test_researcher.py`, `tests/test_agents/test_planner.py`, `tests/test_agents/test_planner_researcher_seam.py` | L |
| 4B | 4.5 Quality record | sp-implementer | 4.1 merged | `agents/report.py`, `tests/test_agents/test_report.py` | M |
| 4B | 4.6 Outcome, API, CLI and README | sp-hard-implementer | 4.1 merged | `runtime/outcome.py`, `api/*.py`, `cli.py`, `README.md`, `tests/test_api/*`, `tests/test_cli/*`, `tests/test_runtime/test_outcome.py` | L |
| 4B | 4.7 Per-agent evaluation | sp-hard-implementer | 4.1 merged | `evaluation/**`, `tests/test_evaluation/**` | XL |
| 4C | 4.8 Graph and runtime cutover | sp-hard-implementer | 4.2, 4.3, 4.4 merged | `graph/*.py`, `runtime/assembly.py`, `runtime/__init__.py`, `runtime/recall.py`, `runtime/memory_bridge.py`, `runtime/errors.py`, `main.py`, `tests/graph_fakes.py`, `tests/research_fakes.py`, `tests/test_graph/*`, `tests/test_runtime/*` except `test_outcome.py` | XL |
| 4C | 4.9 E2E replay doubles and cases | sp-hard-implementer | 4.2 merged | `e2e_evaluation/replay.py`, `e2e_evaluation/replay_matrix.py`, `tests/test_e2e_evaluation/test_replay_doubles.py` | L |
| 4C | 4.13 Parallel sub-topics and scoring batches (D9) | sp-hard-implementer | 4.4 merged and its review clean (R5: same files; 4.1 and 2.1 merged) | `agents/researcher.py`, `agents/react.py`, `agents/base.py`, `agents/source_evaluator.py`, `agents/evidence_verifier.py` (its two config reads), `tests/agent_fakes.py` (the target-keyed completer), `tests/test_agents/test_researcher.py`, `tests/test_agents/test_react.py`, `tests/test_agents/test_source_evaluator.py`, `tests/test_agents/test_evidence_verifier.py` | L |
| 4D | 4.10 Deletion sweep, exports and pins | sp-hard-implementer | 4.2–4.8 and 4.13 merged; waits (R5) for the reviews of 4.2–4.5, 4.7 and 4.13 | deleted files; `agents/__init__.py`, `utils/types.py`, `utils/__init__.py`, `utils/claims.py`, `agents/prompts.py`, `agents/evidence.py`, `agents/identity.py`; dead names in `agents/{quality,report,report_writer,report_reviewer,planner,researcher,wording,figures,verified_facts}.py`; `tests/test_imports.py`, `tests/test_types.py`, `tests/test_state.py`, `tests/test_evaluation/test_config.py`, `tests/test_agents/{test_evidence,test_identity,test_prompts,test_report,test_tool_free_prompts,test_native_react_boundary,test_source_evaluator,test_wording,test_figures,test_verified_facts}.py` | L |
| 4D | 4.11 E2E matrix green; graph-historical harness retired | sp-hard-implementer | 4.8 and 4.9 merged; waits (R5) for the review of 4.9 | `e2e_evaluation/{cases,evaluators,models,runner,replay,replay_matrix}.py`, `tests/test_e2e_evaluation/*` | L |
| 4D | 4.12 Reviewer acceptance probe | sp-implementer; `--live` by the operator | 4.2, 4.3 and 4.8 merged | `scratch/ev_review_audit2.py` | M |
| 4D | 4.14 Concurrency and budget telemetry (spec §7.3) | sp-implementer | 4.5, 4.6, 4.8 and 4.10 merged and their reviews clean (R5: their files) | `observability/run_telemetry.py` (new), `observability/__init__.py`, `providers/{retry,deepseek_provider,openai_provider,factory}.py`, `utils/types.py` (one field), `agents/report.py` (the telemetry block), `cli.py` (one summary line), `runtime/assembly.py`, `graph/nodes.py`, `tests/test_observability_run_telemetry.py` (new), `tests/test_retry_policy.py`, `tests/test_cli/test_render.py`, `tests/test_agents/test_report.py` | M |
| 5A | 5.1 Contract: the final target fields | sp-implementer | G4 | `utils/types.py`, `utils/__init__.py`, `tests/evidence_fakes.py`, `tests/test_types.py` | M |
| 5B | 5.2 Planner: the question's targets only | sp-hard-implementer | 5.1 merged | `agents/planner.py`, `tests/test_agents/test_planner.py` | L |
| 5B | 5.3 Target consumers | sp-implementer | 5.1 merged | `agents/researcher.py`, `agents/report_reviewer.py`, `evaluation/cases/planner.py`, `evaluation/evaluators.py`, `e2e_evaluation/replay.py`, `e2e_evaluation/replay_matrix.py`, `tests/test_agents/test_researcher.py`, `tests/test_agents/test_report_reviewer.py`, `tests/test_agents/test_planner_researcher_seam.py`, `tests/test_evaluation/test_cases_planner.py`, `tests/test_evaluation/test_evaluators_agents.py`, `tests/test_e2e_evaluation/test_real_agents.py` | M |
| 5C | 5.4 Phase-5 integration | sp-implementer | 5.2, 5.3 merged | `agents/__init__.py`, `tests/test_evaluation/test_config.py` | S |
| 5C | 5.5 Step-5 proof: plan probe | sp-implementer; `--live` by the operator | 5.2 merged | `scratch/ev_plan_probe.py` | M |
| 6A | 6.1 Live-proof labels | sp-implementer | G5 | `scratch/run_live_proof.py` | S |
| 6A | Final whole-branch review (the skill's final review) | most capable reviewer, read-only | G5 | — | |
| 6B | 6.2 Capped live pre-flight | operator | 6.1 done, off-peak | — | |
| 6C | 6.3 The live run `ev-1` | operator | 6.2 passed, final-review fixes merged and G4/G5 offline commands re-run, off-peak | — | |
| 6D | 6.4 Independent audit against spec §10 | fresh read-only reviewer | 6.3 done | — | |

Parallel slots at the peak: wave 4B runs six implementers beside the review of 4.1, then up to six reviews beside 4.8, 4.9 and 4.13 (at most 14 agents at once, under the cap of 32).

### Critical path and wall-clock estimate

Assumptions: the sizes above; a task review takes 15 minutes (S, M) or 25 (L, XL); a fix round (fix plus scoped re-review) takes 25–40 minutes; the full suite takes about 10 minutes (Task 0.1 measures it) and the e2e matrix about 15 minutes at three repetitions. "Planned" assumes one fix round per phase. "Realistic" follows the plan audit (`agent://FablePlanAudit`): two to three extra fix rounds on each XL critical-path task and on Task 4.11, one more round elsewhere, and a gate re-run per phase. Off-peak waits are not counted in either column.

| Phase | Critical path | Planned (min) | Realistic (min) |
|---|---|---|---|
| 0 | 0.1 | 45 | 45 |
| 1 | 1.1 (75) → 1.3 (75; 1.2, 1.4 and 3.3 beside it) → 1.6 with its `--live` probe (60; 1.5 beside it) → last review 15 → fix round 25 → G1 25 | 275 | 315 |
| 2 | 2.1 (120; 3.1 and 3.2 beside it, the R8 exception) → 2.3 (45; 2.2 beside it) → last review 15 → fix round 25 → G2 30 | 235 | 305 |
| 3 | 3.4 (120; 3.1 and 3.2 merged just after G2) → 3.6 (75; 3.5 beside it) → last review 25 → fix round 25 → G3 25 | 270 | 340 |
| 4 | 4.1 (75) → 4.2 (120; 4.3–4.7 beside it) → 4.8 (120; 4.9 and 4.13 beside it) → 4.11 (75; 4.10, 4.12 and 4.14 beside it; 4.14 starts after 4.10's review) → last review 25 → fix round 25 → G4 (the matrix once, and the reviewer probe) 42 | 517 | 767 |
| 5 | 5.1 (45) → 5.2 (75; 5.3 beside it) → 5.5 (45; 5.4 beside it) → last review 15 → fix round 25 → G5 25 | 230 | 270 |
| 6 | the final review and its fixes (90; 6.1 beside them) → 6.2 (25) → 6.3 (45) → 6.4 (40) | 200 | 260 |
| **Total** | | **1,772 ≈ 29.5 hours** | **2,302 ≈ 38 hours** |

Plan with the realistic figure: **36–40 hours of agent wall time** (two extra rounds instead of three on 4.8 and 4.11 give about 36 hours; one more round on each XL task, about 40), **plus off-peak waits**: the live steps of G1 (researcher probe), G2, G3, G4 (reviewer probe), G5, 6.2 and 6.3 each wait up to 3 hours if they meet a peak window (Mon–Fri 01:00–04:00 and 06:00–10:00 UTC), so schedule them off-peak in advance. The approved R8 exception (Task 3.3 in wave 1B; Tasks 3.1 and 3.2 in wave 2A) takes about 75 minutes off Phase 3. Run one task at a time with no review overlapping any implementation, the planned figures come to about 3,300 minutes (55 hours); the waves save about half.

## Runtime budget: how one report takes about 30 minutes (45 at most)

Measured baseline (audit-3, `output/live-proof/audit-3/cli.log`, first pass): planner 2 m 30 s, researcher 7 m 45 s (budget 10), source evaluator 1 m 20 s, fact checker 51 m 28 s, synthesizer 4 m 58 s, critic 2 m 19 s, report review 3 m 43 s; the second pass added 1 h 26 m (fact checker 1 h 1 m 28 s); total 2 h 40 m, 86 web calls. The fact checker and the critic go; research gets a bigger budget but a smaller plan.

| Stage | Pass-0 work | Budget | What bounds it |
|---|---|---|---|
| Planner | 1 plan request plus at most 2 plan-review calls | 3 min | `MAX_PLAN_REVIEW_CALLS = 2`; `planner_final_max_tokens` 65,536; five or fewer required targets (step 5) |
| Researcher | at most 5 planned sub-topics, run concurrently (§7.2, D9: at most `agents.sub_topic_concurrency` = 5 in flight, one run-wide tool lock), each ≤ 7 model turns and ≤ 20 tool calls | 4 min | the slowest sub-topic, not the sum: ≈ 8 attempts × 13–18 s + ≈ 9 s tools ≈ 1.9–2.6 min, plus whatever 5 in-flight decision calls add (unmeasured; bounded by Task 6.2's per-turn mean ≤ 27 s); `agents.max_iterations: 7`, `agents.max_sub_topics: 5`, `agents.tool_budget_overrides.researcher: 20` (F1) |
| Source Evaluator | 1–3 batched calls (batch 12, at most 36 sources), run concurrently (D9: at most `agents.source_scoring_concurrency` = 3) | 1 min | `agents.source_evaluator.batch_size` 12 and `max_total_sources` 36; each batch's failure stands alone (Task 4.13) |
| Evidence Verifier | Figure Match (milliseconds), then about 30 findings in 6 Context Check batches of `agents.verifier_batch_size` (5), at most `agents.verifier_concurrency` (8) in flight | 2 min | the batch size and the concurrency are config (§7.3, PD-12); a truncated batch is asked once more in two halves; a failed batch marks its findings `context_unchecked` and the run goes on |
| Report Writer | 1 draft request; a second only after a truncated one; then one Statement Check call per 5 drafted sentences (2 calls for a typical report) | 4 min | two attempts (the profile's effort, then high after a truncation, F10); code builds the key facts table, Not found and the labels, so the model writes only prose; the Statement Check shares `agents.verifier_batch_size`/`agents.verifier_concurrency` |
| Report Reviewer | 1 request; a second only after a truncated one | 4 min | `report_review_max_tokens` 65,536; `report_reviewer` timeout 360 s, `retry_count` 1; the evidence-batch follow-up calls are gone (§6.3: one call) |
| Publish | three file writes | seconds | |
| **First pass** | | **≈ 14–17 min** | |
| Extra pass, only when a required target has no verified finding | researcher on the sub-topics that own the missing targets only (usually 1–2, concurrent, ≈ 2.5 min), then source evaluator, Evidence Verifier on the new findings only, writer and Statement Check, reviewer | ≈ 10 min | `graph.max_extra_passes: 1`; targeted to the missing target ids (§6.5, §7.2) |
| **With the extra pass** | | **≈ 24–28 min** | under the 30-minute target even when a pass fires; if Task 6.2 projects a first pass over 30 minutes or a researcher per-turn mean over 27 s, lower `agents.sub_topic_concurrency` to 3 before `ev-1` without re-running the pre-flight (the sequential budget already fits 45 min); `agents.max_iterations: 6` (review item 15) is the last resort |

Failures never add a pass and never stop a run: a failed Context Check batch leaves its findings citable as "unchecked context"; a failed writer draft still publishes the code-built key facts table, Not found and sources; an unscored review publishes as `partial` with status `incomplete` (PD-13). Task 6.1 reads each stage's duration from `cli.log` and the researcher's slowest sub-topic and per-turn mean from the `sub_topic.completed` events' `elapsed_s` (Task 4.13); the live run starts only if the capped pre-flight passes Task 6.2's criteria: quality accepted, a projected first pass of at most 30 minutes, a researcher per-turn mean of at most 27 s, the Evidence Verifier, Report Writer and Report Reviewer each within 1.5 times their budget above, no failed Context Check batch, no forecast without release, zero unrecovered 429s and no truncated call. What can still break the budget: a reviewer timeout (360 s, `retry_count` 1: up to 12 minutes, then published as partial), a truncated writer draft (+5 minutes) or a truncated Context Check batch (+1–2 minutes for the halves).

---

## Phase 0 — Preparation

### Task 0.1: Stage the live-proof runner and record the baseline

**Role:** sp-implementer. Runs in `$W` (no task worktree: it changes nothing tracked). **Depends on:** nothing.

**Files:**
- Create (untracked copy): `scratch/run_live_proof.py`
- Create (untracked): `scratch/baseline-suite.txt`, `scratch/baseline-state.txt`, `scratch/baseline-e2e.txt`, `scratch/baseline-times.txt`

`scratch/` is gitignored in this repo ("must stay untracked"), so the new worktree has no copy of the live-proof runner; Phase 6 needs it. The baseline is needed at every gate to tell a regression from a failure that was already there, and its timings feed the wall-clock estimate.

- [ ] **Step 1: Copy the runner.**

```bash
mkdir -p scratch
cp "../agent-cli-quality-trace-plan/scratch/run_live_proof.py" scratch/
git status --porcelain scratch
```
Expected: the `git status` line prints nothing (the directory is ignored).

- [ ] **Step 2: Record and time the baseline.**

```bash
{ time "$PY" -m pytest -q tests --ignore=tests/test_state.py > scratch/baseline-suite.txt 2>&1 ; } 2>> scratch/baseline-times.txt
{ time "$PY" -m pytest -q tests/test_state.py > scratch/baseline-state.txt 2>&1 ; } 2>> scratch/baseline-times.txt
{ time "$PY" -m deep_research.e2e_evaluation suite --tier controlled --repetitions 1 > scratch/baseline-e2e.txt 2>&1 ; } 2>> scratch/baseline-times.txt
tail -3 scratch/baseline-suite.txt scratch/baseline-state.txt; tail -5 scratch/baseline-e2e.txt; cat scratch/baseline-times.txt
```
Expected: all green. If anything fails, stop and report the failing test ids to the controller before Phase 1 starts. Do not fix unrelated failures inside this plan. Report the three `real` times to the controller.

- [ ] **Step 3: No commit.** Nothing tracked changed.

**Acceptance:** the five files exist under `scratch/`; the controller knows whether the baseline is green and how long the suite and the e2e suite take.

---

## Phase 1 — Findings carry their evidence (spec step 1)

Phase goal: every new finding carries a verbatim snippet, the read and locator it came from, and structured figures; targets carry structured fields; Figure Match exists and passes on the retained audit-2 reads. The old pipeline still runs unchanged (PD-3).

### Task 1.1: Contracts: finding, verification and report types

**Role:** sp-hard-implementer. **Wave:** 1A, alone: every task of Phases 1–3 builds on these types. **Depends on:** Task 0.1.

**Owns:** `src/deep_research/utils/types.py`, `src/deep_research/utils/__init__.py`, `src/deep_research/agents/identity.py` (`_merge_duplicate_findings`, around line 144), `tests/evidence_fakes.py` (new), `tests/test_types.py`, `tests/test_agents/test_identity.py`.

**Interfaces:**
- Produces every type of the three `utils/types.py` blocks in "Shared interfaces" (steps 1–3): `FigureKind`, `UnitDimension`, `MAX_SNIPPET_CHARS`, `FindingFigure`, the four new `Finding` fields and the six new `EvidenceTarget` fields; `FigureAttribution`, `FindingStatus`, `FigureDropReason`, `FindingDropReason`, `FigureContext`, `FigureResult`, `FindingVerification`, `Finding.verification`, `ResearchState.verified_findings` (and `ResearchStateUpdate.verified_findings`); `EarlierEdition`, `FactRow`, `NotFoundTarget`, `ReportStatement.finding_ids`, `RejectedDraftPoint.finding_labels`, and the three new `ReportComposition` fields. Also the test builders `make_read`, `make_finding`, `figure`, `make_target` in `tests/evidence_fakes.py`, frozen after this task (rule R3).

- [ ] **Step 1: Write the failing tests.**

Create `tests/evidence_fakes.py`:

```python
"""Builders for reads, findings and targets, shared by the Evidence Verifier tests."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from deep_research.agents.evidence import build_read_record
from deep_research.utils.types import EvidenceTarget, Finding, FindingFigure, ReadRecord

EIA_URL = "https://www.eia.gov/todayinenergy/detail.php?id=64705"
EIA_TITLE = "U.S. battery capacity increased 66% in 2024"
EIA_PAGE = (
    "U.S. battery capacity increased 66% in 2024. Generators added 10.4 "
    "gigawatts (GW) of new battery storage capacity in 2024, the second-largest "
    "generating capacity addition after solar, according to our January 2025 "
    "Preliminary Monthly Electric Generator Inventory. In 2025, capacity growth "
    "from battery storage could set a record as operators report plans to add "
    "19.6 GW of utility-scale battery storage to the grid."
)


def make_read(
    text: str = EIA_PAGE,
    *,
    url: str = EIA_URL,
    title: str = EIA_TITLE,
    passages: dict[str, str] | None = None,
) -> ReadRecord:
    return build_read_record(
        session_id="test-session",
        reader="web_scraper",
        requested_url=url,
        resolved_url=url,
        title=title,
        retrieved_at="2026-09-24T00:00:00+00:00",
        text=text,
        passages=passages or {"page-1-chunk-0": text},
    )


def figure(
    value: str, unit: str, period: str | None = None, kind: str | None = None
) -> FindingFigure:
    return FindingFigure(value=value, unit=unit, period=period, kind=kind)


def make_finding(
    read: ReadRecord,
    snippet: str,
    *,
    figures: Sequence[FindingFigure] = (),
    content: str | None = None,
    target_ids: Sequence[str] = (),
    **fields: Any,
) -> Finding:
    locator = next(
        (key for key, value in read.passages.items() if snippet in value),
        next(iter(read.passages)),
    )
    return Finding(
        content=content or snippet,
        source_url=read.resolved_url,
        source_title=read.title,
        extracted_at="2026-09-24T00:00:00+00:00",
        confidence=0.9,
        related_sub_topic="Battery storage",
        snippet=snippet,
        read_id=read.read_id,
        locator=locator,
        figures=list(figures),
        target_ids=list(target_ids),
        **fields,
    )


def make_target(target_id: str = "topic-01-target-01", **fields: Any) -> EvidenceTarget:
    """A target that validates in the current phase (Task 5.1 deletes the legacy branch)."""
    base: dict[str, Any] = {
        "target_id": target_id,
        "coverage_id": target_id.rsplit("-target-", 1)[0],
        "question": "How much battery storage capacity was added in the United States in 2024?",
        "measure": "battery storage power capacity added",
        "unit_dimension": "power",
        "period": "2024",
        "kind": "actual",
        "geography": "United States",
        "organisation": None,
        "required": True,
    }
    if "required_dimensions" in EvidenceTarget.model_fields:
        base.update(
            required_dimensions=["measure: battery storage power capacity added"],
            critical=False,
            support_policy="primary_attribution",
        )
    base.update(fields)
    return EvidenceTarget(**base)
```

Append to `tests/test_types.py`:

```python
import pytest
from pydantic import ValidationError

from deep_research.utils.types import FindingFigure
from tests.evidence_fakes import figure, make_finding, make_read, make_target


def test_a_finding_carries_its_snippet_read_locator_and_figures() -> None:
    read = make_read()
    finding = make_finding(
        read,
        "Generators added 10.4 gigawatts (GW) of new battery storage capacity in 2024,",
        figures=[figure("10.4", "gigawatts", "2024", "actual")],
    )
    assert (finding.read_id, finding.locator) == (read.read_id, "page-1-chunk-0")
    assert finding.figures[0] == FindingFigure(
        value="10.4", unit="gigawatts", period="2024", kind="actual"
    )


def test_blank_evidence_fields_are_absent() -> None:
    finding = make_finding(make_read(), "Generators added 10.4 gigawatts").model_copy(
        update={"snippet": " ", "read_id": "", "locator": "  "}
    )
    rebuilt = type(finding).model_validate(finding.model_dump())
    assert (rebuilt.snippet, rebuilt.read_id, rebuilt.locator) == (None, None, None)


def test_a_figure_needs_a_value_and_a_unit() -> None:
    with pytest.raises(ValidationError):
        FindingFigure(value="", unit="GW")
    with pytest.raises(ValidationError):
        FindingFigure(value="10.4", unit="")


def test_a_target_carries_structured_fields() -> None:
    target = make_target(organisation="U.S. Energy Information Administration")
    assert (target.unit_dimension, target.period, target.kind) == ("power", "2024", "actual")
    assert target.organisation == "U.S. Energy Information Administration"
    with pytest.raises(ValidationError):
        make_target(unit_dimension="volts")
```


Append to `tests/test_agents/test_identity.py`:

```python
from deep_research.agents.identity import deduplicate_findings
from tests.evidence_fakes import figure, make_finding, make_read


def test_a_duplicate_keeps_the_winners_evidence_and_fills_a_missing_one() -> None:
    read = make_read()
    snippet = "Generators added 10.4 gigawatts (GW) of new battery storage capacity in 2024,"
    rich = make_finding(read, snippet, figures=[figure("10.4", "GW", "2024", "actual")],
                        content="EIA: 10.4 GW added in 2024.")
    bare = rich.model_copy(update={"snippet": None, "read_id": None, "locator": None,
                                   "figures": [], "confidence": 0.99})
    [kept] = deduplicate_findings([rich, bare])
    assert kept.confidence == 0.99          # the winner is still the higher confidence
    assert kept.snippet == snippet          # its missing evidence is filled from the duplicate
    assert kept.figures == rich.figures
```

Append the step-2 verification tests to `tests/test_types.py` as well:

```python
from deep_research.utils.types import (
    FigureContext,
    FigureResult,
    FindingVerification,
    ResearchState,
    merge_research_state,
)


def _context(**overrides: object) -> FigureContext:
    fields = dict(period="2024", scope=None, attribution="own",
                  organisation="U.S. Energy Information Administration", kind="actual")
    fields.update(overrides)
    return FigureContext(**fields)


def test_a_kept_figure_carries_its_context() -> None:
    with pytest.raises(ValidationError):
        FigureResult(figure=figure("10.4", "GW"), matched=True)
    dropped = FigureResult(figure=figure("10.4", "GW"), matched=True,
                           dropped_reason="evidence_not_on_page")
    assert not dropped.kept


def test_status_and_drop_reason_agree() -> None:
    with pytest.raises(ValidationError):
        FindingVerification(status="dropped")
    with pytest.raises(ValidationError):
        FindingVerification(status="verified", dropped_reason="snippet_not_on_page")


def test_verified_means_every_figure_confirmed() -> None:
    corrected = FigureResult(figure=figure("18.9", "GW"), matched=True,
                             context=_context(scope="all segments"), corrected=True)
    with pytest.raises(ValidationError):
        FindingVerification(status="verified", figure_results=[corrected])
    assert FindingVerification(status="verified_corrected", figure_results=[corrected])


def test_a_verified_finding_keeps_at_least_one_figure() -> None:
    dropped = FigureResult(figure=figure("10.4", "GW"), matched=False,
                           dropped_reason="figure_not_in_evidence")
    with pytest.raises(ValidationError):
        FindingVerification(status="verified_corrected", figure_results=[dropped])


def test_verified_findings_are_replaced_not_appended() -> None:
    read = make_read()
    first = make_finding(read, "Generators added 10.4 gigawatts")
    second = make_finding(read, "operators report plans to add 19.6 GW")
    state = ResearchState(session_id="s", original_question="q")
    state = merge_research_state(state, {"verified_findings": [first]})
    state = merge_research_state(state, {"verified_findings": [first, second]})
    assert state.verified_findings == [first, second]
```

And the step-3 report types:

```python
from deep_research.utils.types import FactRow, NotFoundTarget, ReportComposition


def test_a_fact_row_and_a_not_found_target_validate() -> None:
    row = FactRow(row_id="K001", organisation="U.S. Energy Information Administration",
                  attribution="own", measure="battery storage power capacity added",
                  period="2024", value="10.4 GW", kind="actual", finding_id="f1")
    assert row.earlier == [] and row.duplicate_finding_ids == []
    assert NotFoundTarget(target_id="topic-02-target-01", question="q").searched is False


def test_a_composition_carries_fact_rows_not_found_and_labels() -> None:
    composition = ReportComposition(question="q", session_id="s")
    assert (composition.fact_rows, composition.not_found, composition.finding_labels) == ([], [], {})
```

- [ ] **Step 2: Run to see them fail.**

Run: `"$PY" -m pytest -q tests/test_types.py tests/test_agents/test_identity.py`
Expected: FAIL (`ImportError: FindingFigure`).

- [ ] **Step 3: Add the types.** In `utils/types.py`, add every type of the three `utils/types.py` blocks in "Shared interfaces" (steps 1–3), typed exactly as there. Step 1: `FigureKind`, `UnitDimension` and `MAX_SNIPPET_CHARS` beside the other aliases; `FindingFigure` directly above `class Finding`; the four `Finding` fields after `release_date`, with docstrings stating they come from the researcher's admitted passage; extend the `normalize_binding_and_dates` loop tuple with `"snippet"`, `"read_id"`, `"locator"`; the six `EvidenceTarget` fields after `support_policy`, each `= None`. Step 2: the aliases and the three verification models directly after `FindingFigure`, with the validators below. Step 3: `EarlierEdition`, `FactRow` and `NotFoundTarget` directly above `class ReportComposition`, and the new fields of `ReportStatement`, `RejectedDraftPoint` and `ReportComposition`. Re-export every new public name from `utils/__init__.py` (import block and `__all__`).

```python
class FigureResult(ContractModel):
    ...  # fields as in Shared interfaces

    @property
    def kept(self) -> bool:
        return self.dropped_reason is None

    @model_validator(mode="after")
    def kept_figures_carry_context(self) -> "FigureResult":
        if self.kept and self.context is None:
            raise ValueError("a kept figure carries its verified context")
        return self


class FindingVerification(ContractModel):
    ...  # fields as in Shared interfaces

    @model_validator(mode="after")
    def status_is_consistent(self) -> "FindingVerification":
        if (self.status == "dropped") != (self.dropped_reason is not None):
            raise ValueError("a dropped finding, and only a dropped one, names its reason")
        if self.status != "dropped" and self.figure_results and not any(
            result.kept for result in self.figure_results
        ):
            raise ValueError("a verified finding keeps at least one figure")
        if self.status == "verified" and any(
            result.corrected or not result.kept for result in self.figure_results
        ):
            raise ValueError("a corrected or dropped figure makes the finding verified_corrected")
        return self
```

Add `verification: FindingVerification | None = None` to `Finding` (docstring: "`None` until the Evidence Verifier has judged this finding"). Add `verified_findings: list[Finding] = Field(default_factory=list)` to `ResearchState` beside `raw_findings` (docstring: the complete verified snapshot for the run so far, written by the Evidence Verifier, replaced on each write like `evaluated_sources`) and `verified_findings: list[Finding]` to `ResearchStateUpdate`. Do **not** add it to `_APPEND_STATE_FIELDS`. Re-export the seven new names from `utils/__init__.py`.

- [ ] **Step 4: Carry the evidence through deduplication.** In `agents/identity.py::_merge_duplicate_findings`, after the attribution block, fill the evidence triple as one unit, the same way the attribution pair is filled:

```python
    if not merged.snippet and loser.snippet:
        merged = merged.model_copy(
            update={
                "snippet": loser.snippet,
                "read_id": loser.read_id,
                "locator": loser.locator,
                "figures": list(loser.figures),
            }
        )
```

- [ ] **Step 5: Run the tests.**

Run: `"$PY" -m pytest -q tests/test_types.py tests/test_agents/test_identity.py tests/test_imports.py` and then, alone, `"$PY" -m pytest -q tests/test_state.py`
Expected: PASS. (This task runs alone in its wave, so it may run `tests/test_imports.py`.)

- [ ] **Step 6: Commit.**

```bash
git add src/deep_research/utils/types.py src/deep_research/utils/__init__.py src/deep_research/agents/identity.py tests/evidence_fakes.py tests/test_types.py tests/test_agents/test_identity.py
git commit -m "feat(types): contracts for finding evidence, verification and report facts (steps 1-3)"
```

**Acceptance:** existing constructions of `Finding`, `EvidenceTarget`, `ResearchState` and `ReportComposition` still validate unchanged (every new field defaults); the new tests pass, including the verification validators; `verified_findings` is replaced on write, never appended.

### Task 1.2: Cosmetic matching, figure normalisation and Figure Match

**Role:** sp-implementer. **Wave:** 1B, parallel with Tasks 1.3 and 1.4. **Depends on:** Task 1.1 merged.

**Owns:** `src/deep_research/agents/evidence.py` (the "canonical text" section, `canonical_read_text` and `excerpt_matches`, around lines 124–166), `src/deep_research/agents/figures.py` (new), `src/deep_research/agents/evidence_verifier.py` (new), `tests/test_agents/test_evidence.py`, `tests/test_agents/test_figures.py` (new), `tests/test_agents/test_evidence_verifier.py` (new).

**Superseded by D7/D8:** the D8 contract (`SDD/d8-contract.md`, `.superpowers/sdd/2026-09-24-evidence-verifier-pipeline/d8-contract.md`) narrows Figure Match to snippet-on-page: `figure_match` keeps `read_found` and `snippet_on_page` and stops computing the per-figure `matched` tuple, because every figure is judged by the Context Check instead. `figures.py`, `figure_in_text` (the P1-2 fallback for a figure with no reply) and the cosmetic rule stand. Apply the contract where this body differs; do not rewrite the body.

**Interfaces:**
- Consumes: `UnitDimension`, `Finding`, `FindingFigure` (Task 1.1); `tests/evidence_fakes.py` (Task 1.1).
- Produces: `cosmetic_text`, the cosmetic `excerpt_matches`, the whole `figures.py` API, and `EVIDENCE_VERIFIER_NAME`, `FigureMatch`, `read_text`, `figure_match`, exactly as in "Shared interfaces". Nothing is re-exported here (Task 1.5 does it).

`excerpt_matches` is also used by `build_read_record`, `validate_cached_read` and the researcher's admission helpers. The spec (§5.1 step 1: "This reuses `excerpt_matches`") makes the whole project use the cosmetic rule. `canonical_read_text` is used for content hashes and must not change.

- [ ] **Step 1: Write the failing tests.**

Append to `tests/test_agents/test_evidence.py`:

```python
from deep_research.agents.evidence import cosmetic_text, excerpt_matches


def test_excerpt_matches_is_cosmetic_only() -> None:
    page = (
        "EIA said \u201cdevelopers plan to add 19.6 GW\u201d of bat\u00adtery "
        "stor-\nage in 2025, and the grid-\nscale fleet keeps growing."
    )
    assert excerpt_matches(
        page, 'EIA said "developers plan to add 19.6 GW" of battery storage in 2025'
    )
    assert excerpt_matches(page, "eia said \u201cDEVELOPERS plan to add 19.6 GW\u201d")
    assert excerpt_matches(page, "the grid-scale fleet keeps growing")
    assert not excerpt_matches(page, "developers plan to add 19.7 GW")
    assert not excerpt_matches(page, "developers plan to add 19.6 GW of storage")
    assert not excerpt_matches(page, "   ")


def test_cosmetic_text_keeps_digits_units_and_dashes() -> None:
    assert cosmetic_text("10,400\u2009MW \u2013 Q1") == "10,400 mw \u2013 q1"
```

Create `tests/test_agents/test_figures.py`:

```python
"""Spec §5.1 step 2: the fixed, question-independent figure normalisation."""

from __future__ import annotations

import pytest

from deep_research.agents.figures import (
    bare_numbers,
    dates_in,
    figure_in_text,
    parse_figure,
    quantities_in,
    same_quantity,
    unit_dimension,
    without_dates,
)

PAGE = (
    "Developers added 10.4 gigawatts (GW) of utility-scale battery storage in "
    "2024. The monitor counted 12,314 MW across all segments, or 37,143 "
    "megawatt-hours, and 16 GW/47.3 GWh in 2025."
)


@pytest.mark.parametrize(
    ("value", "unit"),
    [
        ("10.4", "GW"),
        ("10.4", "gigawatts"),
        ("10,400", "MW"),
        ("10400", "megawatts"),
        ("12,314", "MW"),
        ("12314", "megawatt"),
        ("37,143", "MWh"),
        ("37.143", "GWh"),
        ("16", "GW"),
        ("47.3", "gigawatt-hours"),
    ],
)
def test_a_figure_matches_under_the_fixed_normalisation(value: str, unit: str) -> None:
    assert figure_in_text(value, unit, PAGE)


@pytest.mark.parametrize(
    ("value", "unit"),
    [
        ("12.3", "GW"),  # 12,314 MW is 12.314 GW: rounding is not a match
        ("10.4", "GWh"),  # the right number in the wrong dimension
        ("104", "GW"),
        ("2024", "GW"),  # a year is never a figure
        ("47.3", "GW"),
    ],
)
def test_a_figure_that_is_not_there_does_not_match(value: str, unit: str) -> None:
    assert not figure_in_text(value, unit, PAGE)


def test_a_parenthetical_may_sit_between_value_and_unit() -> None:
    assert figure_in_text("19.6", "GW", "plans to add 19.6 (nineteen point six) GW")
    assert figure_in_text("12,314", "MW", "deployed 12,314 (MW) in 2024")


def test_value_and_unit_must_be_adjacent() -> None:
    assert not figure_in_text("15", "GW", "15 projects totalling several GW")


def test_percent_spellings_are_one_unit() -> None:
    assert figure_in_text("66", "%", "capacity increased 66 percent in 2024")
    assert figure_in_text("66", "percent", "capacity increased 66% in 2024")
    assert figure_in_text("47", "%", "growth of 47 per cent")


def test_a_single_number_word_before_a_unit_is_its_number() -> None:
    assert figure_in_text("10", "GW", "about ten gigawatts were added")


def test_unknown_units_match_literally_after_grouping() -> None:
    assert figure_in_text("1,200", "projects", "1200 projects came online")
    assert not figure_in_text("1,200", "projects", "1200 plants came online")


def test_unit_dimension() -> None:
    assert unit_dimension("GW") == "power"
    assert unit_dimension("megawatt-hours") == "energy"
    assert unit_dimension("%") == "percent"
    assert unit_dimension("projects") is None


def test_same_quantity_compares_across_scales() -> None:
    ten_gw = parse_figure("10.4", "GW")
    [found] = [q for q in quantities_in("added 10,400 MW") if q.unit == "mw"]
    assert ten_gw is not None and same_quantity(ten_gw, found)


def test_bare_numbers_skip_years_dates_labels_and_quantities() -> None:
    text = "On March 12, 2025, 3 states in Q3 reported 1,250 systems and 10.4 GW."
    assert bare_numbers(text) == ["1250"]
    assert bare_numbers("released 2025-03-12; 12 March 2025; Q1 2025") == []     # F2
    assert "2025-03-12" in dates_in("released 2025-03-12 and 12 March 2025")
    assert len(dates_in("released 2025-03-12 and 12 March 2025")) == 2
    assert "03" not in without_dates("released 2025-03-12") and "2025" not in without_dates("released 2025-03-12")
```

Create `tests/test_agents/test_evidence_verifier.py`:

```python
"""The Evidence Verifier (spec §5)."""

from __future__ import annotations

from deep_research.agents.evidence_verifier import figure_match
from tests.evidence_fakes import figure, make_finding, make_read

SNIPPET = "Generators added 10.4 gigawatts (GW) of new battery storage capacity in 2024,"


def test_figure_match_confirms_the_snippet_and_each_figure() -> None:
    read = make_read()
    finding = make_finding(
        read, SNIPPET,
        figures=[figure("10.4", "GW", "2024", "actual"), figure("19.6", "GW", "2025", "forecast")],
    )
    match = figure_match(finding, {read.read_id: read})
    assert match.read_found and match.snippet_on_page
    assert match.matched == (True, False)  # 19.6 GW is on the page, but not in this snippet


def test_a_snippet_that_is_not_on_the_page_matches_nothing() -> None:
    read = make_read()
    finding = make_finding(read, "EIA says 10.4 GW was added in 2024.",
                           figures=[figure("10.4", "GW")])
    match = figure_match(finding, {read.read_id: read})
    assert match.read_found and not match.snippet_on_page and match.matched == (False,)


def test_a_missing_read_is_reported() -> None:
    finding = make_finding(make_read(), SNIPPET, figures=[figure("10.4", "GW")])
    match = figure_match(finding, {})
    assert not match.read_found and match.matched == (False,)


def test_the_snippet_check_is_cosmetic() -> None:
    read = make_read()
    finding = make_finding(read, SNIPPET.upper(), figures=[figure("10,400", "MW")])
    assert figure_match(finding, {read.read_id: read}).matched == (True,)
```

- [ ] **Step 2: Run the tests to see them fail.**

Run: `"$PY" -m pytest -q tests/test_agents/test_figures.py tests/test_agents/test_evidence.py tests/test_agents/test_evidence_verifier.py`
Expected: FAIL (`ImportError` for `cosmetic_text`, `deep_research.agents.figures` and `deep_research.agents.evidence_verifier`).

- [ ] **Step 3: Implement the cosmetic rule in `evidence.py`.**

Add `import re` if missing. Directly after `canonical_read_text`, add `cosmetic_text`, and replace the body of `excerpt_matches`:

```python
_SOFT_HYPHEN = "\u00ad"
_QUOTE_TABLE = str.maketrans(
    {
        "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'", "\u2032": "'",
        "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"', "\u2033": '"',
    }
)
# A word broken across a line: "stor-\nage". Joined by default; kept as one
# hyphenated word ("grid-\nscale" -> "grid-scale") in the second reading.
_LINE_BREAK_HYPHEN = re.compile(r"(?<=\w)-[ \t]*\r?\n[ \t]*(?=\w)")


def cosmetic_text(text: str, *, join_hyphenation: bool = True) -> str:
    """Spec §5.1 step 1: the cosmetic normalisation, and nothing else.

    Whitespace and line breaks, curly and straight quotes, soft hyphens and
    line-break hyphenation, and case. Digits, units, dashes and words are
    untouched, so a paraphrase never matches the page it paraphrases.
    """
    if not isinstance(text, str):
        raise EvidenceContractError("text must be a string")
    value = unicodedata.normalize("NFC", text).replace(_SOFT_HYPHEN, "")
    value = value.translate(_QUOTE_TABLE)
    value = _LINE_BREAK_HYPHEN.sub("" if join_hyphenation else "-", value)
    return " ".join(value.split()).casefold()


def excerpt_matches(text: str, excerpt: str) -> bool:
    """True when ``excerpt`` is contained in ``text`` after cosmetic normalisation.

    Membership is exact after ``cosmetic_text`` -- no fuzzy ratio, no ellipsis
    stitching -- because a near-miss excerpt is how a paraphrase becomes
    "source text". A line-break hyphen is read both ways, as a broken word and
    as a hyphenated compound, because the page cannot say which it was.
    """
    candidate = cosmetic_text(excerpt)
    if not candidate:
        return False
    return candidate in cosmetic_text(text) or candidate in cosmetic_text(
        text, join_hyphenation=False
    )
```

- [ ] **Step 4: Create `src/deep_research/agents/figures.py`.**

```python
"""Figure normalisation for the Evidence Verifier (spec §5.1 step 2).

One fixed, question-independent rule set decides whether a figure is in a
snippet, whether the Context Check's evidence words carry it, and whether a
report sentence states it:

- digit grouping and spacing: ``10,400`` equals ``10400``;
- unit spelling: ``GW`` equals ``gigawatt(s)``, ``MWh`` equals
  ``megawatt-hour(s)``, ``%`` equals ``percent``;
- unit scale within one dimension: kW, MW, GW, TW; kWh, MWh, GWh, TWh;
- value and unit are adjacent, or separated only by one parenthetical;
- a single number word before a unit ("ten gigawatts") is its number.

Callers pass a snippet, evidence words, or a report sentence. Nothing here
ever reads a finding's ``content``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from deep_research.agents.evidence import cosmetic_text
from deep_research.utils.types import UnitDimension

_SCALES: dict[str, tuple[UnitDimension, Decimal]] = {
    "kw": ("power", Decimal("1e3")),
    "mw": ("power", Decimal("1e6")),
    "gw": ("power", Decimal("1e9")),
    "tw": ("power", Decimal("1e12")),
    "kwh": ("energy", Decimal("1e3")),
    "mwh": ("energy", Decimal("1e6")),
    "gwh": ("energy", Decimal("1e9")),
    "twh": ("energy", Decimal("1e12")),
    "%": ("percent", Decimal("1")),
}
_PREFIX = {"kilo": "k", "mega": "m", "giga": "g", "tera": "t"}
_NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90,
}
_MONTHS = (
    "january|february|march|april|may|june|july|august|september|october|"
    "november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec"
)

# Longest spellings first, so "megawatt-hours" is never read as "megawatt".
_UNIT = (
    r"(?:kilo|mega|giga|tera)watt[- ]?hours?"
    r"|(?:kilo|mega|giga|tera)watts?"
    r"|[kmgt]wh\b|[kmgt]w\b|per ?cent\b|%"
)
_NUMBER = r"\d{1,3}(?:[, ]\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
_WORD = "|".join(sorted(_NUMBER_WORDS, key=len, reverse=True))
_GAP = r"\s*(?:\([^()]{0,40}\)\s*)?"
_QUANTITY = re.compile(
    rf"(?<![\w.,])(?P<value>{_NUMBER}|\b(?:{_WORD})\b){_GAP}"
    rf"\(?\s*(?P<unit>{_UNIT})\s*\)?"
)
_ANY_NUMBER = re.compile(rf"(?<![\w.,])(?:{_NUMBER})(?!\w)")
_YEAR = re.compile(r"(?:19|20)\d{2}")
_DATE_DAY = re.compile(rf"\b(?:{_MONTHS})\.?\s+$")
_ORDINAL = re.compile(r"(?:st|nd|rd|th)\b")
_ISO_DATE = re.compile(r"\b(?:19|20)\d{2}-\d{1,2}(?:-\d{1,2})?\b")
_DAY_FIRST_DATE = re.compile(rf"\b\d{{1,2}}\s+(?:{_MONTHS})\.?\s+(?:19|20)\d{{2}}\b")


@dataclass(frozen=True)
class Quantity:
    """One value with its unit, normalised (see the module docstring)."""

    number: Decimal
    unit: str
    dimension: UnitDimension | None
    base: Decimal | None
    value_text: str
    unit_text: str
    start: int = 0
    end: int = 0


def _canonical_unit(unit: str) -> str:
    text = " ".join(cosmetic_text(unit).replace("-", " ").split())
    if text in {"%", "percent", "per cent"}:
        return "%"
    spelled = re.fullmatch(r"(kilo|mega|giga|tera)watts?( ?hours?)?", text)
    if spelled:
        return _PREFIX[spelled.group(1)] + ("wh" if spelled.group(2) else "w")
    return text


def _number(value: str) -> Decimal | None:
    text = cosmetic_text(value)
    if text in _NUMBER_WORDS:
        return Decimal(_NUMBER_WORDS[text])
    digits = text.replace(",", "").replace(" ", "")
    if not re.fullmatch(r"\d+(?:\.\d+)?", digits):
        return None
    try:
        return Decimal(digits)
    except InvalidOperation:
        return None


def _quantity(value: str, unit: str, *, start: int = 0, end: int = 0) -> Quantity | None:
    number = _number(value)
    if number is None:
        return None
    canonical = _canonical_unit(unit)
    scale = _SCALES.get(canonical)
    return Quantity(
        number=number,
        unit=canonical,
        dimension=scale[0] if scale else None,
        base=number * scale[1] if scale else None,
        value_text=value,
        unit_text=unit,
        start=start,
        end=end,
    )


def unit_dimension(unit: str) -> UnitDimension | None:
    """power, energy or percent for a known unit spelling, else ``None``."""
    scale = _SCALES.get(_canonical_unit(unit))
    return scale[0] if scale else None


def parse_figure(value: str, unit: str) -> Quantity | None:
    """A structured figure as a quantity, or ``None`` when ``value`` is no number."""
    return _quantity(value.strip(), unit.strip())


def quantities_in(text: str) -> list[Quantity]:
    """Every known-unit quantity in ``text``; offsets index ``cosmetic_text(text)``."""
    normalised = cosmetic_text(text)
    found: list[Quantity] = []
    for match in _QUANTITY.finditer(normalised):
        quantity = _quantity(
            match.group("value"), match.group("unit"), start=match.start(), end=match.end()
        )
        if quantity is not None:
            found.append(quantity)
    return found


def same_quantity(left: Quantity, right: Quantity) -> bool:
    """Equal after scale for known units; equal number and unit text otherwise."""
    if left.base is not None and right.base is not None:
        return left.dimension == right.dimension and left.base == right.base
    return left.unit == right.unit and left.number == right.number


def figure_in_text(value: str, unit: str, text: str) -> bool:
    """Spec §5.1 step 2: the figure occurs in ``text`` under the fixed rules."""
    target = parse_figure(value, unit)
    if target is None:
        return False
    if target.base is not None:
        return any(same_quantity(target, found) for found in quantities_in(text))
    literal = re.compile(
        rf"(?<![\w.,])(?P<value>{_NUMBER}|\b(?:{_WORD})\b){_GAP}"
        rf"\(?\s*{re.escape(target.unit)}(?!\w)"
    )
    return any(
        _number(match.group("value")) == target.number
        for match in literal.finditer(cosmetic_text(text))
    )


def bare_numbers(text: str) -> list[str]:
    """Numbers ``text`` states with no known unit, grouping removed.

    Skipped: every known-unit quantity, four-digit years, ISO and day-first
    dates, a day beside a month name, single-digit labels ("3 states", "Q3"),
    and ordinals.
    """
    normalised = cosmetic_text(text)
    # F2: a date's parts are not numbers ("released 2025-03-12" states no 03 or 12)
    taken = [(found.start, found.end) for found in quantities_in(text)] + _date_spans(normalised)
    numbers: list[str] = []
    for match in _ANY_NUMBER.finditer(normalised):
        if any(start <= match.start() < end for start, end in taken):
            continue
        raw = match.group(0)
        digits = raw.replace(",", "").replace(" ", "")
        if _YEAR.fullmatch(raw):
            continue
        if "." not in digits and len(digits) == 1:
            continue
        if _ORDINAL.match(normalised, match.end()):
            continue
        if int(float(digits)) <= 31 and _DATE_DAY.search(normalised[: match.start()]):
            continue
        numbers.append(digits)
    return numbers


def _date_spans(normalised: str) -> list[tuple[int, int]]:
    return [found.span() for pattern in (_ISO_DATE, _DAY_FIRST_DATE) for found in pattern.finditer(normalised)]


def dates_in(text: str) -> list[str]:
    """The ISO ("2025-03-12") and day-first ("12 March 2025") dates ``text`` states (F2)."""
    normalised = cosmetic_text(text)
    return [normalised[start:end] for start, end in sorted(_date_spans(normalised))]


def without_dates(text: str) -> str:
    """``text`` in cosmetic form with its ISO and day-first dates blanked (same length)."""
    normalised = cosmetic_text(text)
    for start, end in _date_spans(normalised):
        normalised = normalised[:start] + " " * (end - start) + normalised[end:]
    return normalised
```

- [ ] **Step 5: Create `src/deep_research/agents/evidence_verifier.py` with Figure Match only** (the Context Check is Task 2.1):

```python
"""The Evidence Verifier (spec §5): Figure Match, then the Context Check.

Figure Match is code: the finding's snippet must be on its read, and each
structured figure must be in the snippet under the fixed normalisation of
``figures``. It never parses ``content`` and never searches a whole page for
a number.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from deep_research.agents.evidence import excerpt_matches
from deep_research.agents.figures import figure_in_text
from deep_research.utils.types import Finding, ReadRecord

EVIDENCE_VERIFIER_NAME = "evidence_verifier"


@dataclass(frozen=True)
class FigureMatch:
    """Spec §5.1's two results for one finding."""

    read_found: bool
    snippet_on_page: bool
    matched: tuple[bool, ...]


def read_text(read: ReadRecord) -> str:
    """The read's stored text: every passage, in document order."""
    return " ".join(read.passages.values())


def figure_match(finding: Finding, reads: Mapping[str, ReadRecord]) -> FigureMatch:
    """Is the snippet on its page, and is each figure in the snippet?"""
    unmatched = tuple(False for _ in finding.figures)
    read = reads.get(finding.read_id or "")
    if read is None:
        return FigureMatch(read_found=False, snippet_on_page=False, matched=unmatched)
    if not finding.snippet or not excerpt_matches(read_text(read), finding.snippet):
        return FigureMatch(read_found=True, snippet_on_page=False, matched=unmatched)
    return FigureMatch(
        read_found=True,
        snippet_on_page=True,
        matched=tuple(
            figure_in_text(item.value, item.unit, finding.snippet)
            for item in finding.figures
        ),
    )
```

- [ ] **Step 6: Run the tests.**

Run: `"$PY" -m pytest -q tests/test_agents/test_figures.py tests/test_agents/test_evidence.py tests/test_agents/test_evidence_verifier.py tests/test_agents/test_researcher.py tests/test_agents/test_acquisition.py`
Expected: PASS. `test_researcher.py` and `test_acquisition.py` are run, not edited (rule R2): if one of their tests fails only because `excerpt_matches` is now case- and quote-insensitive, report its id to the controller; Task 1.5 changes that assertion to the §5.1 contract (a re-cased excerpt is admitted; a paraphrase is still refused).

- [ ] **Step 7: Commit.**

```bash
git add src/deep_research/agents/evidence.py src/deep_research/agents/figures.py src/deep_research/agents/evidence_verifier.py tests/test_agents/test_figures.py tests/test_agents/test_evidence.py tests/test_agents/test_evidence_verifier.py
git commit -m "feat(evidence): cosmetic excerpt matching, figure normalisation and Figure Match (spec 5.1)"
```

**Acceptance:** the new tests pass; `excerpt_matches` is cosmetic everywhere; `canonical_read_text` and content hashes are unchanged; Figure Match reports the snippet and each figure separately.

### Task 1.3: The researcher persists snippet, read, locator and figures; budget 20; own page first

**Role:** sp-implementer. **Wave:** 1B, parallel with Tasks 1.2 and 1.4. **Depends on:** Task 1.1 merged.

**Owns:** `src/deep_research/agents/researcher.py` (`RESEARCHER_SYSTEM_PROMPT` lines 102–137; `FindingDraft` 147–188; `_FINDING_REPLY_EXAMPLES` 636–660; `render_planned_targets` 712–724; `extraction_messages` registry contract 853–878; `build_findings` 1066–1194), `src/deep_research/e2e_evaluation/replay.py` (`ReplaySource` dataclass; `_reply_SubTopicFindingsDraft` 601–651), `config.yaml`, `tests/test_config.py`, `tests/test_agents/test_researcher.py`, and the other files that build `FindingDraft(...)`: `tests/test_agents/test_planner_researcher_seam.py`, `tests/test_agents/test_evidence_quality_seam.py`, `tests/test_agents/test_acquisition.py`, `tests/test_evaluation/conftest.py`.

**Interfaces:**
- Consumes: `FindingFigure`, `MAX_SNIPPET_CHARS`, the new `EvidenceTarget` fields (Task 1.1); the existing `excerpt_matches` (its cosmetic rule lands in Task 1.2 and changes none of these tests).
- Produces: `FindingFigureDraft(value: str, unit: str, period: str | None = None, kind: str | None = None)`; `FindingDraft.snippet` (renamed from `excerpt`) and `FindingDraft.figures: list[FindingFigureDraft]`; admitted findings carry `snippet`, `read_id`, `locator`, `figures`; `render_planned_targets` lines read `- <id> [<coverage>]: <question> (measure: …; unit: …; period: …; kind: …; organisation: …)` with empty parts omitted.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_agents/test_researcher.py`):

```python
from deep_research.agents.researcher import (
    FindingDraft,
    FindingFigureDraft,
    SubTopicFindingsDraft,
    build_findings,
)
from deep_research.utils.types import MAX_SNIPPET_CHARS, FindingFigure, SubTopic
from tests.evidence_fakes import make_read

_SNIPPET = "Generators added 10.4 gigawatts (GW) of new battery storage capacity in 2024,"


def _topic() -> SubTopic:
    return SubTopic(
        coverage_id="topic-01", title="Battery storage", rationale="r",
        search_queries=["q"], success_criteria=["c"], priority=1,
    )


def _draft(read, **overrides) -> SubTopicFindingsDraft:
    fields = dict(
        content="EIA reports 10.4 GW of battery storage added in 2024.",
        source_url=read.resolved_url, source_title=read.title, confidence=0.9,
        read_id=read.read_id, locator="page-1-chunk-0", snippet=_SNIPPET,
        figures=[FindingFigureDraft(value="10.4", unit="gigawatts", period="2024", kind="actual")],
        data_period="2024",
    )
    fields.update(overrides)
    return SubTopicFindingsDraft(findings=[FindingDraft(**fields)])


def _build(read, draft):
    return build_findings(
        draft, sub_topic=_topic(), extracted_at="2026-09-24T00:00:00+00:00",
        known_urls=[read.resolved_url], known_reads={read.read_id: read},
    )


def test_build_findings_keeps_snippet_read_locator_and_figures() -> None:
    read = make_read()
    findings, rejected = _build(read, _draft(read))
    assert rejected == []
    [finding] = findings
    assert (finding.snippet, finding.read_id, finding.locator) == (
        _SNIPPET, read.read_id, "page-1-chunk-0"
    )
    assert finding.figures == [
        FindingFigure(value="10.4", unit="gigawatts", period="2024", kind="actual")
    ]


def test_build_findings_refuses_a_snippet_the_locator_does_not_carry() -> None:
    read = make_read()
    findings, rejected = _build(read, _draft(read, snippet="EIA says 10.4 GW was added."))
    assert findings == [] and "snippet was not admitted at locator" in rejected[0]


def test_build_findings_refuses_a_snippet_over_the_cap() -> None:
    long_text = "Battery storage grew. " * 40
    read = make_read(long_text)
    findings, rejected = _build(read, _draft(read, snippet=long_text.strip()))
    assert findings == [] and f"longer than {MAX_SNIPPET_CHARS}" in rejected[0]


def test_an_unusable_figure_is_dropped_but_the_finding_is_kept() -> None:
    read = make_read()
    draft = _draft(read, figures=[
        FindingFigureDraft(value="", unit="GW"),
        FindingFigureDraft(value="10.4", unit="GW", kind="estimate"),
    ])
    findings, rejected = _build(read, draft)
    [finding] = findings
    assert finding.figures == [FindingFigure(value="10.4", unit="GW", kind=None)]
    assert any("figure 1 has no value or unit" in reason for reason in rejected)
```

- [ ] **Step 2: Run to see them fail.** Run: `"$PY" -m pytest -q tests/test_agents/test_researcher.py -k "snippet or unusable_figure"`. Expected: FAIL (`ImportError: FindingFigureDraft`).

- [ ] **Step 3: Draft schema.** In `researcher.py` above `FindingDraft` add:

```python
class FindingFigureDraft(ContractModel):
    """One figure the snippet states, before domain validation (no Field constraints)."""

    value: str
    unit: str
    period: str | None = None
    kind: str | None = None
```

In `FindingDraft` rename `excerpt: str | None = None` to `snippet: str | None = None` and add `figures: list[FindingFigureDraft] = Field(default_factory=list)` after `target_ids`. Update the comment above `read_id` to say "read_id, locator and snippet".

- [ ] **Step 4: Admission.** In `build_findings`:
  1. Replace the `if not item.locator or not item.excerpt:` check with `if not item.locator or not item.snippet:` and the reason text with `"finding {index}: read id requires locator and snippet"`.
  2. Directly after it add:
     ```python
            if len(item.snippet) > MAX_SNIPPET_CHARS:
                rejected.append(
                    f"finding {index}: snippet longer than {MAX_SNIPPET_CHARS} characters"
                )
                continue
     ```
  3. Replace `excerpt_matches(passage, item.excerpt)` with `excerpt_matches(passage, item.snippet)` and its reason with `"finding {index}: snippet was not admitted at locator"`.
  4. Add the helper below `_admitted_target_ids`:
     ```python
     def _admitted_figures(
         drafts: Sequence[FindingFigureDraft], *, index: int, rejected: list[str]
     ) -> list[FindingFigure]:
         """The figures a finding may carry; an unusable one is named and dropped."""
         figures: list[FindingFigure] = []
         for position, draft in enumerate(drafts, start=1):
             value, unit = draft.value.strip(), draft.unit.strip()
             if not value or not unit:
                 rejected.append(
                     f"finding {index}: figure {position} has no value or unit"
                 )
                 continue
             kind = (draft.kind or "").strip().casefold()
             figures.append(
                 FindingFigure(
                     value=value,
                     unit=unit,
                     period=(draft.period or "").strip() or None,
                     kind=kind if kind in ("actual", "forecast") else None,
                 )
             )
         return figures
     ```
  5. In the `Finding(...)` constructor add `snippet=item.snippet if read is not None else None`, `read_id=item.read_id if read is not None else None`, `locator=item.locator if read is not None else None`, and `figures=_admitted_figures(item.figures, index=index, rejected=rejected) if read is not None else []`.

- [ ] **Step 5: Contract text.** In `extraction_messages`, replace the acquisition branch's first sentence ("Every finding MUST copy the read_id, locator, and excerpt of the passage it came from exactly as the acquisition context above prints them, and MUST name …") with:

```
"Return one finding per distinct, source-backed figure or fact. Every finding "
"MUST copy read_id and locator exactly as the acquisition context above prints "
"them, and MUST carry a snippet: one or two sentences copied character for "
"character from that passage, containing the finding's figures (at most "
f"{MAX_SNIPPET_CHARS} characters). List every figure the snippet states for "
"the finding in figures: value exactly as the snippet writes it (\"10.4\", "
"\"12,314\"), unit as the snippet writes it (\"GW\", \"megawatts\", \"%\"), the "
"period it applies to, and kind: actual for a measured or reported outcome, "
"forecast for a projection, plan or expectation. Figures that measure "
"different things - a yearly addition and a cumulative total - belong in "
"separate findings. A finding MUST name in target_ids every planned target ..."
```
(keep the rest of the existing sentence from "every planned target from the Planned targets list …" unchanged), and change the later sentence "A finding whose excerpt the locator does not contain is dropped." to "A finding whose snippet the locator does not contain is dropped."

- [ ] **Step 6: Reply example.** In `_FINDING_REPLY_EXAMPLES`, replace `'"excerpt":"The measured reduction was 12 percent, according to the '` / `'Example Statistical Agency, across all classes.",'` with `'"snippet":"The measured reduction was 12 percent, according to the '` / `'Example Statistical Agency, across all classes.",'` and add `'"figures":[{"value":"12","unit":"percent","period":"2024","kind":"actual"}],'` before `'"target_ids"'`.

- [ ] **Step 7: Own page first.** In `RESEARCHER_SYSTEM_PROMPT` delete the paragraph that begins "Verification needs independence, so a sub-topic is not finished when its key facts come from a single publisher." through "…over another query for the same one.\n" and put in its place:

```
"Read the organisation's own page first. When a figure belongs to an "
"organisation - an agency's inventory, a market monitor's release, a "
"company's filing - read that organisation's own page or document (for "
"example eia.gov or woodmac.com) before any story that repeats it. Use a "
"relay only when the original is not reachable, and record it as a relay: "
"cite the page where you read it and name the organisation it credits in "
"attributed_issuer. Do not search for a second source to confirm a figure "
"its own organisation publishes.\n"
```

- [ ] **Step 8: Budget 20, seven turns, five sub-topics (F1).** In `config.yaml` set `agents.tool_budget_overrides.researcher: 20` and change the comment above it from "…the fact checker keeps the researcher's ten…" to state that the researcher's per-sub-topic budget is 20 (spec §7.2). Also set `agents.max_iterations: 7` (was 5: the ReAct loop stops after this many model turns whatever the tool budget, `react.py` line 327, so without it the budget of 20 changes nothing) and `agents.max_sub_topics: 5` (was 7), each with a one-line comment naming spec §7.2. Until the cutover the old pipeline's other loops also get seven turns; Gate G1's e2e run shows whether a scripted double depended on five. Add to `tests/test_config.py`: `settings = load_settings("config.yaml")`, then `assert settings.agents.max_iterations == 7 and settings.agents.max_sub_topics == 5 and settings.agents.tool_budget_overrides["researcher"] == 20` (import `load_settings` as the file already does, or from `deep_research.main`). Leave the other keys for Task 4.1.

- [ ] **Step 9: Replay double.** In `e2e_evaluation/replay.py` add `figures: tuple[tuple[str, str, str | None, str | None], ...] = ()` to the `ReplaySource` dataclass (value, unit, period, kind), and in `_reply_SubTopicFindingsDraft` build the draft with `snippet=source.excerpt` (the scripted excerpt, which the assertion just above proves the evidence excerpt contains) and `figures=[FindingFigureDraft(value=v, unit=u, period=p, kind=k) for v, u, p, k in source.figures]` instead of `excerpt=excerpt`. Import `FindingFigureDraft`.

- [ ] **Step 10: Rename the keyword everywhere.** Run `grep -rn "excerpt=" tests src/deep_research/e2e_evaluation src/deep_research/evaluation | grep -i "FindingDraft\|findings="` and, in every `FindingDraft(...)` construction it shows, rename the keyword `excerpt=` to `snippet=`. Leave `EvidenceUnit(excerpt=...)` and every other model alone. If it lists a file this task does not own, report it (rule R2).

- [ ] **Step 11: Planned targets show their structured fields.** Replace `render_planned_targets`'s body (`researcher.py` 712–724) with the code below, and append the test `test_planned_targets_render_their_structured_fields` shown in Task 1.4's Step 1 to `tests/test_agents/test_researcher.py` (this task owns that file):


```python
    lines: list[str] = []
    for target in targets:
        details = "; ".join(
            f"{name}: {value}"
            for name, value in (
                ("measure", target.measure),
                ("unit", target.unit_dimension),
                ("period", target.period),
                ("kind", target.kind),
                ("organisation", target.organisation),
            )
            if value
        )
        line = f"- {target.target_id} [{target.coverage_id}]: {target.question}"
        lines.append(f"{line} ({details})" if details else line)
    return "\n".join(lines)
```
(The replay double reads only the `- <id> [<coverage>]: ` prefix, which is unchanged.)
- [ ] **Step 12: Run the tests.**

Run: `"$PY" -m pytest -q tests/test_agents/test_researcher.py tests/test_agents/test_planner_researcher_seam.py tests/test_agents/test_evidence_quality_seam.py tests/test_agents/test_acquisition.py tests/test_agents/test_tool_free_prompts.py tests/test_config.py tests/test_e2e_evaluation`
Expected: PASS. A `test_config.py` assertion that pins the researcher's budget at 10 is updated to 20.

- [ ] **Step 13: Commit.**

```bash
git add src/deep_research/agents/researcher.py src/deep_research/e2e_evaluation/replay.py config.yaml tests/test_config.py tests/test_agents/test_researcher.py tests/test_agents/test_planner_researcher_seam.py tests/test_agents/test_evidence_quality_seam.py tests/test_agents/test_acquisition.py tests/test_evaluation/conftest.py
git commit -m "feat(researcher): persist snippet, read, locator and figures; own page first; budget 20"
```

**Acceptance:** admitted findings carry the evidence triple and figures; an off-page or over-long snippet is refused with a named reason; the "second source" instruction is gone from the researcher prompt.

### Task 1.4: The planner emits structured target fields

**Role:** sp-implementer. **Wave:** 1B, parallel with Tasks 1.2 and 1.3. **Depends on:** Task 1.1 merged.

**Owns:** `src/deep_research/agents/planner.py` (`PLAN_INSTRUCTION` 474–597; `_PLAN_REPLY_EXAMPLES` 613–663; `EvidenceTargetDraft` 666–693; `_draft_targets` 2003–2032; `apply_answer_contract` 2810–2879), `tests/test_agents/test_planner.py`.

**Interfaces:**
- Consumes: the `EvidenceTarget` fields from Task 1.1.
- Produces: `EvidenceTargetDraft.measure|unit_dimension|period|kind|geography|organisation: str = ""`; `_structured_fields(target: EvidenceTargetDraft) -> dict[str, object]`; stamped targets carry the fields through `apply_answer_contract`. (`render_planned_targets` is Task 1.3's: it lives in `researcher.py`.)

- [ ] **Step 1: Write the failing tests** (append to `tests/test_agents/test_planner.py`):

```python
from deep_research.agents.planner import (
    EvidenceTargetDraft,
    SubTopicDraft,
    _draft_targets,
    apply_answer_contract,
)
from deep_research.utils.types import AnswerContract, SubTopic


def _structured_draft(**overrides: str) -> SubTopicDraft:
    fields = dict(
        question="How much battery storage capacity did EIA report added in the U.S. in 2024?",
        required_dimensions=["measure: battery storage capacity added"],
        critical=True,
        measure="battery storage power capacity added",
        unit_dimension="power",
        period="2024",
        kind="actual",
        geography="United States",
        organisation="U.S. Energy Information Administration",
    )
    fields.update(overrides)
    return SubTopicDraft(
        title="EIA 2024 additions", rationale="r", search_queries=["q"],
        success_criteria=["c"], priority=1,
        evidence_targets=[EvidenceTargetDraft(**fields)],
    )


def test_draft_targets_carry_the_structured_fields() -> None:
    [target] = _draft_targets(_structured_draft(), "topic-01")
    assert (target.measure, target.unit_dimension, target.period, target.kind) == (
        "battery storage power capacity added", "power", "2024", "actual"
    )
    assert target.organisation == "U.S. Energy Information Administration"


def test_an_unknown_dimension_or_kind_is_stamped_empty() -> None:
    [target] = _draft_targets(
        _structured_draft(unit_dimension="volts", kind="estimate", organisation=" "),
        "topic-01",
    )
    assert (target.unit_dimension, target.kind, target.organisation) == (None, None, None)


def test_the_answer_contract_keeps_the_structured_fields() -> None:
    targets = _draft_targets(_structured_draft(), "topic-01")
    topic = SubTopic(
        coverage_id="topic-01", title="EIA 2024 additions", rationale="r",
        search_queries=["q"], success_criteria=["c"], priority=1,
        evidence_targets=targets,
    )
    contract = AnswerContract(
        question="How much battery storage was added in the U.S. in 2024?",
        scope_statement="United States, as of 2026-09-24, a factual answer.",
        geographic_scope="United States", as_of_date="2026-09-24",
        evidence_period_requirement="the period the question names (2024)",
        assumptions=[], answer_kind="factual",
    )
    [stamped] = apply_answer_contract([topic], contract)
    [target] = stamped.evidence_targets
    assert (target.unit_dimension, target.period, target.kind) == ("power", "2024", "actual")
```

Task 1.3 appends this test to `tests/test_agents/test_researcher.py` (Task 1.3 owns that file and `render_planned_targets`); it is shown here because it checks the fields this task adds. Do not add it in this task:

```python
from deep_research.agents.researcher import render_planned_targets
from tests.evidence_fakes import make_target


def test_planned_targets_render_their_structured_fields() -> None:
    line = render_planned_targets([make_target(organisation="EIA")])
    assert line.startswith("- topic-01-target-01 [topic-01]: How much")
    assert "unit: power" in line and "period: 2024" in line and "organisation: EIA" in line
```

- [ ] **Step 2: Run to see them fail.** Run: `"$PY" -m pytest -q tests/test_agents/test_planner.py -k "structured"`. Expected: FAIL.

- [ ] **Step 3: Draft fields and stamping.** Add the six `str = ""` fields to `EvidenceTargetDraft` (docstring: "the fields a program checks an answer against; empty when the question does not name one"). Add `_structured_fields`:

```python
def _structured_fields(target: EvidenceTargetDraft) -> dict[str, object]:
    """The draft's checkable fields, blank ones and unknown values stamped empty."""

    def text(value: str) -> str | None:
        return " ".join(value.split()) or None

    dimension = (text(target.unit_dimension) or "").casefold()
    kind = (text(target.kind) or "").casefold()
    return {
        "measure": text(target.measure),
        "unit_dimension": dimension if dimension in get_args(UnitDimension) else None,
        "period": text(target.period),
        "kind": kind if kind in get_args(FigureKind) else None,
        "geography": text(target.geography),
        "organisation": text(target.organisation),
    }
```

In `_draft_targets` pass `**_structured_fields(target)` to `EvidenceTarget(...)`. In `apply_answer_contract` (whose loop variable is an already-stamped `EvidenceTarget`) pass `measure=target.measure, unit_dimension=target.unit_dimension, period=target.period, kind=target.kind, geography=target.geography, organisation=target.organisation`. Import `FigureKind` and `UnitDimension`.

- [ ] **Step 4: Prompt and example.** In `PLAN_INSTRUCTION`, after the sentence ending "…never require two sources to agree within a numeric tolerance unless the question itself states that tolerance.\n", insert:

```
"For every target also fill the fields a program checks answers against: "
"measure (what is measured, in words: \"battery storage power capacity "
"added\"), unit_dimension (power for a capacity in kW, MW or GW; energy for "
"MWh or GWh; percent for a share; empty when the answer is not a quantity), "
"period (the year or period the answer applies to, such as \"2024\"), kind "
"(actual for a measured outcome, forecast for a projection; empty when the "
"answer is not a quantity), geography, and organisation (the one body whose "
"figure the target asks for, or empty when any body's figure answers it). "
"Plan one target per organisation, measure, period and kind.\n"
```

In `_PLAN_REPLY_EXAMPLES`, add `"measure"`, `"unit_dimension"`, `"period"`, `"kind"`, `"geography"` and `"organisation"` keys to each example target object with values that fit that example (a ridership count: `"unit_dimension":""`; a capital cost: `""`; periods and organisations as the example's own text states them).


- [ ] **Step 5: Run the tests.** Run: `"$PY" -m pytest -q tests/test_agents/test_planner.py tests/test_agents/test_planner_researcher_seam.py tests/test_agents/test_tool_free_prompts.py tests/test_e2e_evaluation`. Expected: PASS. If a test in `test_planner.py` pins the exact text of `PLAN_INSTRUCTION`, update the pinned text to the new wording (behaviour is unchanged); a failure in a file this task does not own is reported (rule R2).

- [ ] **Step 6: Commit.**

```bash
git add src/deep_research/agents/planner.py tests/test_agents/test_planner.py
git commit -m "feat(planner): targets carry measure, unit dimension, period, kind, geography and organisation"
```

**Acceptance:** a planned target carries the structured fields when the model supplies them; unknown values are stamped empty; nothing else about planning changes in this task.

### Task 1.5: Phase-1 integration: exports, pins and routed test fixes

**Role:** sp-implementer. **Wave:** 1C, parallel with Task 1.6. **Depends on:** Tasks 1.2, 1.3, 1.4 and 3.3 merged.

**Owns:** `src/deep_research/agents/__init__.py`, `tests/test_evaluation/test_config.py`, and any test file the controller routes here under rule R6 (for example a `test_researcher.py` assertion that pinned case-sensitive excerpt matching).

- [ ] **Step 1: Re-export the phase's public names.** In `agents/__init__.py` add `cosmetic_text` to the `from deep_research.agents.evidence import (...)` block; a new block `from deep_research.agents.figures import (Quantity, bare_numbers, dates_in, figure_in_text, parse_figure, quantities_in, same_quantity, unit_dimension, without_dates)`; a new block `from deep_research.agents.evidence_verifier import (EVIDENCE_VERIFIER_NAME, FigureMatch, figure_match, read_text)`; a new block importing every public name of `agents/wording.py` (Task 3.3), and where `agents/__init__.py` imported a moved wording name (`hedge_marker` and others) from `synthesizer`, import it from `wording` instead; `FindingFigureDraft` in the researcher block; every name in `__all__`.

- [ ] **Step 2: Re-pin the prompt fingerprints (PD-17).** Print the values the merged tree computes:

```bash
"$PY" -c "from deep_research.evaluation.config import agent_prompt_fingerprint as f; from deep_research.evaluation.models import AGENT_NAMES as n; print({a: f(a) for a in n})"
```
Set `PINNED_TARGET_PROMPT_FINGERPRINTS["researcher"]`, `["planner"]` and `["synthesizer"]` in `tests/test_evaluation/test_config.py` to the printed values (the other three must print unchanged; if one moved, stop and report it), with one comment line: "Evidence Verifier plan, Tasks 1.3, 1.4 and 3.3: researcher and planner module edits; wording rules moved to agents/wording.py." Delete each historical single-value test that now fails only because one of those three pins moved (PD-17).

- [ ] **Step 3: Apply the routed fixes**, one assertion at a time, each to the §5.1 contract.

- [ ] **Step 4: Run the tests.** Run: `"$PY" -m pytest -q tests/test_imports.py tests/test_evaluation/test_config.py tests/test_agents` and, alone, `"$PY" -m pytest -q tests/test_state.py`. Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add src/deep_research/agents/__init__.py tests/test_evaluation/test_config.py
git commit -m "chore(agents): export phase-1 names; re-pin researcher, planner and synthesizer fingerprints"
```
(Add, by path, any routed test file you changed.)

**Acceptance:** `tests/test_imports.py` and `tests/test_evaluation/test_config.py` pass on the merged Phase-1 tree.

### Task 1.6: Step-1 proof on the audit-2 reads (Gate G1)

**Role:** sp-implementer; runs in `$W` (rule R1); the `--live` probe is an operator step. **Wave:** 1C, parallel with Task 1.5. **Depends on:** Tasks 1.2 and 1.3 merged.

**Files:**
- Create (untracked): `scratch/ev_rebuild_audit2.py`

**Interfaces:**
- Produces: `rebuild(state_path: Path = STATE) -> Rebuilt` with `Rebuilt` fields `findings: list[Finding]`, `reads: dict[str, ReadRecord]`, `sources: list[ScoredSource]`, `sub_topics: list[SubTopic]`, `rejected: list[str]`, `rows: list[str]`. Later harnesses (Tasks 2.3 and 3.6, and Task 4.12 through 3.6) import it as `from scratch.ev_rebuild_audit2 import rebuild`.

- [ ] **Step 1: Write the harness.**

```python
"""Step-1 proof: audit-2's findings rebuilt as new-style findings, then Figure Match.

Offline and read-only: reads %TEMP%/audit2/final_state.json and never writes it.
For each recorded finding it simulates what the new extraction contract asks the
researcher for -- the verbatim sentence(s) of the finding's own read that state
its figures (the snippet, at most MAX_SNIPPET_CHARS), that passage's locator, and
the figures as the snippet writes them -- admits them through the production
build_findings, and runs the production figure_match. Deriving figures from the
recorded content is harness-only: product code never parses content.

Exit 0 only when:
  P1 a finding stating 19.6 GW is admitted with figure 19.6 GW, period 2025;
  P2 an EIA STEO finding (ent.news) stating 14 GW is admitted with figure 14 GW, period 2025;
  P3 every admitted figure-bearing finding has its snippet on its page and every
     figure matched (the architecture audit found 25 of 25).

Usage: PYTHONPATH='src;.' python scratch/ev_rebuild_audit2.py [--state PATH]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from deep_research.agents.evidence_verifier import figure_match
from deep_research.agents.figures import figure_in_text, quantities_in, same_quantity
from deep_research.agents.researcher import (
    FindingDraft,
    FindingFigureDraft,
    SubTopicFindingsDraft,
    build_findings,
)
from deep_research.agents.sources import normalize_source_url
from deep_research.utils.types import (
    MAX_SNIPPET_CHARS,
    EvidenceTarget,
    Finding,
    ReadRecord,
    ScoredSource,
    SubTopic,
)

STATE = Path(tempfile.gettempdir()) / "audit2" / "final_state.json"
QUESTION = (
    "How much grid-scale battery storage capacity was added in the United States "
    "in 2024, and what do the latest forecasts project for 2025?"
)
_BOUNDARY = re.compile(r"(?<=[a-z0-9%)\]][.!?])\s+(?=[A-Z\"\u201c(])")
_FORECAST = re.compile(r"\b(?:project|plan|expect|forecast|outlook|could|will)\w*", re.I)
EIA = "U.S. Energy Information Administration"

# The benchmark plan the new planner is asked for (spec §7.1), as fixture targets.
PLAN: tuple[tuple[str, str, tuple[dict[str, Any], ...]], ...] = (
    ("topic-01", "EIA's 2024 utility-scale battery storage additions", (
        dict(question="How much utility-scale battery storage power capacity did EIA "
             "report added in the United States in 2024?", period="2024", kind="actual",
             organisation=EIA, required=True),)),
    ("topic-02", "EIA's latest forecast of 2025 battery storage additions", (
        dict(question="What battery storage capacity addition does EIA's latest "
             "forecast project for the United States in 2025?", period="2025",
             kind="forecast", organisation=EIA, required=True),)),
    ("topic-03", "Wood Mackenzie's latest 2025 storage forecast", (
        dict(question="What storage installations does Wood Mackenzie's latest "
             "forecast project for the United States in 2025?", period="2025",
             kind="forecast", organisation="Wood Mackenzie", required=True),)),
    ("topic-04", "BloombergNEF's latest 2025 storage forecast", (
        dict(question="What storage additions does BloombergNEF's latest forecast "
             "project for the United States in 2025?", period="2025",
             kind="forecast", organisation="BloombergNEF", required=False),)),
    ("topic-05", "Published 2025 battery storage additions", (
        dict(question="How much battery storage was added in the United States in "
             "2025, as published after the year?", period="2025", kind="actual",
             organisation=None, required=False),)),
)
# audit-2 finding index -> the fixture targets its figures answer (by hand, from the
# audit-2 findings table; cumulative totals and quarterly figures answer none).
TARGETS_BY_FINDING: dict[int, list[str]] = {
    **{index: ["topic-01-target-01"] for index in (0, 2, 15, 16, 18, 19)},
    **{index: ["topic-02-target-01"] for index in (3, 14, 17, 21)},
    **{index: ["topic-03-target-01"] for index in (9, 10, 24, 25)},
    **{index: ["topic-05-target-01"] for index in (5, 12, 22, 23)},
}


def _target(target_id: str, coverage_id: str, fields: dict[str, Any]) -> EvidenceTarget:
    base: dict[str, Any] = dict(
        target_id=target_id, coverage_id=coverage_id,
        measure="battery storage power capacity added", unit_dimension="power",
        geography="United States", **fields,
    )
    if "required_dimensions" in EvidenceTarget.model_fields:  # before Task 5.1
        base.update(required_dimensions=[f"measure: {base['measure']}"],
                    critical=False, support_policy="primary_attribution")
    return EvidenceTarget(**base)


def fixture_plan() -> list[SubTopic]:
    topics: list[SubTopic] = []
    for priority, (coverage_id, title, targets) in enumerate(PLAN, start=1):
        topics.append(SubTopic(
            coverage_id=coverage_id, title=title, rationale="benchmark fixture",
            search_queries=[title], success_criteria=[title], priority=priority,
            evidence_targets=[
                _target(f"{coverage_id}-target-{position:02d}", coverage_id, fields)
                for position, fields in enumerate(targets, start=1)
            ],
        ))
    return topics


@dataclass
class Rebuilt:
    findings: list[Finding] = field(default_factory=list)
    reads: dict[str, ReadRecord] = field(default_factory=dict)
    sources: list[ScoredSource] = field(default_factory=list)
    sub_topics: list[SubTopic] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    rows: list[str] = field(default_factory=list)


def _windows(passage: str) -> list[str]:
    sentences = [part for part in _BOUNDARY.split(passage) if part.strip()]
    windows = list(sentences)
    windows += [f"{a} {b}" for a, b in zip(sentences, sentences[1:])]
    return [w for w in windows if len(w) <= MAX_SNIPPET_CHARS and w in passage]


def _snippet(read: ReadRecord, wanted: list[Any]) -> tuple[str, str, list[tuple[str, str]]] | None:
    """The shortest window stating the most wanted figures, and those figures as written."""
    best: tuple[int, int, str, str] | None = None
    for locator, passage in read.passages.items():
        for window in _windows(passage):
            hits = [q for q in wanted if figure_in_text(q.value_text, q.unit_text, window)]
            if hits and (best is None or (len(hits), -len(window)) > (best[0], best[1])):
                best = (len(hits), -len(window), locator, window)
    if best is None:
        return None
    _, _, locator, window = best
    written: list[tuple[str, str]] = []
    for q in wanted:
        for found in quantities_in(window):
            if same_quantity(q, found):
                written.append((found.value_text, found.unit_text))
                break
    return locator, window, written


def rebuild(state_path: Path = STATE) -> Rebuilt:
    raw = json.loads(state_path.read_text(encoding="utf-8"))
    out = Rebuilt(sub_topics=fixture_plan())
    out.reads = {rid: ReadRecord.model_validate(r) for rid, r in raw["read_records"].items()}
    out.sources = [ScoredSource.model_validate(s) for s in raw.get("evaluated_sources", [])]
    by_url = {read.resolved_url: read for read in out.reads.values()}
    valid_ids = [t.target_id for s in out.sub_topics for t in s.evidence_targets]
    stub = SubTopic(coverage_id="topic-99", title="audit-2", rationale="r",
                    search_queries=["q"], success_criteria=["c"], priority=1)
    for index, item in enumerate(raw["raw_findings"]):
        read = by_url.get(normalize_source_url(item["source_url"]))
        wanted = quantities_in(item["content"])
        picked = _snippet(read, wanted) if read is not None and wanted else None
        if read is None or picked is None:
            out.rows.append(f"{index:>2} {item['source_url'][:60]:60} no read or no figure")
            continue
        locator, snippet, written = picked
        kind = "forecast" if _FORECAST.search(item["content"]) else "actual"
        drafts = [
            FindingFigureDraft(value=value, unit=unit, period=item.get("data_period"), kind=kind)
            for value, unit in written
        ]
        draft_finding = FindingDraft(
            content=item["content"], source_url=read.resolved_url, source_title=read.title,
            confidence=item["confidence"], read_id=read.read_id, locator=locator,
            snippet=snippet, figures=drafts, target_ids=TARGETS_BY_FINDING.get(index, []),
            data_period=item.get("data_period"), statement_date=item.get("statement_date"),
            vintage=item.get("vintage"), attributed_issuer=item.get("attributed_issuer"),
            attribution_quote=item.get("attribution_quote"),
            measure_scope=item.get("measure_scope"), release_date=item.get("release_date"),
        )
        admitted, rejected = build_findings(
            SubTopicFindingsDraft(findings=[draft_finding]),
            sub_topic=stub.model_copy(update={"title": item["related_sub_topic"]}),
            extracted_at=item["extracted_at"], known_urls=list(by_url),
            known_reads=out.reads, valid_target_ids=valid_ids,
        )
        out.rejected += [f"{index}: {reason}" for reason in rejected]
        out.findings += admitted
        figures = ", ".join(f"{f.value} {f.unit}" for f in (admitted[0].figures if admitted else []))
        out.rows.append(f"{index:>2} {read.resolved_url[:60]:60} {'kept' if admitted else 'REFUSED'} {figures}")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=STATE)
    args = parser.parse_args()
    rebuilt = rebuild(args.state)
    print("\n".join(rebuilt.rows))
    if rebuilt.rejected:
        print("rejected:", *rebuilt.rejected, sep="\n  ")
    bearing = [f for f in rebuilt.findings if f.figures]
    matches = [figure_match(f, rebuilt.reads) for f in bearing]
    clean = sum(m.snippet_on_page and all(m.matched) for m in matches)

    def has(value: str, host: str = "") -> bool:
        return any(
            host in f.source_url and any(g.value == value and g.period == "2025" for g in f.figures)
            for f in rebuilt.findings
        )

    checks = {
        "P1 19.6 GW finding kept with structured figure (2025)": has("19.6"),
        "P2 EIA STEO 14 GW finding kept with structured figure (2025)": has("14", "ent.news"),
        f"P3 Figure Match {clean}/{len(bearing)} figure-bearing findings": bool(bearing) and clean == len(bearing),
    }
    for name, passed in checks.items():
        print(("PASS " if passed else "FAIL ") + name)
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Run it.** Run: `"$PY" scratch/ev_rebuild_audit2.py`. Expected: three `PASS` lines, exit 0, and a P3 count of 25/25 (the audit's replay counted 25 figure-bearing findings; finding 11 carries no figure). If a finding is refused, read its reason: a refusal caused by this harness's sentence windows (for example a snippet the window splitter cut badly) is fixed in the harness; a refusal caused by product code is a defect in Tasks 1.1–1.4 and goes back to that task (rule R6).

- [ ] **Step 3: No commit** (the harness is scratch).

- [ ] **Step 4: Add the live extraction probe (F6).** Add `--live` to the harness (operator, off-peak, about 2 minutes, one provider call). It proves the real researcher model honours the new extraction contract (a verbatim snippet of at most 600 characters that contains the figures, plus structured figures) before any later phase builds on it:
  - take two audit-2 reads from `rebuilt.reads`: the EIA page stating 10.4 GW (`eia.gov`) and the ent.news STEO mirror stating 14 GW;
  - build `evidence` for them with the production `build_evidence_unit` (`agents/evidence.py` line 2493; read its signature), one unit per passage, keyed by `evidence_id`;
  - build the request exactly as `ResearcherAgent` does (`researcher.py` lines 1879–1895): `extraction_messages(task, ReActRun(agent_name="researcher"), evidence_chars=settings.agents.evidence_chars, acquisition_context=build_acquisition_context(AcquisitionState(), reads, evidence, limit=settings.agents.evidence_packet_chars), planned_targets=targets)` (use the settings fields the researcher reads for `_evidence_chars` and `_evidence_packet_chars`), where `task` is a `SubTopicTask` for a fixture sub-topic from `fixture_plan()` and `targets` its evidence targets;
  - call `provider.complete_structured(messages, SubTopicFindingsDraft, agent_name="researcher")` once, with the provider built as Task 2.3's `--live` builds it and `model_profile=settings.llm.resolve_for("researcher")`;
  - run the production `build_findings(draft, sub_topic=..., extracted_at=..., known_urls=[read.requested_url for read in reads.values()], known_reads=reads, valid_target_ids=[t.target_id for t in targets])`, then `figure_match(finding, reads)` for each admitted finding;
  - print every admitted finding (read, snippet, figures, match) and every rejection reason, then **P4**: each of the two reads has at least one admitted finding with figures whose snippet is on its page and whose every figure matched. Exit 0 only on `PASS P4`.

  Run the OFF-PEAK CHECK; if `OK`, run `"$PY" scratch/ev_rebuild_audit2.py --live`. Expected: `PASS P4`. A failure (paraphrased snippets, no figures, snippets over 600 characters) goes back to Task 1.3's extraction contract and prompt (rule R6) before Gate G1 passes.

### Gate G1 — end of step 1

Run, in `$W`, once every Phase-1 task is merged and review-clean (rule R8):

```bash
"$PY" scratch/ev_rebuild_audit2.py
"$PY" scratch/ev_rebuild_audit2.py --live      # operator, off-peak only (F6)
"$PY" -m pytest -q tests --ignore=tests/test_state.py
"$PY" -m pytest -q tests/test_state.py
"$PY" -m deep_research.e2e_evaluation suite --tier controlled --repetitions 1
"$PY" -c "import deep_research.agents, deep_research.graph, deep_research.runtime, deep_research.api, deep_research.cli, deep_research.main, deep_research.evaluation, deep_research.e2e_evaluation"
```

**Pass condition:** the harness prints `PASS` for P1, P2 and P3 (25/25) and exits 0; the `--live` probe prints `PASS P4` (the real researcher's extraction contract holds on real pages, F6); both suite invocations are green, or show only the failures recorded in `scratch/baseline-*.txt`; the e2e real-agent suite is accepted (the old pipeline still runs, PD-3). Do not start Phase 2 until G1 passes.

---

## Phase 2 — Evidence Verifier (spec step 2)

Phase goal: a finding's figures come out of the Evidence Verifier with a verified period, scope, attribution and kind, or with a named drop reason; nothing the AI says is kept unless the page carries it. The agent exists and is unit-tested, but the graph does not run it yet (PD-3).

### Task 2.1: The Context Check and the Evidence Verifier agent

**Role:** sp-hard-implementer. **Wave:** 2A, parallel with Tasks 3.1 and 3.2 (the approved R8 exception; no shared file). **Depends on:** Gate G1 (Task 3.3 is merged in wave 1B).

**Owns:** `src/deep_research/agents/evidence.py` (move three attribution helpers in; add `relay_attribution_on_page` and `own_organisation_on_page`), `src/deep_research/agents/researcher.py` (delete the moved helpers, lines 928–984; import them from `evidence.py`), `src/deep_research/agents/evidence_verifier.py`, `tests/test_agents/test_evidence_verifier.py`, `tests/test_agents/test_evidence.py`, `tests/test_agents/test_researcher.py`, `tests/test_agents/test_tool_free_prompts.py`.

**Superseded by D7/D8:** the D8 contract (`SDD/d8-contract.md`) replaces this task's bounds (15 per call, 4 in flight) with 5 per call and 8 in flight, deletes the `not_matched` rescue branch and every code check that a figure's value occurs in the evidence words (the AI rejects a figure its snippet or passage does not state; code keeps only "`evidence_words` is on the page"), keeps `correction_not_on_page`, the relay cue rules and the page-owner naming, and adds the Statement Check (`check_statements`, §5.4) beside the Context Check. Task 4.1's config block and Task 4.13 make the bounds config values (PD-12, PD-27). Apply the contract where this body differs; do not rewrite the body.

**Interfaces:**
- Consumes: `figure_match`, `read_text`, `figure_in_text` (Task 1.2); the verification types (Task 1.1); `ScoredSource` and its `identity_anchors` (existing, `utils/types.py` lines 496–535); `first_party_host_evidences_issuer`, `_issuer_name_pattern`, `_institutional_domain_label`, `_identity_words`, `_document_text` (existing, `evidence.py` lines 1031, 896, 927, 740); `publisher_identity` (`agents/sources.py`); `finding_fingerprint`, `deduplicate_findings` (`agents/identity.py`); `stated_role` (`agents/wording.py`, Task 3.3, merged in wave 1B); `render_structured_reply_format` (`agents/prompts.py` line 73); `agent_error` (`agents/errors.py`); `agent_event` (`agents/events.py`).
- Produces: everything listed for `evidence_verifier.py` and the Task 2.1 lines of `evidence.py` in "Shared interfaces". Nothing is re-exported here (Task 2.2 does it).

**Attribution resolution (PD-8)** — `resolve_attribution` returns the first row that applies:

| Context Check proposed | Code checks | Result |
|---|---|---|
| `relayed`, organisation X | X is the page's own publisher | `own`, X |
| `relayed`, organisation X | X is named in the snippet's passage beside an attribution cue (`relay_attribution_on_page`) | `relayed`, X |
| anything | the researcher admitted `attributed_issuer` Y (its quote is on the page) | `own` Y if Y is the page's own publisher, else `relayed` Y |
| `own`, organisation X | X is the page's own publisher | `own`, X |
| `own` with X unverified, or no reply at all | — | `own`, the page owner: the Source Evaluator's validated issuer for the read (`evaluated_issuer`, PD-25), else the host (`page_owner(read)`, e.g. `eia.gov`) |
| `unattributed`, or `relayed` with X unverified | — | `unattributed`, the page owner as in the row above |

"The page's own publisher" is `_owns_page(read, X, issuer)`: the Source Evaluator's validated issuer for the read names X (PD-25), or else `own_organisation_on_page(read, X)` (PD-18), which Step 3 adds to `evidence.py`. The code below calls exactly that; nothing in `evidence_verifier.py` calls `first_party_host_evidences_issuer` directly.

```python
def own_organisation_on_page(read: ReadRecord, organisation: str) -> bool:
    """PD-18: the read is ``organisation``'s own page.

    The first-party rule, or a government/education host whose registrable
    label spells the name (initials, or the words run together, a leading
    "U.S." dropped) while the page itself names it: eia.gov and "U.S. Energy
    Information Administration". A lookalike on a suffix anyone can buy
    (eia.news) never qualifies, because _institutional_domain_label refuses it.
    """
    if first_party_host_evidences_issuer(read, organisation):
        return True
    label = _institutional_domain_label(read)
    words = _identity_words(organisation).split()
    core = [word for word in words if word not in {"u", "s", "us"}]
    if not label or not core:
        return False
    if label not in {"".join(words), "".join(core), "".join(word[0] for word in core)}:
        return False
    name = re.compile(rf"(?<![A-Za-z0-9]){_issuer_name_pattern(organisation)}(?![A-Za-z0-9])", re.IGNORECASE)
    return bool(name.search(f"{read.title} {_document_text(read)}"))
```

When the helpers move (Step 3), `ATTRIBUTION_CUE_PATTERN` also gains `sources?\s*:` as a cue ("Data source: U.S. Energy Information Administration"), so a mirrored or relayed document that credits its originator in a source line is labelled a relay of that originator. Add to Step 1: `test_own_organisation_on_an_agency_host` (eia.gov titled "U.S. battery capacity increased 66% in 2024", text naming "U.S. Energy Information Administration" → True; the same page on `eia.news` → False) and `test_a_source_line_is_an_attribution_cue`.

A relay is therefore never credited to an organisation the page does not credit, and an organisation name is never printed as "own" unless the page is that organisation's own.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_agents/test_evidence_verifier.py`):

```python
import pytest

from deep_research.agents.evidence_verifier import (
    ContextCheckDraft,
    ContextItem,
    FigureCheckDraft,
    context_passage,
    evaluated_issuer,
    figure_match,
    resolve_attribution,
    verify_finding,
)
from deep_research.utils.types import ScoredSource   # fill any other field it requires

WOODMAC_URL = "https://www.woodmac.com/press-releases/2025-us-energy-storage"
WOODMAC_PAGE = (
    "The U.S. energy storage market hit a record 18.9 gigawatts of battery energy "
    "storage system installations in 2025, a 52% increase over 2024, across all "
    "segments. Grid-scale storage installations are forecasted to reach 13.3 GW in 2025."
)
RELAY_URL = "https://www.utilitydive.com/news/storage-2025"
RELAY_PAGE = (
    "According to Wood Mackenzie, utility-scale installations reached 16 GW in 2025. "
    "Analysts expect further growth."
)
SNIPPET_189 = (
    "The U.S. energy storage market hit a record 18.9 gigawatts of battery energy "
    "storage system installations in 2025"
)


def _item(read, finding) -> ContextItem:
    return ContextItem(
        label="F01", finding=finding, read=read,
        passage=context_passage(read, finding.locator, finding.snippet),
        match=figure_match(finding, {read.read_id: read}),
    )


def _reply(**overrides: object) -> FigureCheckDraft:
    fields = dict(finding="F01", figure=1, period="2025", scope=None, attribution="own",
                  organisation="Wood Mackenzie", kind="actual",
                  evidence_words=SNIPPET_189, verdict="confirm", reason="As stated.")
    fields.update(overrides)
    return FigureCheckDraft(**fields)


def _woodmac_finding(**fields: object):
    read = make_read(WOODMAC_PAGE, url=WOODMAC_URL, title="2025 storage record | Wood Mackenzie")
    finding = make_finding(read, SNIPPET_189,
                           figures=[figure("18.9", "gigawatts", "2025", "actual")], **fields)
    return read, finding


def test_a_scope_correction_the_page_carries_is_applied() -> None:
    read, finding = _woodmac_finding(measure_scope="Grid-scale")
    words = SNIPPET_189 + ", a 52% increase over 2024, across all segments"
    result = verify_finding(_item(read, finding), {1: _reply(
        scope="all segments", evidence_words=words, verdict="correct")})
    assert result.status == "verified_corrected"
    [figure_result] = result.figure_results
    assert figure_result.kept and figure_result.context.scope == "all segments"


def test_invented_evidence_words_are_rejected() -> None:
    read, finding = _woodmac_finding()
    result = verify_finding(_item(read, finding), {1: _reply(
        evidence_words="installations of 18.9 GW of grid-scale batteries in 2025")})
    assert result.status == "dropped"
    assert result.figure_results[0].dropped_reason == "evidence_not_on_page"


def test_a_correction_the_page_does_not_carry_drops_the_figure() -> None:
    read, finding = _woodmac_finding()
    result = verify_finding(_item(read, finding), {1: _reply(period="2026", verdict="correct")})
    assert result.figure_results[0].dropped_reason == "correction_not_on_page"


def test_a_not_matched_figure_is_rescued_only_by_its_evidence_words() -> None:
    read, finding = _woodmac_finding()
    finding = finding.model_copy(update={"figures": [figure("13.3", "GW", "2025", "forecast")]})
    item = _item(read, finding)
    assert item.match.matched == (False,)
    kept = verify_finding(item, {1: _reply(
        kind="forecast", evidence_words="Grid-scale storage installations are forecasted to reach 13.3 GW in 2025")})
    assert kept.figure_results[0].kept
    dropped = verify_finding(item, {1: _reply(kind="forecast")})
    assert dropped.figure_results[0].dropped_reason == "figure_not_in_evidence"


def test_a_rejected_figure_drops_and_the_rest_survive() -> None:
    read, finding = _woodmac_finding()
    finding = finding.model_copy(update={"figures": [
        figure("18.9", "gigawatts", "2025", "actual"), figure("52", "%", "2025", "actual")]})
    snippet = SNIPPET_189 + ", a 52% increase over 2024"
    finding = finding.model_copy(update={"snippet": snippet})
    result = verify_finding(_item(read, finding), {
        1: _reply(evidence_words=snippet),
        2: _reply(figure=2, verdict="reject", evidence_words=snippet, reason="A growth rate, not a capacity."),
    })
    assert result.status == "verified_corrected"
    assert [r.kept for r in result.figure_results] == [True, False]


def test_a_missing_reply_leaves_a_matched_figure_unchecked() -> None:
    read, finding = _woodmac_finding()
    result = verify_finding(_item(read, finding), None)
    assert result.status == "verified" and result.context_unchecked
    assert result.figure_results[0].context.attribution == "own"


def test_a_relay_needs_its_originator_named_on_the_page() -> None:
    read = make_read(RELAY_PAGE, url=RELAY_URL, title="Storage in 2025 | Utility Dive")
    finding = make_finding(read, "According to Wood Mackenzie, utility-scale installations reached 16 GW in 2025.")
    assert resolve_attribution(proposed="relayed", organisation="Wood Mackenzie",
                               finding=finding, read=read, issuer=None) == ("relayed", "Wood Mackenzie")
    assert resolve_attribution(proposed="relayed", organisation="BloombergNEF",
                               finding=finding, read=read, issuer=None) == ("unattributed", "utilitydive.com")


def test_an_admitted_attribution_makes_a_relay() -> None:
    read = make_read(RELAY_PAGE, url=RELAY_URL, title="Storage in 2025 | Utility Dive")
    finding = make_finding(read, "utility-scale installations reached 16 GW in 2025.",
                           attributed_issuer="Wood Mackenzie",
                           attribution_quote="According to Wood Mackenzie")
    assert resolve_attribution(proposed="unattributed", organisation=None,
                               finding=finding, read=read, issuer=None) == ("relayed", "Wood Mackenzie")


def test_the_source_evaluators_issuer_names_the_pages_own_organisation() -> None:
    # PD-25: a .com page with no copyright line is Wood Mackenzie's own when the
    # Source Evaluator validated that issuer for the read; without it, the host.
    read = make_read("The U.S. storage market will install 15 GW in 2025, a record year.",
                     url="https://www.woodmac.com/press-releases/q1-2025", title="US storage outlook")
    finding = make_finding(read, "The U.S. storage market will install 15 GW in 2025, a record year.")
    source = ScoredSource(url=read.resolved_url, title=read.title, identity_anchors={"issuer": "Wood Mackenzie"})
    issuer = evaluated_issuer([source], read)
    assert issuer == "Wood Mackenzie"
    assert resolve_attribution(proposed="own", organisation="Wood Mackenzie",
                               finding=finding, read=read, issuer=issuer) == ("own", "Wood Mackenzie")
    assert resolve_attribution(proposed=None, organisation=None,
                               finding=finding, read=read, issuer=issuer) == ("own", "Wood Mackenzie")
    assert resolve_attribution(proposed="own", organisation="Wood Mackenzie",
                               finding=finding, read=read, issuer=None) == ("own", "woodmac.com")
```

Also add agent-level tests (async; build the agent like `_synthesizer` in `tests/test_agents/test_synthesizer.py` lines 314–331, with `ScriptedCompleter` from `tests/agent_fakes.py`, and run it inside the same tracker/session scope that file's `run` tests use):

```python
@pytest.mark.asyncio
async def test_findings_are_checked_fifteen_per_call(...) -> None:
    # 20 figure-bearing findings on one read -> exactly 2 ContextCheckDraft calls
    # (completer.calls names ContextCheckDraft twice); every finding verified.

@pytest.mark.asyncio
async def test_failed_batch_marks_findings_context_unchecked(...) -> None:
    # the completer raises ProviderError for the only batch: every matched figure
    # is kept with context_unchecked=True, status "verified", and state_update
    # errors carry one "evidence_verifier_context_check_failed".

@pytest.mark.asyncio
async def test_a_truncated_batch_is_asked_again_once_in_halves(...) -> None:
    # 4 findings; outputs = [ProviderOutputLimitError(...), reply_for_first_2, reply_for_last_2]
    # -> three calls, all four verified, no error recorded.

@pytest.mark.asyncio
async def test_only_new_findings_are_verified(...) -> None:
    # state.verified_findings holds F1 (verified); raw_findings holds F1 and F2 ->
    # one call whose request names only F2's snippet; the update's verified_findings
    # is [F1, F2] with F1 unchanged.
```

Write those four bodies in full, following the comments, using `make_read`/`make_finding` and replies built with a callable queued in `ScriptedCompleter(outputs=[...])` that reads the batch labels from the request (`F01`, `F02`, …) and answers one `FigureCheckDraft` per listed figure. Construct `ProviderOutputLimitError` and `ProviderError` the way `tests/test_agents/test_synthesizer.py` constructs them.

Append to `tests/test_agents/test_evidence.py`:

```python
from deep_research.agents.evidence import relay_attribution_on_page
from tests.evidence_fakes import make_read


def test_a_relay_is_read_from_the_cue_beside_the_name() -> None:
    read = make_read(
        "Utility-scale additions reached 16 GW in 2025, according to Wood Mackenzie. "
        "Unlike BloombergNEF, the firm counts all segments.",
        url="https://www.utilitydive.com/news/x", title="x",
    )
    assert relay_attribution_on_page(read, "page-1-chunk-0", "Wood Mackenzie")
    assert not relay_attribution_on_page(read, "page-1-chunk-0", "BloombergNEF")
```

- [ ] **Step 2: Run to see them fail.** Run: `"$PY" -m pytest -q tests/test_agents/test_evidence_verifier.py tests/test_agents/test_evidence.py -k "relay or context or correction or evidence_words or rejected or reply or fifteen or failed_batch or truncated or new_findings"`. Expected: FAIL (`ImportError`).

- [ ] **Step 3: Move the attribution helpers.** Move `_neighbouring_passage_text`, `_ATTRIBUTION_CUE_PATTERN`, `_POSSESSIVE_MARK`, `_ATTRIBUTION_CUE_REACH` and `_attribution_cue_adjacent` (with their comments) from `researcher.py` to `evidence.py`, renaming the public three to `neighbouring_passage_text`, `ATTRIBUTION_CUE_PATTERN` and `attribution_cue_adjacent`. Keep `neighbouring_passage_text`'s existing guard (`if locator not in keys: return ""`, `researcher.py` lines 941–942): a stale or missing locator never reaches `keys.index`, so it cannot raise (F9). In `researcher.py` import those three from `deep_research.agents.evidence` and update the two call sites in `_admitted_attribution`. Then add to `evidence.py`:

```python
def relay_attribution_on_page(read: ReadRecord, locator: str, organisation: str) -> bool:
    """True when the passage around ``locator`` credits ``organisation`` for a figure.

    The same rule a researcher attribution quote is admitted under: the name is
    in the snippet's own passage or its immediate neighbour, with an attribution
    cue ("according to", "reported by", a possessive, ...) beside it.
    """
    # F9: Figure Match admits a snippet found anywhere on the page, so its locator
    # may be stale; then the whole page is the passage (the researcher's own use
    # of neighbouring_passage_text keeps its "" for an unknown locator).
    passage = neighbouring_passage_text(read, locator) or _document_text(read)
    name = organisation.strip()
    if not passage or not name:
        return False
    pattern = re.compile(_issuer_name_pattern(name), re.IGNORECASE)
    return any(attribution_cue_adjacent(passage, match) for match in pattern.finditer(passage))
```

Add to `tests/test_agents/test_evidence.py`:

```python
def test_a_stale_locator_neither_raises_nor_hides_a_relay() -> None:
    read = make_read("According to Wood Mackenzie, utility-scale installations reached 16 GW in 2025.")
    assert neighbouring_passage_text(read, "no-such-locator") == ""
    assert relay_attribution_on_page(read, "no-such-locator", "Wood Mackenzie")
    assert not relay_attribution_on_page(read, "no-such-locator", "BloombergNEF")
```

- [ ] **Step 4: Implement the Context Check** in `evidence_verifier.py` (append below `figure_match`). Imports: `asyncio`, `dataclasses.replace`, `Literal`, `Sequence`, `ValidationError` (pydantic), `ProviderError`, `ProviderOutputLimitError`, `StructuredOutputError` (from `deep_research.providers`), `ChatMessage`, `render_structured_reply_format`, `BaseAgent`, `AgentRun`, `AgentTask`, `ReActRun`, `agent_error`, `agent_event`, the types (with `ScoredSource`), `finding_fingerprint`, `deduplicate_findings`, `publisher_identity`, `cosmetic_text`, `neighbouring_passage_text`, `own_organisation_on_page`, `relay_attribution_on_page` and `_identity_words` from `deep_research.agents.evidence`, and `stated_role` from `deep_research.agents.wording` (Task 3.3, merged in wave 1B). The code below calls `own_organisation_on_page` through `_owns_page` everywhere; it never calls `first_party_host_evidences_issuer` directly (F9).

```python
CONTEXT_CHECK_BATCH_SIZE = 5       # D8; Task 4.13 reads agents.verifier_batch_size (PD-12)
CONTEXT_CHECK_CONCURRENCY = 8      # D8; Task 4.13 reads agents.verifier_concurrency (PD-12)
CONTEXT_PASSAGE_CHARS = 3000

CONTEXT_CHECK_SYSTEM_PROMPT = (
    "You check the context of figures that a research system copied from web "
    "pages. For each figure you are shown the snippet it was copied from, the "
    "surrounding passage of the same page, and the fields the extractor recorded. "
    "You have no tools and no web access: judge only from the passage printed "
    "for that figure."
)

CONTEXT_CHECK_INSTRUCTION = (
    "Return one entry in figures for every figure listed, naming it by its "
    "finding label and figure number. For each figure give:\n"
    "- period: the period the page says the figure applies to, as the page "
    "writes it (\"2024\", \"2025\", \"Q3 2025\"); repeat the recorded period when "
    "the page confirms it.\n"
    "- scope: the segment or basis the page says the figure covers, as the page "
    "writes it (\"all segments\", \"utility-scale\", \"utility, C&I, and "
    "residential\"), or null when the page states none.\n"
    "- attribution: own when the page states the figure as its publisher's own; "
    "relayed when the page credits another organisation for it (\"according "
    "to\", \"reported by\", a possessive); unattributed when the page states it "
    "without saying whose it is.\n"
    "- organisation: for own, the page's publisher as the page names itself; for "
    "relayed, the organisation the page credits, exactly as the page names it; "
    "null for unattributed.\n"
    "- kind: actual for a measured or reported outcome; forecast for a "
    "projection, plan, expectation or target.\n"
    "- evidence_words: the exact words of the passage that state this figure "
    "with this period and scope, copied character for character, one sentence "
    "or less. Words that are not on the page make the figure unusable.\n"
    "- verdict: confirm when the recorded period, scope and kind are right; "
    "correct when you changed any of them; reject when the passage does not "
    "state this figure, or states it for something else.\n"
    "- reason: one short sentence.\n"
    "A correction is kept only when your corrected wording appears in "
    "evidence_words or in the passage. Never guess a period, a scope or an "
    "organisation the passage does not state."
)

_CONTEXT_CHECK_REPLY_EXAMPLES = (
    (
        "Example input: F01, figure 1: 12 percent | recorded period 2024 | recorded "
        "kind actual; passage \"The measured reduction was 12 percent in 2024, "
        "according to the Example Statistical Agency, across all classes.\"",
        '{"figures":[{"finding":"F01","figure":1,"period":"2024","scope":"all '
        'classes","attribution":"relayed","organisation":"Example Statistical '
        'Agency","kind":"actual","evidence_words":"The measured reduction was 12 '
        'percent in 2024","verdict":"correct","reason":"The page states the '
        'scope and credits the agency."}]}',
    ),
)


class FigureCheckDraft(ContractModel):
    """One figure's context as the model returns it, before code enforcement."""

    finding: str
    figure: int
    period: str | None = None
    scope: str | None = None
    attribution: FigureAttribution
    organisation: str | None = None
    kind: FigureKind
    evidence_words: str
    verdict: Literal["confirm", "correct", "reject"]
    reason: str


class ContextCheckDraft(ContractModel):
    """The provider-facing reply for one batch."""

    figures: list[FigureCheckDraft]


@dataclass(frozen=True)
class ContextItem:
    """One finding in a Context Check batch, with its page and Figure Match."""

    label: str
    finding: Finding
    read: ReadRecord
    passage: str
    match: FigureMatch
    issuer: str | None = None   # evaluated_issuer(...) for the read (PD-25)


class VerifiedFindings(ContractModel):
    """What one Evidence Verifier run judged. Never sent to the provider."""

    findings: list[Finding] = Field(default_factory=list)


def page_owner(read: ReadRecord) -> str:
    """The registrable host that served the page (``eia.gov``)."""
    return publisher_identity(read.resolved_url)


def context_passage(read: ReadRecord, locator: str | None, snippet: str | None) -> str:
    """§5.2's bounded passage: the snippet's passage and its neighbours, centred on it."""
    text = neighbouring_passage_text(read, locator or "") or read_text(read)
    if len(text) <= CONTEXT_PASSAGE_CHARS:
        return text
    anchor = text.casefold().find((snippet or "")[:40].casefold())
    start = max(0, anchor - CONTEXT_PASSAGE_CHARS // 2)
    return text[start : start + CONTEXT_PASSAGE_CHARS]


def evaluated_issuer(sources: Sequence[ScoredSource], read: ReadRecord) -> str | None:
    """PD-25: the issuer the Source Evaluator validated for this read, if any.

    ``identity_anchors`` holds only the anchors the read was shown to evidence,
    so this name is the page's own organisation, not a guess from its host.
    """
    urls = {read.requested_url, read.resolved_url}
    for source in sources:
        if source.url in urls:
            anchor = source.identity_anchors.get("issuer")
            if isinstance(anchor, list):
                anchor = next(iter(anchor), None)
            if isinstance(anchor, str) and anchor.strip():
                return anchor.strip()
            return None
    return None


def _owns_page(read: ReadRecord, organisation: str, issuer: str | None) -> bool:
    """PD-25 first (the validated issuer names it), then PD-18 (code confirms it)."""
    if issuer and _identity_words(issuer) == _identity_words(organisation):
        return True
    return own_organisation_on_page(read, organisation)


def resolve_attribution(
    *,
    proposed: FigureAttribution | None,
    organisation: str | None,
    finding: Finding,
    read: ReadRecord,
    issuer: str | None,
) -> tuple[FigureAttribution, str]:
    """PD-8: the Context Check proposes, the page's own words decide.

    ``issuer`` is ``evaluated_issuer(...)`` for the read (PD-25), or ``None``.
    """
    name = (organisation or "").strip()
    if proposed == "relayed" and name:
        if _owns_page(read, name, issuer):
            return "own", name
        if relay_attribution_on_page(read, finding.locator or "", name):
            return "relayed", name
    admitted = finding.attributed_issuer
    if admitted:
        if _owns_page(read, admitted, issuer):
            return "own", admitted
        return "relayed", admitted
    if proposed == "own" and name and _owns_page(read, name, issuer):
        return "own", name
    owner = issuer or page_owner(read)
    if proposed in (None, "own"):
        return "own", owner
    return "unattributed", owner


def unchecked_context(finding: Finding, figure: FindingFigure, read: ReadRecord,
                      issuer: str | None) -> FigureContext:
    """The recorded fields, used when no Context Check reply exists for a figure.

    The finding keeps its Figure Match status (verified or verified_corrected) and
    carries ``context_unchecked``; its label says "unchecked context" (PD-26).
    """
    attribution, organisation = resolve_attribution(
        proposed=None, organisation=None, finding=finding, read=read, issuer=issuer
    )
    kind = figure.kind or (
        "forecast" if stated_role(finding.snippet or "") == "forecast" else "actual"
    )
    return FigureContext(
        period=figure.period or finding.data_period,
        scope=finding.measure_scope,
        attribution=attribution,
        organisation=organisation,
        kind=kind,
    )


def _differs(proposed: str | None, recorded: str | None) -> bool:
    return bool(proposed) and (
        recorded is None or cosmetic_text(proposed) != cosmetic_text(recorded)
    )


def _checked(item: ContextItem, figure: FindingFigure, matched: bool,
             reply: FigureCheckDraft) -> FigureResult:
    words = reply.evidence_words.strip()

    def drop(reason: FigureDropReason) -> FigureResult:
        return FigureResult(figure=figure, matched=matched, evidence_words=words or None,
                            dropped_reason=reason, reason=reply.reason or None)

    if reply.verdict == "reject":
        return drop("context_rejected")
    if not words or not excerpt_matches(read_text(item.read), words):
        return drop("evidence_not_on_page")
    if not matched and not figure_in_text(figure.value, figure.unit, words):
        return drop("figure_not_in_evidence")
    finding = item.finding
    period, scope, corrected = figure.period or finding.data_period, finding.measure_scope, False
    for proposed, current, field in ((reply.period, period, "period"), (reply.scope, scope, "scope")):
        if not _differs(proposed, current):
            continue
        if not (excerpt_matches(words, proposed) or excerpt_matches(item.passage, proposed)):
            return drop("correction_not_on_page")
        corrected = True
        if field == "period":
            period = proposed
        else:
            scope = proposed
    attribution, organisation = resolve_attribution(
        proposed=reply.attribution, organisation=reply.organisation,
        finding=finding, read=item.read, issuer=item.issuer,
    )
    corrected = corrected or (figure.kind is not None and figure.kind != reply.kind)
    return FigureResult(
        figure=figure, matched=matched, evidence_words=words, corrected=corrected,
        reason=reply.reason or None,
        context=FigureContext(period=period, scope=scope, attribution=attribution,
                              organisation=organisation, kind=reply.kind),
    )


def verify_finding(
    item: ContextItem, replies: Mapping[int, FigureCheckDraft] | None
) -> FindingVerification:
    """§5.2's enforcement for one figure-bearing finding whose snippet is on its page.

    ``replies`` maps a 1-based figure number to its reply; ``None`` means the
    batch's Context Check failed. A figure with no reply keeps its Figure Match
    result and marks the finding ``context_unchecked``; it is never promoted.
    """
    results: list[FigureResult] = []
    unchecked = False
    for position, (figure, matched) in enumerate(
        zip(item.finding.figures, item.match.matched), start=1
    ):
        reply = None if replies is None else replies.get(position)
        if reply is not None:
            results.append(_checked(item, figure, matched, reply))
            continue
        unchecked = True
        results.append(
            FigureResult(figure=figure, matched=True,
                         context=unchecked_context(item.finding, figure, item.read, item.issuer))
            if matched
            else FigureResult(figure=figure, matched=False, dropped_reason="context_unavailable")
        )
    if not any(result.kept for result in results):
        return FindingVerification(status="dropped", figure_results=results,
                                   dropped_reason="all_figures_dropped",
                                   context_unchecked=unchecked)
    corrected = any(result.corrected or not result.kept for result in results)
    return FindingVerification(
        status="verified_corrected" if corrected else "verified",
        figure_results=results, context_unchecked=unchecked,
    )


def context_check_messages(items: Sequence[ContextItem]) -> list[ChatMessage]:
    """One batch's request: every finding's snippet, passage, fields and figures."""
    blocks: list[str] = []
    for item in items:
        finding = item.finding
        recorded = "; ".join(
            f"{name}: {value}"
            for name, value in (
                ("period", finding.data_period), ("scope", finding.measure_scope),
                ("attributed to", finding.attributed_issuer),
                ("release date", finding.release_date), ("vintage", finding.vintage),
                ("statement date", finding.statement_date),
            )
            if value
        ) or "none"
        figures = "\n".join(
            f"  figure {number}: {figure.value} {figure.unit} | recorded period "
            f"{figure.period or finding.data_period or 'none'} | recorded kind "
            f"{figure.kind or 'none'} | "
            f"{'in the snippet' if matched else 'not found in the snippet'}"
            for number, (figure, matched) in enumerate(
                zip(finding.figures, item.match.matched), start=1
            )
        )
        blocks.append(
            f"## {item.label}\npage: {item.read.title} ({page_owner(item.read)})\n"
            f"recorded fields: {recorded}\nfigures:\n{figures}\n"
            f"snippet: {finding.snippet}\npassage: {item.passage}"
        )
    return [
        ChatMessage(role="developer", content=CONTEXT_CHECK_SYSTEM_PROMPT),
        ChatMessage(
            role="user",
            content="\n\n".join(
                (
                    "# Figures to check\n" + "\n\n".join(blocks),
                    f"# Response contract\n{CONTEXT_CHECK_INSTRUCTION}",
                    "# Reply format\n"
                    + render_structured_reply_format(_CONTEXT_CHECK_REPLY_EXAMPLES),
                )
            ),
        ),
    ]
```

Then the agent, following `SynthesizerAgent`'s skeleton (`synthesizer.py` 4349–4714: constructor signature, `output_schema`, `system_prompt`, `build_task`, `finalize` returning `None`, `state_update`, `run`):

```python
class EvidenceVerifierAgent(BaseAgent[VerifiedFindings]):
    """Figure Match, then one batched, tool-free Context Check (spec §5)."""

    name = EVIDENCE_VERIFIER_NAME
    description = "Check each finding's snippet and figures against its page, then their context."
    allowed_tools = ()

    async def run(self, state: ResearchState) -> AgentRun[VerifiedFindings]:
        done = {finding_fingerprint(finding) for finding in state.verified_findings}
        pending = [
            finding for finding in deduplicate_findings(state.raw_findings)
            if finding_fingerprint(finding) not in done
        ]
        errors: list[ResearchError] = []
        async with self.tracker.agent_span(self.name) as span:
            judged = await self.verify(pending, state.read_records, errors, state.evaluated_sources)
            span.set_outputs({"agent_name": self.name, "findings": len(judged)})
        snapshot = [*state.verified_findings, *judged]
        react = ReActRun(agent_name=self.name, stop_reason="finished", errors=errors)
        return AgentRun(
            agent_name=self.name, result=VerifiedFindings(findings=judged), react=react,
            errors=errors,
            state_update={"verified_findings": snapshot, "errors": errors,
                          "events": [evidence_verified_event(judged)]},
            call_fingerprints=dict(self._call_fingerprints),
        )

    async def verify(self, findings: Sequence[Finding], reads: Mapping[str, ReadRecord],
                     errors: list[ResearchError], sources: Sequence[ScoredSource] = ()) -> list[Finding]:
        results: dict[str, FindingVerification] = {}
        items: list[ContextItem] = []
        for finding in findings:
            key, match = finding_fingerprint(finding), figure_match(finding, reads)
            if not match.read_found:
                results[key] = FindingVerification(status="dropped", dropped_reason="read_not_found")
            elif not match.snippet_on_page:
                results[key] = FindingVerification(status="dropped", dropped_reason="snippet_not_on_page")
            elif not finding.figures:
                results[key] = FindingVerification(status="verified")
            else:
                read = reads[finding.read_id or ""]
                items.append(ContextItem(label="", finding=finding, read=read,
                                         passage=context_passage(read, finding.locator, finding.snippet),
                                         match=match, issuer=evaluated_issuer(sources, read)))
        batches = [items[i : i + CONTEXT_CHECK_BATCH_SIZE]
                   for i in range(0, len(items), CONTEXT_CHECK_BATCH_SIZE)]
        gate = asyncio.Semaphore(CONTEXT_CHECK_CONCURRENCY)

        async def one(batch: list[ContextItem]) -> dict[str, dict[int, FigureCheckDraft] | None]:
            async with gate:
                return await self._check(batch, errors, split=True)

        for replies in await asyncio.gather(*(one(batch) for batch in batches)):
            for item in items:
                key = finding_fingerprint(item.finding)
                if key in replies:
                    results[key] = verify_finding(item, replies[key])
        return [f.model_copy(update={"verification": results[finding_fingerprint(f)]})
                for f in findings]

    async def _check(self, batch: list[ContextItem], errors: list[ResearchError], *,
                     split: bool) -> dict[str, dict[int, FigureCheckDraft] | None]:
        """One call; on truncation or an invalid reply, one re-ask in two halves."""
        labelled = [replace(item, label=f"F{number:02d}") for number, item in enumerate(batch, 1)]
        try:
            reply = await self.provider.complete_structured(
                context_check_messages(labelled), ContextCheckDraft, agent_name=self.name
            )
            reply = ContextCheckDraft.model_validate(
                reply.model_dump() if isinstance(reply, ContextCheckDraft) else reply
            )
        except (ProviderOutputLimitError, StructuredOutputError, ValidationError) as error:
            if split and len(labelled) > 1:
                half = len(labelled) // 2
                first = await self._check(labelled[:half], errors, split=False)
                return {**first, **await self._check(labelled[half:], errors, split=False)}
            errors.append(context_check_failed_error(len(labelled), error))
            return {finding_fingerprint(item.finding): None for item in labelled}
        except ProviderError as error:
            errors.append(context_check_failed_error(len(labelled), error))
            return {finding_fingerprint(item.finding): None for item in labelled}
        by_label = {item.label: item for item in labelled}
        replies: dict[str, dict[int, FigureCheckDraft] | None] = {
            finding_fingerprint(item.finding): {} for item in labelled
        }
        ignored = 0
        for draft in reply.figures:
            item = by_label.get(draft.finding.strip())
            if item is None or not 1 <= draft.figure <= len(item.finding.figures):
                ignored += 1
                continue
            replies[finding_fingerprint(item.finding)].setdefault(draft.figure, draft)
        if ignored:
            errors.append(agent_error(
                agent_name=EVIDENCE_VERIFIER_NAME,
                error_type="evidence_verifier_unknown_reference",
                message="The Context Check named figures the batch did not list; they were ignored.",
                details={"ignored": ignored},
            ))
        return replies


def context_check_failed_error(batch_size: int, error: Exception) -> ResearchError:
    return agent_error(
        agent_name=EVIDENCE_VERIFIER_NAME,
        error_type="evidence_verifier_context_check_failed",
        message=("The Context Check failed for one batch; its findings keep their "
                 "Figure Match result and are cited only as unchecked context."),
        details={"findings": batch_size, "exception_type": type(error).__name__},
    )


def evidence_verified_event(findings: Sequence[Finding]) -> ResearchEvent:
    statuses = [f.verification.status for f in findings if f.verification is not None]
    return agent_event(
        agent_name=EVIDENCE_VERIFIER_NAME,
        event_type="evidence_verifier.verification.completed",
        message=f"Verified {len(findings)} findings.",
        metadata={
            "verified": statuses.count("verified"),
            "verified_corrected": statuses.count("verified_corrected"),
            "dropped": statuses.count("dropped"),
            "context_unchecked": sum(
                1 for f in findings if f.verification and f.verification.context_unchecked
            ),
        },
    )
```

Check `agent_event`'s real keyword names in `agents/events.py` line 18 and adapt the call to them. Add the remaining `BaseAgent` members exactly as `SynthesizerAgent` defines them (`__init__` passes through; `output_schema` returns `VerifiedFindings`; `system_prompt` returns `CONTEXT_CHECK_SYSTEM_PROMPT`; `build_task` returns `AgentTask(instruction=state.original_question)`; `finalize` returns `None`; `state_update` returns `{"errors": list(run.errors)}` plus `verified_findings` when a result is given).

- [ ] **Step 5: No re-export in this task.** Task 2.2 exports the new names (rule R3).

- [ ] **Step 6: Run the tests.** Run: `"$PY" -m pytest -q tests/test_agents/test_evidence_verifier.py tests/test_agents/test_evidence.py tests/test_agents/test_researcher.py tests/test_agents/test_tool_free_prompts.py`. Expected: PASS. If `test_tool_free_prompts.py` enumerates every tool-free structured request and fails because a new one exists, add the Context Check to its inventory the way the synthesizer's report request is registered there.

- [ ] **Step 7: Commit.**

```bash
git add src/deep_research/agents/evidence.py src/deep_research/agents/researcher.py src/deep_research/agents/evidence_verifier.py tests/test_agents/test_evidence_verifier.py tests/test_agents/test_evidence.py tests/test_agents/test_researcher.py tests/test_agents/test_tool_free_prompts.py
git commit -m "feat(evidence_verifier): batched Context Check with code enforcement (spec 5.2)"
```

**Acceptance:** every enforcement rule in §5.2 has a passing test; a failed batch never promotes a figure and never stops the run; a batch of 20 findings costs two calls.

### Task 2.2: Phase-2 integration: exports and pins

**Role:** sp-implementer. **Wave:** 2B, parallel with Task 2.3. **Depends on:** Task 2.1 merged.

**Owns:** `src/deep_research/agents/__init__.py`, `tests/test_evaluation/test_config.py`.

- [ ] **Step 1: Re-export** every new public name of `evidence_verifier.py` (`CONTEXT_CHECK_BATCH_SIZE`, `CONTEXT_CHECK_CONCURRENCY`, `CONTEXT_PASSAGE_CHARS`, `CONTEXT_CHECK_SYSTEM_PROMPT`, `CONTEXT_CHECK_INSTRUCTION`, `FigureCheckDraft`, `ContextCheckDraft`, `ContextItem`, `VerifiedFindings`, `EvidenceVerifierAgent`, `page_owner`, `evaluated_issuer`, `context_passage`, `resolve_attribution`, `unchecked_context`, `verify_finding`, `context_check_messages`, `context_check_failed_error`, `evidence_verified_event`) and of `evidence.py` (`neighbouring_passage_text`, `ATTRIBUTION_CUE_PATTERN`, `attribution_cue_adjacent`, `relay_attribution_on_page`, `own_organisation_on_page`) from `agents/__init__.py` and `__all__`.
- [ ] **Step 2: Re-pin the researcher fingerprint** exactly as Task 1.5 Step 2 does (only the researcher's value may move: Task 2.1 edited `researcher.py`); comment "Evidence Verifier plan, Task 2.1: attribution helpers moved to evidence.py".
- [ ] **Step 3: Run the tests.** Run: `"$PY" -m pytest -q tests/test_imports.py tests/test_evaluation/test_config.py tests/test_agents`. Expected: PASS.
- [ ] **Step 4: Commit** `src/deep_research/agents/__init__.py` and `tests/test_evaluation/test_config.py` with "chore(agents): export phase-2 names; re-pin the researcher fingerprint".

### Task 2.3: Step-2 proof on the audit-2 findings (Gate G2)

**Role:** sp-implementer, in `$W` (rule R1); the `--live` run is an operator step. **Wave:** 2B, parallel with Task 2.2. **Depends on:** Task 2.1 merged.

**Files:**
- Create (untracked): `scratch/ev_verify_audit2.py`

**Interfaces:**
- Consumes: `rebuild()` (Task 1.6); `figure_match`, `context_passage`, `verify_finding`, `ContextItem`, `FigureCheckDraft`, `EvidenceVerifierAgent` (Task 2.1); `_measure_scale` from `utils/types.py` (line 2374).
- Produces: `scripted_replies(item: ContextItem, overrides: Mapping[str, dict]) -> dict[int, FigureCheckDraft]` and `verify_scripted(rebuilt) -> list[Finding]`, imported by Task 3.6.

- [ ] **Step 1: Write the harness.** Behaviour:
  - `--scripted` (default, offline): for every figure-bearing rebuilt finding, build a `ContextItem` with the production `figure_match`, `context_passage` and `evaluated_issuer(rebuilt.sources, read)`, and a scripted reply per figure that confirms the recorded fields (period = recorded, scope = recorded, attribution `relayed` with the recorded `attributed_issuer` when there is one, else `own` with no organisation, kind = recorded, `evidence_words` = the snippet, verdict `confirm`). Three overrides exercise enforcement on real pages:
    1. every figure whose value is `18.9`: `scope` set to the first of `"utility, C&I, and residential"` or `"all segments"` that the finding's own passage contains (search `item.passage` with `excerpt_matches`), verdict `correct`, `evidence_words` = the snippet;
    2. the first finding from `enerknol.com`: `evidence_words` replaced by `"EnerKnol confirms 10.4 GW of grid-scale storage"` (not on the page);
    3. the first `forecast` figure from `eia.gov`: `period` set to `"2026"`, verdict `correct` (the page does not say 2026 for it).
    Run the production `verify_finding` over each item and attach the verification to its finding.
  - `--live` (operator, off-peak): build `state = ResearchState(session_id="ev-proof-audit2", original_question=QUESTION, raw_findings=rebuilt.findings, read_records=rebuilt.reads, evaluated_sources=rebuilt.sources)`; build the agent directly (it is not in the runtime assembly until Task 4.8): `tracker = Tracker(LangSmithRuntimeConfig(tracing_enabled=False, project="ev-proof", api_key=None))`, `settings = load_settings("config.yaml")`, `provider = build_chat_provider(settings.llm, tracker, request_budget=RequestBudget(settings.request_budget))`, `agent = EvidenceVerifierAgent(provider=provider, tracker=tracker, scratchpad=ScratchpadMemory(session_id=state.session_id, agent_name="evidence_verifier", max_entries=20), tools=[], config=settings.agents, model_profile=settings.llm.resolve_for("evidence_verifier"))` (imports: `deep_research.main.load_settings`, `deep_research.observability.{LangSmithRuntimeConfig, Tracker}`, `deep_research.providers.build_chat_provider`, `deep_research.request_budget.RequestBudget`, `deep_research.memory.scratchpad.ScratchpadMemory`); run `run = await agent.run(state)` inside `async with tracker.session_span(state.session_id, state.original_question):` and take `run.state_update["verified_findings"]`.
  - Print one line per 18.9 GW figure (status, kept, scope, attribution, organisation) and the counts by status and by drop reason. Then the checks:
    - **V1** every kept figure with value `18.9` has a scope whose `_measure_scale(scope)` contains `"all"`, or contains both `"residential"` and `"commercial"`; and at least one such figure is kept;
    - **V2** for every kept figure, `excerpt_matches(read_text(read), evidence_words)` is true (re-checked here, independent of the verifier) or `evidence_words` is `None` and the finding is `context_unchecked`;
    - **V3** (`--scripted` only) the enerknol figure is dropped with `evidence_not_on_page` and the eia.gov forecast figure with `correction_not_on_page`.
  - Exit 0 only when every applicable check prints `PASS`.

- [ ] **Step 2: Run offline.** Run: `"$PY" scratch/ev_verify_audit2.py --scripted`. Expected: `PASS V1`, `PASS V2`, `PASS V3`, exit 0.

- [ ] **Step 3: Run live (operator, off-peak).** Run the OFF-PEAK CHECK; if it prints `OK`, run `"$PY" scratch/ev_verify_audit2.py --live`. Expected: `PASS V1`, `PASS V2`, at most two `ContextCheckDraft` calls plus at most two half-batch re-asks, under 5 minutes. The harness wraps `provider.complete_structured` in a timing wrapper (harness-only) and prints each call's seconds, then every `evidence_verifier_context_check_failed` error the run recorded. If V1 fails (the model kept grid-scale), stop: the Context Check prompt or enforcement needs work before Phase 3. Report the printed 18.9 GW lines, the call seconds and any failed batch to the controller.

- [ ] **Step 4: No commit** (scratch).

### Gate G2 — end of step 2

Run in `$W` once Tasks 2.1–2.3 are merged and review-clean (rule R8):

```bash
"$PY" -m pytest -q tests/test_agents/test_evidence_verifier.py tests/test_agents/test_evidence.py tests/test_agents/test_figures.py tests/test_agents/test_researcher.py tests/test_types.py tests/test_imports.py
"$PY" scratch/ev_verify_audit2.py --scripted
"$PY" scratch/ev_verify_audit2.py --live      # operator, off-peak only
```

**Pass condition:** the tests pass (they include a unit test for every normalisation case of §5.1 and every enforcement rule of §5.2, including invented `evidence_words` rejected and an unsupported correction dropped); the scripted run prints PASS for V1–V3; the live run prints PASS for V1 and V2 (audit-2's 18.9 GW labelled all-segment by the real Context Check). Record the live run's per-call seconds and failed batches: a failed batch or a call over about 60 s makes Task 4.1 add the `evidence_verifier` timeout line (review item 13, F8). Do not start Phase 3's wave 3B until G2 passes.

---

## Phases 3–6 at a glance

> The full, step-by-step bodies of Tasks 3.1–6.4 follow this overview (after the horizontal rule). This overview only summarises them; dispatch from the bodies, never from these bullets.

### Phase 3 (spec step 3): the Report Writer on verified findings

- **3.1 Verified facts** (`agents/verified_facts.py`, signatures in "Shared interfaces"). Citable means status is not `dropped`. `same_organisation`: identical identity words once a leading "U.S."/country word is dropped; or one side is a single token (a host label on a government, education, `.com` or `.org` host, or an acronym) equal to the other side's initials (country word and a trailing legal-form word dropped; an all-capitals word contributes itself whole), to its words run together, or (four letters or more) to their prefix. So EIA, "U.S. Energy Information Administration" and eia.gov are one organisation, Wood Mackenzie and woodmac.com are one, BloombergNEF and bnef.com are one, and eia.news is never EIA. `same_period`: equal after `cosmetic_text`, or both reduce to one bare year. `finding_answers`: §6.6 plus PD-7. `fact_rows`: §5.3 and PD-9 (same organisation, unit dimension, period, kind and value is one row; the own page cites ahead of a relay; a revision folds two rows answering the same target with differing release keys, the latest kept and the earlier listed). `not_found_targets`: required targets with no answer, with the owning sub-topic's queries and the acquisition state's attempted and read URLs (keyed by `coverage_id`). `untraced_numbers`: every quantity and bare number in a sentence must equal a kept figure of a cited finding. Tests cover each organisation pair above, the 10.4 GW / 10.3 GW revision, own-plus-relay folding into one row, forecast and actual never merging, two organisations staying two rows, and "12 GW" untraced while "10,400 MW" traces to 10.4 GW.
- **3.2 Layout** (`agents/report.py`): `figure_label` per §6.1; `render_written_report` with the header (question, as-of, scope, counts), executive summary, key facts table (Organisation, Measure, Period, Value, Kind, Scope, Release or edition, Source), findings sections, Not found (question, queries, pages read), and sources (only those cited, in first-use order), each statement ending with its figures' labels; `render_finding_log` with every finding's snippet, read, locator and status, every figure kept or dropped with its reason and evidence words, and every refused sentence in full. A test asserts that no verdict or corroboration word appears.
- **3.3 Wording rules** (`agents/wording.py`, PD-19; runs in wave 1B, so `evidence_verifier.py` imports `stated_role` from it from the start): move the helpers named in "Shared interfaces" out of `synthesizer.py` unchanged (`synthesizer.py` imports them back until Task 4.10 deletes it). Add `unattested_names`, `stated_years`, `SCOPE_TERMS` (grid-scale, utility-scale, front-of-the-meter, behind-the-meter, residential, commercial, industrial, C&I, commercial and industrial, distributed, community, all segments, all sectors) with `stated_scopes`, `page_modal`, and `hedge_forecast` (F3: `will`/`would` become the page's own modal, "could", "might" or "may", passed as `marker`, else "is expected to"; a past-tense outcome verb becomes "is/are expected to be …"; ", according to <organisation>'s forecast" is appended when no forecast marker remains). `_realized_outcome` stops counting a verb after "to be". Pinned by `test_hedge_forecast_makes_a_forecast_read_as_one` and `test_hedge_forecast_takes_the_pages_own_modal`.
- **3.4 Report Writer** (`agents/report_writer.py`): the draft schemas; `finding_registry` (labels F01… over citable findings, answers to required targets first); `writer_messages`, whose registry lines have the fixed form `F01 | figure 1: 10.4 GW | period 2024 | kind actual | organisation <name> | label: <figure_label>` (Task 4.9's replay double parses it). The writing rules: cite labels only; use only numbers from the cited figures; state forecasts with organisation and release; name both parties of a relay; use the scope words the finding states, never the question's; summary order is the 2024 actual, then each organisation's latest 2025 forecast, then 2025 actuals; no verdict words. `check_point` checks numbers (`untraced_numbers`, dates skipped, F2); names (`unattested_names` over the cited snippets, evidence words, verified organisations, periods, scopes, releases, titles, the Source Evaluator's validated issuers (PD-25) and plan geographies); dates (each must appear in the cited findings); years; scope (a scope term must appear in the verified scope or the evidence words of a figure the sentence states); kind, clause by clause (`stated_role` against the figure's verified kind); and `hardened_modality`. In `compose_written_report`, a refused sentence becomes a `RejectedDraftPoint` with its full text, labels and reason. A sentence refused only for stating a forecast as fact or for hardening its modality is rewritten once with `hedge_forecast` and checked again (F3). A summary point that restates only facts already stated is dropped as a duplicate. The agent also builds the statements, fact rows, Not found list and labels. `ReportWriterAgent` has the two-attempt ladder (the profile's effort, then high after a truncation; F10), `WrittenReport`, `publish_document`, `publish_finding` and `finding_memory_payload`. Pinned by `test_forecast_stated_as_fact_is_rewritten_once_and_kept`, `test_a_hardened_forecast_takes_the_pages_own_modal`, `test_grid_scale_wording_on_an_all_segment_figure_is_refused` and `test_a_failed_draft_still_composes_the_key_facts`.
- **3.5 Integration:** exports; confirms no fingerprint pin moved (the synthesizer pin moved in Task 1.5, when Task 3.3 ran).
- **3.6 Harness** `scratch/ev_compose_audit2.py`: `rebuild()` → `verify_scripted()` → a scripted draft built from real audit-2 findings (EIA's 10.4 GW 2024 actual; the January 2025 STEO's 14 GW forecast relayed by ent.news; Wood Mackenzie's Q1 2025 forecast of 15 GW / 49 GWh; "EIA's outlook adds 14 GW in 2025"; one "18.9 GW of grid-scale storage" sentence; one duplicate restatement) → compose → render. `--live` asks the real writer (off-peak). Checks: C1, the summary states 10.4 GW labelled actual with an EIA organisation and a release. C2, at least two summary lines state 2025 forecasts from two organisations, each labelled "forecast (<release>)". C3, the "adds 14 GW" sentence is rewritten and kept. C4, the grid-scale sentence is refused with a scope reason. C5, no duplicate figure line (in the summary or the key facts). C6, no verdict wording. C7, nothing untraced. `--live` must pass C1, C2, C5, C6 and C7.
- **Gate G3:** the four new test files, the full suite (the old pipeline is still green, PD-3), and the harness: `--scripted` passes C1–C7, `--live` passes C1, C2, C5, C6 and C7. Spec §9 step 3's proof: the summary carries the 2024 actual and at least two 2025 forecasts with organisation and release; there is no duplicate figure line and no verdict wording.

### Phase 4 (spec step 4): the cutover (tasks 4.1–4.14 as in the wave table)

- **4.1** Adds and renames the step-4 types. `utils/config.py`: `PRODUCTION_AGENT_NAMES` = planner, researcher, source_evaluator, evidence_verifier, report_writer; `SERVICE_ROLE_NAMES = ("report_reviewer",)`; `GraphConfig.max_extra_passes = 1` (env `GRAPH_MAX_EXTRA_PASSES`); `claim_batch_size`, `claim_batches_per_pass`, `critic_review_max_tokens`, `claim_verification_max_tokens` and their env keys (and their tests in `tests/test_config.py`) are removed. `config.yaml`: overrides planner `max`, researcher `high`, source_evaluator `high`, evidence_verifier `high` (§5.2), report_writer `high` (the writer's one effort source, F10), report_reviewer `max` with timeout 360 and retry_count 1; `tool_budget_overrides` for planner 1, researcher 20, source_evaluator 0, evidence_verifier 0, report_writer 0; `graph.max_extra_passes: 1`; the four §7.3 caps `agents.sub_topic_concurrency: 5`, `agents.source_scoring_concurrency: 3`, `agents.verifier_batch_size: 5` and `agents.verifier_concurrency: 8`, each with its `AGENTS_*` environment key and its `test_config.py` assertion. Also: `git mv` of `report_review.py` to `report_reviewer.py` with every import line updated, `REPORT_JUDGE_ROLE` renamed to `REPORT_REVIEWER_ROLE = "report_reviewer"`, and the three filename helpers moved into `report_writer.py`. Acceptance: IMPORT SMOKE for agents, graph, runtime, api, cli and main.
- **4.2 Report Reviewer.** One call. The packet holds the statements with their code-built labels and their cited findings' snippets and labels, the key facts, Not found, and the gate results. Dispositions are supported, unsupported or not_reviewed; an unsupported statement becomes a material derived defect, and the reviewer may record a defect for prose that contradicts its label (the Statement Check is the primary guard, §6.3). A truncated reply is asked once more at high effort; a provider failure gives `provider_failed`; missing dispositions give `incomplete`. No critic imports. The node, not the model, stamps `missing_required_target_ids` (PD-5).
- **4.3 Quality.** `compute_report_quality(state, composition)` with the PD-10 gates and the new snapshot fields, including `unjudged_sentences` in place of `untraced_figures` (D8: a kept sentence with no Statement Check verdict and no recorded batch failure); `compose_written_report` fills `ReportComposition.statement_verdicts`. Missing targets are the required targets no finding answers (`verified_facts`); unaccounted targets are the missing ones absent from Not found.
- **4.4 Researcher and planner.** The first pass runs every planned sub-topic in priority order. An extra pass runs only the sub-topics that own `state.extra_pass_target_ids`, and shows only those targets. The critic-driven selection helpers are deleted. The planner drops its `claim_clusters` import, the three dimension-validation blocks, `support_policy` (draft field, prompt paragraph and examples) and the `extend_plan` path.
- **4.5 Quality record.** `render_quality_json` carries the new snapshot, the review, the findings with their verification, the fact rows, Not found, the statements, and the refused sentences in full. The quality contract version is bumped.
- **4.6 Outcome, API, CLI, README.** Coverage becomes required, answered, missing and not found. Evidence counts cover reads, sources, and findings verified, corrected, dropped, unchecked and cited. The summary lines are rewritten: no critic score and no claim lines; the review mean is shown; the integrity line counts duplicate fact rows, uncited statements, unjudged sentences and forecasts without release. `--max-iterations` accepts 0 and its help says "extra research passes for missing required targets". Progress events cover the new nodes; the README sections are rewritten, the graph-historical subsection is deleted, and a short "Upgrading" note lists the renamed env key, the removed config keys and the unresumable old checkpoints (F15).
- **4.7 Evaluation.** `AGENT_NAMES` becomes planner, researcher, source_evaluator, evidence_verifier, report_writer. The fact_checker and critic cases, gates, scenarios, conftest outputs and tests are deleted. `cases/report_writer.py` is the `git mv` of the synthesizer cases, reworked to verified findings. A new `cases/evidence_verifier.py` holds the case count that `cases/__init__.py` enforces (scope correction, relay, invented evidence words; the live case uses the benchmark's EIA and Wood Mackenzie pages), with the gates `verification_recorded`, `no_invented_evidence` and `drop_reasons_named`.
- **4.8 Graph and runtime,** with the contract in "Shared interfaces". `graph_route` (PD-23): halted → end. A missing required target with `iteration < max_extra_passes` → extra_pass (`extra_pass_requested`). An unscored review → finalize (`review_unavailable`, `incomplete`). Clear gates and a passing review → finalize (`report_accepted`, `completed`), with any still-missing target under Not found. Otherwise, a missing target with no pass left → finalize (`extra_passes_exhausted`, status `max_iterations`), else finalize (`report_not_accepted`, `incomplete`). Quality is `accepted` only on `report_accepted`. The writer node runs `compute_report_quality`; the reviewer node stamps `missing_required_target_ids`; the extra-pass node sets `extra_pass_target_ids` and advances the iteration. Finalize renders `render_written_report`, `render_finding_log` and `render_quality_json`, and saves cited findings to memory only when the report is accepted. The critic, fact-checker, refine and repair machinery is deleted. Pinned by `test_run_publishes_when_the_context_check_fails` and `test_extra_pass_that_finds_nothing_publishes_with_not_found`.
- **4.9 E2E doubles and cases.** The `ClaimsDraft`, `ClaimVerdictDraft`, `ClaimEquivalenceDraft`, `CritiqueDraft` and `ReportDraft` doubles are deleted. New doubles: `ContextCheckDraft` (confirms by default, with per-source overrides, one reply per batch of 5), `StatementCheckDraft` (consistent by default, per-scenario corrected/inconsistent overrides), `ReportWriterDraft` (one point per registry figure, from the fixed line format) and the reviewer draft. `REPLAY_CASE_MANIFEST` is reworked. same-work-mirror, primary-attribution, current-versus-forecast and reopen-unanswered-target become Evidence Verifier cases; semantic-duplicate-claims and late-contradiction are retired with stated reasons. New rows: relay-labelled-as-relay, figure-not-on-page-dropped, evidence-words-not-on-page-rejected, scope-corrected-to-all-segments, revision-noted, forecast-versus-actual-kept-apart and extra-pass-finds-nothing. The graph-only rows go with the graph-historical harness (PD-14, Task 4.11).
- **4.10 Deletion sweep.** Deletes the four modules, `utils/claims.py`, their tests and every dead name (including the D8 helpers `hedge_forecast`, `page_modal`, `unattested_names`/`stated_scopes` if unread, `bare_numbers`, `untraced_numbers`, and the `figure_not_in_evidence` drop reason), and cleans up the types. Updates the exports and `test_imports.py`, re-pins the fingerprints (PD-17), and runs the acceptance grep outside e2e.
- **4.11 E2E matrix.** Reworks the models, evaluators and runner, and retires the graph-historical harness (PD-14). The doubles script both check drafts; the real-agent matrix is green at three repetitions, and the acceptance grep passes over e2e.
- **4.12 Reviewer probe** `scratch/ev_review_audit2.py`: the real writer and the real Report Reviewer on the audit-2 state (off-peak, about 10 minutes); the review must be scored and accepting (F7).
- **4.13 Parallel sub-topics and scoring batches (D9).** The researcher's sub-topics run concurrently (at most `agents.sub_topic_concurrency`, 5) with one run-wide tool lock and per-loop scratchpads and acquisition contexts; findings and events fold in plan order; source-evaluator batches run at most `agents.source_scoring_concurrency` (3) at once; the Evidence Verifier's two bounds come from config; `sub_topic.completed` carries `elapsed_s`.
- **4.14 Concurrency and budget telemetry (§7.3).** One collector per run records the 429 count and how many a retry recovered, the peak provider calls in flight, per-stage calls/seconds/slowest, and per-operation max output tokens against the configured cap plus the truncation count. It lands in the quality JSON and in one CLI summary line with advisory messages that name the config knob; nothing auto-tunes.
- **Gate G4:** both full-suite invocations, the real-agent e2e matrix at three repetitions, IMPORT SMOKE, the acceptance grep over everything, the reviewer probe and the Telemetry line, all green (spec §9 step 4).

### Phase 5 (spec step 5): the planner's floor

- **5.1** Final `EvidenceTarget` fields: `required_dimensions` and `critical` are removed and `measure` is required; `make_target` loses its legacy branch.
- **5.2** `PLAN_INSTRUCTION` follows §7.1. One target per organisation, measure, period and kind the question asks for. `required` only for what the question names. Self-added targets (MWh for a capacity question, facility types, definitions) and paywalled-only issuers are optional. About five targets for the benchmark, with the temporal contract kept. `apply_answer_contract` drops the answer-form and evidence-period boilerplate, and a target with no measure is a plan problem the existing repair loop sees.
- **5.3** Updates the consumers: the researcher's unit mentions come from `unit_dimension`; the reviewer's target view; the evaluation planner cases and gates (`targets_have_measure` replaces the dimension gates); the e2e planner double and matrix topics.
- **5.4** Re-pins the fingerprints.
- **5.5** `scratch/ev_plan_probe.py --live` (off-peak, about 3 minutes) runs the real planner on the benchmark question.
- **Gate G5:** the probe's checks pass. At most five required targets; no required energy (MWh) target; no required facility-type or definition target; the required targets include EIA's 2024 actual and at least two organisations' 2025 forecasts.

### Phase 6 (spec step 6): the live proof

- **6.1** `scratch/run_live_proof.py` gains two labels, with per-label arguments and environment recorded in `run.env`. `ev-preflight`: the benchmark question, `--max-iterations 0`, `AGENTS_MAX_SUB_TOPICS=2` and `--request-tavily-attempt-ceiling 12`. `ev-1`: the benchmark question on the default config. The stage-time summary prints the slowest sub-topic and the researcher's per-turn mean from each `sub_topic.completed` event's `elapsed_s` (Task 4.13), plus the Telemetry line (Task 4.14).
- **6.2** The operator runs `ev-preflight` off-peak. Pass: exit 0 (quality accepted; exit 4 fails); report, evidence log and quality JSON written; the projected first pass within 30 minutes; the researcher's per-turn mean at most 27 s; no failed Context Check batch and no unchecked context; no forecast without release (F7, F8, PD-24); zero unrecovered 429s and no truncated call on the Telemetry line. A violated criterion is fixed by lowering the config knob its advice names (`agents.sub_topic_concurrency` to 3), no code change.
- **6.3** The operator runs `ev-1` off-peak, never hard-stopped.
- **6.4** A fresh read-only reviewer audits `output/live-proof/ev-1/` against spec §10: wall time at most 45 minutes; the summary answers both halves with releases; every number is traced; relays are labelled; no wrong scope, period or kind; the reviewer accepted with no gate failure; rated GREAT.

---

## Phase 3 — Report Writer on verified findings (spec step 3)

Phase goal: a report is composed from verified findings only, cited by label, with the reader labels of §6.1, a code-built key facts table (duplicates folded, revisions noted), a Not found list, and the §6.2 guards. The writer agent exists and is unit-tested; the graph does not run it yet (PD-3). Task 3.3 already ran in wave 1B, and Tasks 3.1 and 3.2 in wave 2A beside Task 2.1 (the approved R8 exception); Task 3.4 needs Gate G2 and all three; 3.5 and 3.6 run in parallel after 3.4.

### Task 3.1: Verified facts: answering, duplicates, revisions, Not found, number tracing

**Role:** sp-hard-implementer. **Wave:** 2A, parallel with Tasks 2.1 and 3.2 (the approved R8 exception: it needs only Task 1.1's types and Task 1.2's `figures.py`). **Depends on:** Gate G1. The controller merges it into the plan branch only after Gate G2 passes (rule R8); its proof is Gate G3.

**Owns:** `src/deep_research/agents/verified_facts.py` (new), `tests/test_agents/test_verified_facts.py` (new).

**Interfaces:**
- Consumes: the Task 1.1 types; `cosmetic_text` (`agents/evidence.py`); `parse_figure`, `quantities_in`, `same_quantity`, `bare_numbers`, `Quantity` (`agents/figures.py`); `finding_fingerprint` (`agents/identity.py`); `publisher_identity` (`agents/sources.py`).
- Produces: exactly the `verified_facts.py` block of "Shared interfaces".

- [ ] **Step 1: Write the failing tests** (`tests/test_agents/test_verified_facts.py`):

```python
"""Spec §5.3, §6.4 and §6.6: facts are read from verified fields, never from prose."""

from __future__ import annotations

import pytest

from deep_research.agents.identity import finding_fingerprint
from deep_research.agents.verified_facts import (
    answered_target_ids,
    fact_rows,
    finding_answers,
    not_found_targets,
    release_key,
    same_organisation,
    same_period,
    untraced_numbers,
)
from deep_research.utils.types import (
    AcquisitionState,
    FigureContext,
    FigureResult,
    FindingVerification,
    SubTopic,
)
from tests.evidence_fakes import figure, make_finding, make_read, make_target

EIA = "U.S. Energy Information Administration"


def ctx(organisation=EIA, attribution="own", period="2024", kind="actual", scope=None):
    return FigureContext(period=period, scope=scope, attribution=attribution,
                         organisation=organisation, kind=kind)


def verified(finding, *contexts, unchecked=False, dropped=False):
    if dropped:
        verification = FindingVerification(status="dropped", dropped_reason="snippet_not_on_page")
    else:
        results = [FigureResult(figure=f, matched=True, context=c)
                   for f, c in zip(finding.figures, contexts)]
        verification = FindingVerification(status="verified", figure_results=results,
                                           context_unchecked=unchecked)
    return finding.model_copy(update={"verification": verification})


def eia_2024(value="10.4", **fields):
    read = make_read()
    finding = make_finding(read, "Generators added 10.4 gigawatts (GW) of new battery storage capacity in 2024,",
                           figures=[figure(value, "GW", "2024", "actual")],
                           target_ids=["topic-01-target-01"], **fields)
    return verified(finding, ctx())


@pytest.mark.parametrize(("left", "right", "same"), [
    (EIA, "EIA", True),
    ("EIA", "eia.gov", True),
    (EIA, "eia.gov", True),
    ("Wood Mackenzie", "woodmac.com", True),
    ("BloombergNEF", "bnef.com", True),
    ("American Clean Power Association", "ACP", True),
    ("Energy Information Administration", EIA, True),
    ("EIA", "eia.news", False),
    ("EIA", "IEA", False),
    ("Wood Mackenzie", "BloombergNEF", False),
])
def test_same_organisation(left, right, same) -> None:
    assert same_organisation(left, right) is same
    assert same_organisation(right, left) is same


def test_same_period() -> None:
    assert same_period("2025", "in 2025") and same_period("calendar year 2024", "2024")
    assert not same_period("2025", "Q3 2025") and not same_period(None, "2025")


def test_a_verified_figure_answers_its_matching_target() -> None:
    finding = eia_2024()
    assert finding_answers(finding, make_target(organisation="EIA"))
    assert not finding_answers(finding, make_target(organisation="Wood Mackenzie"))
    assert not finding_answers(finding, make_target(kind="forecast"))
    assert not finding_answers(finding, make_target(period="2025"))
    assert not finding_answers(finding, make_target("topic-02-target-01", period="2024"))
    assert not finding_answers(finding.model_copy(update={"verification": None}), make_target())


def test_a_qualitative_target_is_answered_by_naming_it() -> None:
    read = make_read()
    finding = make_finding(read, "Generators added 10.4 gigawatts", target_ids=["topic-01-target-01"])
    finding = finding.model_copy(update={"verification": FindingVerification(status="verified")})
    assert finding_answers(finding, make_target(unit_dimension=None, organisation="eia.gov"))


def test_an_own_page_and_its_relay_are_one_row_citing_the_own_page() -> None:
    own = eia_2024()
    relay_read = make_read("According to EIA, generators added 10.4 GW in 2024.",
                           url="https://www.utilitydive.com/news/x", title="x")
    relay = verified(make_finding(relay_read, "According to EIA, generators added 10.4 GW in 2024.",
                                  figures=[figure("10.4", "GW", "2024", "actual")],
                                  target_ids=["topic-01-target-01"]),
                     ctx(organisation="EIA", attribution="relayed"))
    [row] = fact_rows([relay, own], [make_target()])
    assert row.finding_id == finding_fingerprint(own) and row.attribution == "own"
    assert row.duplicate_finding_ids == [finding_fingerprint(relay)]
    assert row.row_id == "K001" and row.target_ids == ["topic-01-target-01"]


def test_a_later_release_is_a_revision_with_the_earlier_edition_noted() -> None:
    latest = eia_2024(release_date="2025-03-12")
    earlier = eia_2024(value="10.3", release_date="2025-02-10").model_copy(update={"snippet": "power providers added a record 10.3 GW"})
    [row] = fact_rows([earlier, latest], [make_target()])
    assert row.value == "10.4 GW" and row.release == "released 2025-03-12"
    assert [(e.value, e.release) for e in row.earlier] == [("10.3 GW", "released 2025-02-10")]


def test_forecast_and_actual_never_merge_and_two_organisations_stay_two_rows() -> None:
    actual = eia_2024()
    forecast = verified(actual.model_copy(update={"figures": [figure("10.4", "GW", "2024", "forecast")], "verification": None}),
                        ctx(kind="forecast"))
    other = verified(actual.model_copy(update={"verification": None}), ctx(organisation="Wood Mackenzie"))
    rows = fact_rows([actual, forecast, other], [make_target()])
    assert len(rows) == 3 and all(not row.earlier for row in rows)


def test_dropped_findings_answer_nothing_and_make_no_row() -> None:
    dropped = verified(eia_2024().model_copy(update={"verification": None}), dropped=True)
    assert answered_target_ids([dropped], [make_target()]) == {}
    assert fact_rows([dropped], [make_target()]) == []


def test_release_key() -> None:
    assert release_key(eia_2024(release_date="2025-03-12")) == (2025, 3, 12)
    assert release_key(eia_2024(vintage="January 2025 STEO")) == (2025, 1, 0)
    assert release_key(eia_2024()) is None


def test_not_found_lists_required_unanswered_targets_with_their_trail() -> None:
    required, optional = make_target("topic-02-target-01", kind="forecast", period="2025"), make_target("topic-02-target-02", required=False)
    topic = SubTopic(coverage_id="topic-02", title="EIA forecast", rationale="r", search_queries=["EIA STEO 2025 battery"],
                     success_criteria=["c"], priority=1, evidence_targets=[required, optional])
    acquisition = {"topic-02": AcquisitionState(read_urls=["https://www.eia.gov/outlooks/steo/"], consecutive_searches=1)}
    [row] = not_found_targets([topic], {}, acquisition)
    assert (row.target_id, row.queries, row.searched) == ("topic-02-target-01", ["EIA STEO 2025 battery"], True)
    assert row.pages_read == ["https://www.eia.gov/outlooks/steo"] or row.pages_read[0].startswith("https://www.eia.gov/outlooks/steo")


def test_untraced_numbers() -> None:
    cited = [eia_2024()]
    assert untraced_numbers("EIA reports 10,400 MW added in 2024.", cited) == []
    assert untraced_numbers("EIA reports 12 GW added in 2024.", cited) == ["12 GW"]
    assert untraced_numbers("EIA reports 10.4 GW across 37 states.", cited) == ["37"]
```

- [ ] **Step 2: Run to see them fail.** Run: `"$PY" -m pytest -q tests/test_agents/test_verified_facts.py`. Expected: FAIL (`ModuleNotFoundError: deep_research.agents.verified_facts`).

- [ ] **Step 3: Implement** `src/deep_research/agents/verified_facts.py`:

```python
"""Facts from verified findings (spec §5.3, §6.1, §6.4, §6.6).

Deterministic and field-driven: target answering, duplicates and revisions,
the Not found list and number tracing read verified fields and structured
figures only. Nothing here parses a finding's ``content``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from deep_research.agents.evidence import cosmetic_text
from deep_research.agents.figures import (
    Quantity,
    bare_numbers,
    parse_figure,
    quantities_in,
    same_quantity,
)
from deep_research.agents.identity import finding_fingerprint
from deep_research.agents.sources import publisher_identity
from deep_research.utils.types import (
    AcquisitionState,
    EarlierEdition,
    EvidenceTarget,
    FactRow,
    Finding,
    FigureContext,
    FindingFigure,
    NotFoundTarget,
    SubTopic,
)

_WORD = re.compile(r"[A-Z]{2,}(?![a-z])|[A-Z]?[a-z]+|[A-Z]|\d+")
_HOST = re.compile(r"(?:[a-z0-9-]+\.)+[a-z]{2,}")
_COUNTRY_WORDS = frozenset({"us", "usa", "uk"})
_CONNECTORS = frozenset({"of", "and", "the", "for", "on", "in"})
_LEGAL_SUFFIXES = frozenset(
    {"inc", "llc", "ltd", "corp", "corporation", "co", "association", "institute", "council", "agency"}
)
# A host label may stand for an organisation's name only on a suffix whose
# label is the organisation's own choice or an institution's (PD-18): never on
# a suffix anyone buys to look like someone else ("eia.news").
_NAMEABLE_SUFFIXES = frozenset({"gov", "edu", "int", "mil", "com", "org"})
_YEAR = re.compile(r"(?:19|20)\d{2}")
_PERIOD_FILLER = frozenset({"in", "during", "calendar", "year", "full", "the", "of", "cy"})
_MONTHS = {
    name: number
    for number, names in enumerate(
        (("january", "jan"), ("february", "feb"), ("march", "mar"), ("april", "apr"),
         ("may",), ("june", "jun"), ("july", "jul"), ("august", "aug"),
         ("september", "sep", "sept"), ("october", "oct"), ("november", "nov"),
         ("december", "dec")),
        start=1,
    )
    for name in names
}
_MONTH_YEAR = re.compile(
    r"\b(" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\.?\s+((?:19|20)\d{2})\b",
    re.IGNORECASE,
)
_ISO_DATE = re.compile(r"\b((?:19|20)\d{2})(?:-(\d{1,2})(?:-(\d{1,2}))?)?\b")
_MEASURE_BY_DIMENSION = {"power": "power capacity", "energy": "energy capacity", "percent": "share"}
_ATTRIBUTION_RANK = {"own": 0, "relayed": 1, "unattributed": 2}


@dataclass(frozen=True)
class VerifiedFigure:
    """One kept figure of a citable finding, with its verified context."""

    finding: Finding
    finding_id: str
    index: int
    figure: FindingFigure
    context: FigureContext
    quantity: Quantity | None
    unchecked: bool


def citable_findings(findings: Sequence[Finding]) -> list[Finding]:
    """§4: only verified and verified_corrected findings can be cited."""
    return [f for f in findings if f.verification is not None and f.verification.status != "dropped"]


def verified_figures(findings: Sequence[Finding]) -> list[VerifiedFigure]:
    """Every kept figure of every citable finding, in finding order."""
    figures: list[VerifiedFigure] = []
    for finding in citable_findings(findings):
        verification = finding.verification
        assert verification is not None
        finding_id = finding_fingerprint(finding)
        for index, result in enumerate(verification.figure_results):
            if result.kept and result.context is not None:
                figures.append(
                    VerifiedFigure(
                        finding=finding, finding_id=finding_id, index=index,
                        figure=result.figure, context=result.context,
                        quantity=parse_figure(result.figure.value, result.figure.unit),
                        unchecked=verification.context_unchecked,
                    )
                )
    return figures


def _tokens(value: str) -> list[str]:
    """A name's words, case kept, camel case split, "U.S." read as one word."""
    text = value.replace("U.S.", "US").replace("U.K.", "UK").replace("&", " and ")
    return _WORD.findall(text)


def _core(tokens: Sequence[str]) -> list[str]:
    return [t.casefold() for t in tokens if t.casefold() not in _COUNTRY_WORDS | _CONNECTORS]


def _initials(tokens: Sequence[str]) -> str:
    kept = [t for t in tokens if t.casefold() not in _COUNTRY_WORDS | _CONNECTORS]
    if len(kept) > 1 and kept[-1].casefold() in _LEGAL_SUFFIXES:
        kept = kept[:-1]
    return "".join(t.casefold() if t.isupper() and len(t) > 1 else t[0].casefold() for t in kept)


def _single_token(value: str) -> str | None:
    """The one token a host label or a one-word name stands for, else ``None``."""
    text = value.strip().casefold()
    if _HOST.fullmatch(text):
        label, _, suffix = publisher_identity(f"https://{text}").partition(".")
        return label if suffix.rsplit(".", 1)[-1] in _NAMEABLE_SUFFIXES else None
    tokens = _tokens(value)
    return tokens[0].casefold() if len(tokens) == 1 else None


def same_organisation(left: str, right: str) -> bool:
    """Whether two organisation names, acronyms or hosts name one organisation."""
    if not left.strip() or not right.strip():
        return False
    if _HOST.fullmatch(left.strip().casefold()) and _HOST.fullmatch(right.strip().casefold()):
        return publisher_identity(f"https://{left.strip()}") == publisher_identity(f"https://{right.strip()}")
    if not _HOST.fullmatch(left.strip().casefold()) and not _HOST.fullmatch(right.strip().casefold()):
        if _core(_tokens(left)) == _core(_tokens(right)):
            return True
    for one, other in ((left, right), (right, left)):
        token = _single_token(one)
        if token is None:
            continue
        if _single_token(other) == token:
            return True
        if _HOST.fullmatch(other.strip().casefold()):
            continue
        tokens = _tokens(other)
        joined = "".join(_core(tokens))
        if token in {_initials(tokens), joined} or (len(token) >= 4 and joined.startswith(token)):
            return True
    return False


def _period_key(value: str | None) -> str | None:
    if not value or not value.strip():
        return None
    words = [w for w in re.findall(r"[a-z0-9]+", cosmetic_text(value)) if w not in _PERIOD_FILLER]
    return " ".join(words) or None


def same_period(left: str | None, right: str | None) -> bool:
    """Equal after cosmetic normalisation, or both the same bare year."""
    key = _period_key(left)
    return key is not None and key == _period_key(right)


def _figure_answers(figure: VerifiedFigure, target: EvidenceTarget) -> bool:
    return (
        figure.quantity is not None
        and figure.quantity.dimension == target.unit_dimension
        and (target.period is None or same_period(figure.context.period, target.period))
        and (target.kind is None or figure.context.kind == target.kind)
        and (target.organisation is None or same_organisation(target.organisation, figure.context.organisation))
    )


def _finding_organisations(finding: Finding) -> list[str]:
    names = [figure.context.organisation for figure in verified_figures([finding])]
    if finding.attributed_issuer:
        names.append(finding.attributed_issuer)
    names.append(publisher_identity(finding.source_url))
    return names


def finding_answers(finding: Finding, target: EvidenceTarget) -> bool:
    """§6.6, plus PD-7 for a target with no unit dimension."""
    if finding.verification is None or finding.verification.status == "dropped":
        return False
    if target.target_id not in finding.target_ids:
        return False
    if target.unit_dimension is None:
        return target.organisation is None or any(
            same_organisation(target.organisation, name) for name in _finding_organisations(finding)
        )
    return any(_figure_answers(figure, target) for figure in verified_figures([finding]))


def answered_target_ids(
    findings: Sequence[Finding], targets: Sequence[EvidenceTarget]
) -> dict[str, list[str]]:
    """Target id -> the ids of the findings that answer it (answered targets only)."""
    answered: dict[str, list[str]] = {}
    for target in targets:
        ids = [finding_fingerprint(f) for f in findings if finding_answers(f, target)]
        if ids:
            answered[target.target_id] = ids
    return answered


def release_text(finding: Finding) -> str | None:
    """The finding's edition as the reader sees it: vintage, then release or statement date."""
    parts: list[str] = []
    if finding.vintage:
        parts.append(finding.vintage)
    if finding.release_date:
        parts.append(f"released {finding.release_date}")
    elif finding.statement_date:
        parts.append(f"stated {finding.statement_date}")
    return "; ".join(parts) or None


def _date_key(text: str | None) -> tuple[int, int, int] | None:
    if not text:
        return None
    month_year = _MONTH_YEAR.search(text)
    if month_year:
        return (int(month_year.group(2)), _MONTHS[month_year.group(1).casefold()], 0)
    iso = _ISO_DATE.search(text)
    if iso:
        return (int(iso.group(1)), int(iso.group(2) or 0), int(iso.group(3) or 0))
    return None


def release_key(finding: Finding) -> tuple[int, int, int] | None:
    """A sortable release: release date, else statement date, else vintage."""
    for value in (finding.release_date, finding.statement_date, finding.vintage):
        key = _date_key(value)
        if key is not None:
            return key
    return None


def _value_text(figure: FindingFigure) -> str:
    return f"{figure.value} {figure.unit}"


def _same_fact(left: VerifiedFigure, right: VerifiedFigure) -> bool:
    if left.context.kind != right.context.kind:
        return False
    if not same_period(left.context.period, right.context.period):
        return False
    if not same_organisation(left.context.organisation, right.context.organisation):
        return False
    if left.quantity is not None and right.quantity is not None:
        return same_quantity(left.quantity, right.quantity)
    return cosmetic_text(_value_text(left.figure)) == cosmetic_text(_value_text(right.figure))


def _primary(group: Sequence[VerifiedFigure]) -> VerifiedFigure:
    """§5.3: the organisation's own page ahead of a relay; then the latest release."""
    def rank(figure: VerifiedFigure) -> tuple[int, tuple[int, int, int]]:
        key = release_key(figure.finding) or (0, 0, 0)
        return (_ATTRIBUTION_RANK[figure.context.attribution], tuple(-part for part in key))  # type: ignore[return-value]
    return min(group, key=rank)


def fact_rows(findings: Sequence[Finding], targets: Sequence[EvidenceTarget]) -> list[FactRow]:
    """§5.3 and PD-9: one row per fact; revisions folded; row ids K001, K002, ..."""
    by_id = {target.target_id: target for target in targets}
    groups: list[list[VerifiedFigure]] = []
    for figure in verified_figures(findings):
        for group in groups:
            if _same_fact(group[0], figure):
                group.append(figure)
                break
        else:
            groups.append([figure])
    rows: list[FactRow] = []
    for group in groups:
        primary = _primary(group)
        target_ids = sorted(
            {t for figure in group for t in figure.finding.target_ids
             if t in by_id and _figure_answers(figure, by_id[t])}
        )
        dimension = primary.quantity.dimension if primary.quantity is not None else None
        measure = next((by_id[t].measure for t in target_ids if by_id[t].measure), None)
        rows.append(
            FactRow(
                row_id="pending",
                organisation=primary.context.organisation,
                attribution=primary.context.attribution,
                relay_host=publisher_identity(primary.finding.source_url)
                if primary.context.attribution == "relayed" else None,
                measure=measure or _MEASURE_BY_DIMENSION.get(dimension or "", "stated figure"),
                period=primary.context.period,
                value=_value_text(primary.figure),
                kind=primary.context.kind,
                scope=primary.context.scope,
                release=release_text(primary.finding),
                finding_id=primary.finding_id,
                duplicate_finding_ids=sorted({f.finding_id for f in group} - {primary.finding_id}),
                target_ids=target_ids,
                context_unchecked=primary.unchecked,
            )
        )
    by_finding = {finding_fingerprint(f): f for f in findings}
    folded = _fold_revisions(rows, by_finding)
    return [row.model_copy(update={"row_id": f"K{n:03d}"}) for n, row in enumerate(folded, start=1)]


def _fold_revisions(rows: list[FactRow], findings: Mapping[str, Finding]) -> list[FactRow]:
    """PD-9: same organisation, target, period and kind, both released, releases differ."""
    kept = list(rows)
    while True:
        pair = next(
            ((a, b) for a in kept for b in kept
             if a is not b and set(a.target_ids) & set(b.target_ids)
             and a.kind == b.kind and same_period(a.period, b.period)
             and same_organisation(a.organisation, b.organisation)
             and (ka := release_key(findings[a.finding_id])) is not None
             and (kb := release_key(findings[b.finding_id])) is not None and ka > kb),
            None,
        )
        if pair is None:
            return kept
        latest, earlier = pair
        merged = latest.model_copy(update={"earlier": [
            *latest.earlier,
            EarlierEdition(value=earlier.value, release=earlier.release, finding_id=earlier.finding_id),
            *earlier.earlier,
        ]})
        kept = [merged if row is latest else row for row in kept if row is not earlier]


def not_found_targets(
    sub_topics: Sequence[SubTopic],
    answered: Mapping[str, list[str]],
    acquisition: Mapping[str, AcquisitionState],
) -> list[NotFoundTarget]:
    """§6.1 item 5: each required target with no verified finding, and where it was searched."""
    rows: list[NotFoundTarget] = []
    for topic in sub_topics:
        state = acquisition.get(topic.coverage_id)
        for target in topic.evidence_targets:
            if not target.required or target.target_id in answered:
                continue
            pages = list(dict.fromkeys([*(state.read_urls if state else []), *(state.attempted_urls if state else [])]))
            searched = bool(state and (state.attempted_urls or state.read_urls
                                       or state.consecutive_searches or state.empty_searches))
            rows.append(NotFoundTarget(target_id=target.target_id, question=target.question,
                                       queries=list(topic.search_queries), pages_read=pages,
                                       searched=searched))
    return rows


def untraced_numbers(text: str, cited: Sequence[Finding]) -> list[str]:
    """§6.4: the numbers ``text`` states that no kept figure of ``cited`` carries."""
    figures = verified_figures(cited)
    known = [f.quantity for f in figures if f.quantity is not None and f.quantity.base is not None]
    literal = {
        cosmetic_text(f.figure.value).replace(",", "").replace(" ", "")
        for f in figures if f.quantity is None or f.quantity.base is None
    }
    untraced = [
        f"{q.value_text} {q.unit_text}" for q in quantities_in(text)
        if not any(same_quantity(q, k) for k in known)
    ]
    untraced.extend(number for number in bare_numbers(text) if number not in literal)
    return untraced
```

- [ ] **Step 4: Run the tests.** Run: `"$PY" -m pytest -q tests/test_agents/test_verified_facts.py`. Expected: PASS. If `test_not_found_lists_required_unanswered_targets_with_their_trail` fails only on the exact spelling `AcquisitionState` normalises a URL to, assert with the normalised URL the model returns (behaviour is what the test pins, not the trailing slash).

- [ ] **Step 5: Commit.**

```bash
git add src/deep_research/agents/verified_facts.py tests/test_agents/test_verified_facts.py
git commit -m "feat(verified_facts): target answering, fact rows, revisions, Not found and number tracing"
```

**Acceptance:** every rule of §5.3, §6.4 (number tracing) and §6.6 has a passing test; eia.news is never EIA; an own page and its relay are one row that cites the own page.

### Task 3.2: Reader report and evidence log layout

**Role:** sp-implementer. **Wave:** 2A, parallel with Tasks 2.1 and 3.1 (the approved R8 exception: it needs only Task 1.1's types and Task 1.2's `figures.py`). **Depends on:** Gate G1. The controller merges it into the plan branch only after Gate G2 passes (rule R8); its proof is Gate G3.

**Owns:** `src/deep_research/agents/report.py` (additions only; nothing existing changes in this phase, PD-3), `tests/test_agents/test_report_layout.py` (new).

**Interfaces:**
- Consumes: `FactRow`, `NotFoundTarget`, `ReportComposition`, `FigureAttribution`, `FigureKind` (Task 1.1); `quantities_in`, `same_quantity` (`agents/figures.py`); `finding_fingerprint`; `publisher_identity`; the existing `Citation`, `citation_markers`, `render_citations`, `normalize_source_url` in `report.py`.
- Produces: `figure_label`, `written_citations`, `render_written_report`, `render_finding_log` (the `report.py` block of "Shared interfaces"). Task 3.4 renders with them; Task 4.8 publishes with them.

- [ ] **Step 1: Write the failing tests** (`tests/test_agents/test_report_layout.py`):

```python
"""Spec §6.1: the report's shape, its reader labels, and the evidence log."""

from __future__ import annotations

import re

from deep_research.agents.identity import finding_fingerprint
from deep_research.agents.report import (
    figure_label,
    render_finding_log,
    render_written_report,
    written_citations,
)
from deep_research.utils.types import (
    FactRow,
    FigureContext,
    FigureResult,
    FindingVerification,
    NotFoundTarget,
    RejectedDraftPoint,
    ReportComposition,
    ReportPoint,
    ReportSection,
    ReportStatement,
)
from tests.evidence_fakes import figure, make_finding, make_read

VERDICT_WORDS = re.compile(r"\b(verified|unverified|corroborat\w*|independently|insufficient evidence|contested|contradicted|not established)\b", re.I)


def _finding(url, snippet, value, unit, *, organisation, attribution="own", kind="actual", period="2024", dropped_figure=None):
    read = make_read(snippet, url=url, title=f"Page at {url}")
    figures = [figure(value, unit, period, kind)] + ([dropped_figure] if dropped_figure else [])
    finding = make_finding(read, snippet, figures=figures, target_ids=["topic-01-target-01"])
    results = [FigureResult(figure=figures[0], matched=True, evidence_words=snippet,
                            context=FigureContext(period=period, attribution=attribution,
                                                  organisation=organisation, kind=kind))]
    if dropped_figure:
        results.append(FigureResult(figure=dropped_figure, matched=True, dropped_reason="context_rejected",
                                    reason="A growth rate, not a capacity."))
    status = "verified_corrected" if dropped_figure else "verified"
    return finding.model_copy(update={"verification": FindingVerification(status=status, figure_results=results)})


def _composition():
    eia = _finding("https://www.eia.gov/todayinenergy/detail.php?id=64705",
                   "Generators added 10.4 gigawatts (GW) of new battery storage capacity in 2024,",
                   "10.4", "GW", organisation="U.S. Energy Information Administration",
                   dropped_figure=figure("66", "%", "2024", "actual"))
    steo = _finding("https://ent.news/2025/1/940.pdf", "battery storage capacity growing by 47% (14 GW) in 2025",
                    "14", "GW", organisation="U.S. Energy Information Administration",
                    attribution="relayed", kind="forecast", period="2025")
    eia_id, steo_id = finding_fingerprint(eia), finding_fingerprint(steo)
    rows = [
        FactRow(row_id="K001", organisation="U.S. Energy Information Administration", attribution="own",
                measure="battery storage power capacity added", period="2024", value="10.4 GW", kind="actual",
                release="released 2025-03-12", finding_id=eia_id),
        FactRow(row_id="K002", organisation="U.S. Energy Information Administration", attribution="relayed",
                relay_host="ent.news", measure="battery storage power capacity added", period="2025",
                value="14 GW", kind="forecast", release="January 2025 STEO", finding_id=steo_id),
    ]
    def point(n, text, finding):
        return ReportPoint(text=text, source_urls=[finding.source_url],
                           statement=ReportStatement(statement_id=f"S00{n}", text=text,
                                                     finding_ids=[finding_fingerprint(finding)]))
    return ReportComposition(
        question="How much grid-scale battery storage capacity was added in the United States in 2024, and what do the latest forecasts project for 2025?",
        session_id="s", as_of="2026-09-24T00:00:00+00:00", scope="United States",
        findings=[eia, steo],
        summary=[point(1, "Generators added 10.4 GW of battery storage in 2024.", eia),
                 point(2, "EIA expects 14 GW of battery storage to be added in 2025.", steo)],
        sections=[ReportSection(title="Basis", points=[point(3, "EIA counts 10.4 GW of new capacity.", eia)])],
        fact_rows=rows,
        not_found=[NotFoundTarget(target_id="topic-03-target-01", question="What does BloombergNEF project for 2025?",
                                  queries=["BloombergNEF 2025 US storage forecast"], pages_read=["https://about.bnef.com/x"], searched=True)],
        finding_labels={"F01": eia_id, "F02": steo_id},
        rejected_points=[RejectedDraftPoint(where="summary[2]", text="Wood Mackenzie reports 18.9 GW of grid-scale storage.",
                                            finding_labels=["F03"], reason="scope not carried by the cited figures: grid-scale")],
    )


def test_figure_label_follows_the_spec_labels() -> None:
    assert figure_label(organisation="EIA", attribution="own", relay_host=None, kind="actual",
                        release="released 2025-03-12", unchecked=False) == "EIA's own figure; actual; released 2025-03-12"
    assert figure_label(organisation="EIA", attribution="relayed", relay_host="ent.news", kind="forecast",
                        release="January 2025 STEO", unchecked=True) == "relayed by ent.news from EIA; forecast (January 2025 STEO); unchecked context"
    assert figure_label(organisation="ent.news", attribution="unattributed", relay_host=None, kind="forecast",
                        release=None, unchecked=False) == "source does not attribute it; forecast (release not stated on the page)"


def test_the_report_has_the_spec_shape_in_order() -> None:
    report = render_written_report(_composition())
    headings = [line for line in report.splitlines() if line.startswith("#")]
    assert headings[1:] == ["## Executive summary", "## Key facts", "## Basis", "## Not found", "## Sources"]
    assert "| Organisation | Measure | Period | Value | Kind | Scope | Release or edition | Source |" in report
    assert "U.S. Energy Information Administration (relayed by ent.news)" in report
    assert "U.S. Energy Information Administration's own figure; actual; released 2025-03-12" in report
    assert "relayed by ent.news from U.S. Energy Information Administration; forecast (January 2025 STEO)" in report
    assert '"BloombergNEF 2025 US storage forecast"' in report and "Pages read: 1" in report
    assert not VERDICT_WORDS.search(report)


def test_sources_are_only_the_cited_ones_in_first_use_order() -> None:
    index = written_citations(_composition())
    assert [c.number for c in index] == [1, 2]
    assert index[0].url.startswith("https://www.eia.gov") and index[1].url.startswith("https://ent.news")


def test_the_evidence_log_keeps_snippets_drop_reasons_and_refusals() -> None:
    log = render_finding_log(_composition())
    assert "### F01" in log and "Generators added 10.4 gigawatts" in log
    assert "context_rejected" in log and "A growth rate, not a capacity." in log
    assert "Wood Mackenzie reports 18.9 GW of grid-scale storage." in log
    assert "F03" in log and "scope not carried" in log
```

- [ ] **Step 2: Run to see them fail.** Run: `"$PY" -m pytest -q tests/test_agents/test_report_layout.py`. Expected: FAIL (`ImportError: figure_label`).

- [ ] **Step 3: Implement** — append to `src/deep_research/agents/report.py` (import `finding_fingerprint`, `publisher_identity`, `quantities_in`, `same_quantity` and the Task 1.1 types at the top of the module; if an import cycle appears, import `finding_fingerprint` inside the functions that use it, as `ReportComposition`'s validator already does for `agents.identity`):

```python
_FACTS_HEADER = "| Organisation | Measure | Period | Value | Kind | Scope | Release or edition | Source |"


def figure_label(
    *,
    organisation: str,
    attribution: FigureAttribution,
    relay_host: str | None,
    kind: FigureKind,
    release: str | None,
    unchecked: bool,
) -> str:
    """§6.1's reader label: who, kind (with a forecast's release), edition, unchecked."""
    if attribution == "own":
        who = f"{organisation}'s own figure"
    elif attribution == "relayed":
        who = f"relayed by {relay_host or 'another site'} from {organisation}"
    else:
        who = "source does not attribute it"
    if kind == "forecast":
        # PD-24 (F5): a forecast with no release says so, never silently "forecast"
        parts = [who, f"forecast ({release})" if release else "forecast (release not stated on the page)"]
    else:
        parts = [who, kind]
    if kind == "actual" and release:
        parts.append(release)
    if unchecked:
        parts.append("unchecked context")
    return "; ".join(parts)


def _row_label(row: FactRow) -> str:
    return figure_label(organisation=row.organisation, attribution=row.attribution,
                        relay_host=row.relay_host, kind=row.kind, release=row.release,
                        unchecked=row.context_unchecked)


def _table_cell(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")


def _findings_by_id(composition: ReportComposition) -> dict[str, Finding]:
    return {finding_fingerprint(finding): finding for finding in composition.findings}


def written_citations(composition: ReportComposition) -> list[Citation]:
    """Only the pages the report cites, numbered in the order a reader meets them."""
    by_id = _findings_by_id(composition)
    ordered: list[str] = []

    def add(url: str) -> None:
        normalized = normalize_source_url(url)
        if normalized and normalized not in ordered:
            ordered.append(normalized)

    for point in composition.summary:
        for url in point.source_urls:
            add(url)
    for row in composition.fact_rows:
        if row.finding_id in by_id:
            add(by_id[row.finding_id].source_url)
    for section in composition.sections:
        for point in section.points:
            for url in point.source_urls:
                add(url)
    titles = {normalize_source_url(s.url): s.title for s in composition.sources}
    for finding in composition.findings:
        titles.setdefault(normalize_source_url(finding.source_url), finding.source_title)
    return [Citation(number=n, url=url, title=titles.get(url, url)) for n, url in enumerate(ordered, start=1)]


def _point_labels(point: ReportPoint, composition: ReportComposition) -> list[str]:
    cited = set(point.statement.finding_ids) if point.statement is not None else set()
    stated = quantities_in(point.text)
    labels: list[str] = []
    for row in composition.fact_rows:
        if row.finding_id not in cited and not cited & set(row.duplicate_finding_ids):
            continue
        if any(same_quantity(r, s) for r in quantities_in(row.value) for s in stated):
            label = _row_label(row)
            if label not in labels:
                labels.append(label)
    return labels


def _written_point(point: ReportPoint, composition: ReportComposition, index: Sequence[Citation]) -> str:
    markers = citation_markers(point.source_urls, index)
    labels = _point_labels(point, composition)
    suffix = f" — *{' | '.join(labels)}*" if labels else ""
    return f"- {point.text} {markers}{suffix}".rstrip()


def _header_counts(composition: ReportComposition) -> str:
    statuses = [f.verification for f in composition.findings if f.verification is not None]
    checked = sum(1 for v in statuses if v.status != "dropped")
    corrected = sum(1 for v in statuses if v.status == "verified_corrected")
    unchecked = sum(1 for v in statuses if v.status != "dropped" and v.context_unchecked)
    dropped = sum(1 for v in statuses if v.status == "dropped")
    return (f"{len(written_citations(composition))} sources cited; {checked} findings checked against "
            f"their pages ({corrected} with corrected context, {unchecked} with unchecked context), "
            f"{dropped} dropped; {len(composition.not_found)} required targets not found.")


def render_written_report(composition: ReportComposition) -> str:
    """§6.1 items 1-6: header, summary, key facts, findings sections, Not found, sources."""
    index = written_citations(composition)
    by_id = _findings_by_id(composition)
    lines = [
        f"# {composition.question}", "",
        f"*As of {composition.as_of or 'not recorded'}. Scope: {composition.scope or 'not recorded'}. "
        f"{_header_counts(composition)}*", "",
        "## Executive summary", "",
    ]
    lines += [_written_point(p, composition, index) for p in composition.summary] or [
        "No summary statement could be printed from the checked findings; the key facts follow."
    ]
    lines += ["", "## Key facts", ""]
    if composition.fact_rows:
        lines += [_FACTS_HEADER, "|---|---|---|---|---|---|---|---|"]
        for row in composition.fact_rows:
            finding = by_id.get(row.finding_id)
            organisation = {
                "own": row.organisation,
                "relayed": f"{row.organisation} (relayed by {row.relay_host})",
                "unattributed": f"{row.organisation} (source does not attribute it)",
            }[row.attribution]
            release = "; ".join([row.release or "not stated", *(
                f"earlier edition {e.value}" + (f" ({e.release})" if e.release else "") for e in row.earlier
            )])
            cells = [organisation, row.measure, row.period or "not stated", row.value,
                     row.kind + (" (unchecked context)" if row.context_unchecked else ""),
                     row.scope or "not stated", release,
                     citation_markers([finding.source_url], index) if finding else ""]
            lines.append("| " + " | ".join(_table_cell(c) for c in cells) + " |")
    else:
        lines.append("No figure passed the Evidence Verifier.")
    for section in composition.sections:
        lines += ["", f"## {section.title}", ""]
        lines += [_written_point(p, composition, index) for p in section.points]
    if composition.not_found:
        lines += ["", "## Not found", ""]
        for target in composition.not_found:
            if target.searched:
                trail = "Searched: " + "; ".join(f'"{q}"' for q in target.queries) + f". Pages read: {len(target.pages_read)}"
                trail += (" (" + ", ".join(target.pages_read[:5]) + ")." if target.pages_read else ".")
            else:
                trail = "Not searched in this run."
            lines.append(f"- **{target.question}** No checked finding answers it. {trail}")
    lines += ["", "## Sources", "", render_citations(index)]
    return "\n".join(lines) + "\n"


def render_finding_log(composition: ReportComposition) -> str:
    """§6.1 item 7: every finding with its snippet and verification, every drop and refusal."""
    labels = {finding_id: label for label, finding_id in composition.finding_labels.items()}
    lines = [f"# Evidence log: {composition.question}", "",
             f"Session {composition.session_id}, pass {composition.iteration}. Every finding the "
             "researcher recorded, with its snippet and its verification result.", "", "## Findings", ""]
    unlabelled = 0
    for finding in composition.findings:
        label = labels.get(finding_fingerprint(finding))
        if label is None:
            unlabelled += 1
            label = f"X{unlabelled:02d}"
        verification = finding.verification
        if verification is None:
            status = "not checked"
        else:
            status = {"verified": "verified", "verified_corrected": "verified with corrections",
                      "dropped": f"dropped ({verification.dropped_reason})"}[verification.status]
            if verification.context_unchecked:
                status += "; context unchecked"
        lines += [f"### {label} — {finding.source_title}", "", f"- Source: {finding.source_url}",
                  f"- Read: {finding.read_id or 'none'}, locator {finding.locator or 'none'}",
                  f'- Snippet: "{finding.snippet or ""}"', f"- Verification: {status}"]
        for result in verification.figure_results if verification else []:
            text = f"{result.figure.value} {result.figure.unit}"
            if result.kept and result.context is not None:
                context = result.context
                line = (f"  - {text}: kept; period {context.period or 'not stated'}; scope "
                        f"{context.scope or 'not stated'}; "
                        + figure_label(organisation=context.organisation, attribution=context.attribution,
                                       relay_host=publisher_identity(finding.source_url), kind=context.kind,
                                       release=None, unchecked=verification.context_unchecked))
                if result.evidence_words:
                    line += f'; evidence words: "{result.evidence_words}"'
                if result.corrected:
                    line += "; corrected"
            else:
                line = f"  - {text}: dropped ({result.dropped_reason})" + (f": {result.reason}" if result.reason else "")
            lines.append(line)
        lines.append("")
    if composition.rejected_points:
        lines += ["## Refused sentences", ""]
        for rejected in composition.rejected_points:
            lines.append(f'- "{rejected.text}" (cited {", ".join(rejected.finding_labels) or "nothing"}): {rejected.reason}')
    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: Run the tests.** Run: `"$PY" -m pytest -q tests/test_agents/test_report_layout.py tests/test_agents/test_report.py`. Expected: PASS (`test_report.py` proves nothing existing moved).

- [ ] **Step 5: Commit.**

```bash
git add src/deep_research/agents/report.py tests/test_agents/test_report_layout.py
git commit -m "feat(report): the evidence-verifier report layout, reader labels and evidence log (spec 6.1)"
```

**Acceptance:** the report has §6.1's sections in order, labels on every figure, only cited sources, a Not found trail and no verdict wording; the evidence log keeps every snippet, drop reason and refused sentence.

### Task 3.3: Shared wording rules

**Role:** sp-hard-implementer. **Wave:** 1B, parallel with Tasks 1.2, 1.3 and 1.4 (the approved R8 exception: it only moves `synthesizer.py` helpers into a new module and adds pure functions, so it needs no Phase-1 or Phase-2 output). **Depends on:** Task 1.1 merged.

**Owns:** `src/deep_research/agents/wording.py` (new), `src/deep_research/agents/synthesizer.py`, `tests/test_agents/test_wording.py` (new).

**Superseded by D7/D8:** D8 deletes the writer's code checks on sentence wording, so `hedge_forecast`, `page_modal`, `unattested_names` and `stated_scopes` end this plan with no caller unless something else reads them; Task 4.10's sweep removes each one with its export and tests (the D8 contract, `SDD/d8-contract.md`). The moved rules and `stated_role` (the Evidence Verifier's kind fallback) stand. Apply the contract where this body differs; do not rewrite the body.

**Interfaces:**
- Produces: the `wording.py` block of "Shared interfaces". `synthesizer.py` keeps working unchanged (it imports what moved). Task 2.1 imports `stated_role` from `wording.py`; Task 3.4 imports the rest.

- [ ] **Step 1: Write the failing tests** (`tests/test_agents/test_wording.py`):

```python
"""The wording rules the Evidence Verifier and the Report Writer share (PD-19)."""

from __future__ import annotations

from deep_research.agents.wording import (
    hardened_modality,
    hedge_forecast,
    hedge_marker,
    page_modal,
    stated_role,
    stated_scopes,
    stated_years,
    unattested_names,
)


def test_the_moved_rules_behave_as_before() -> None:
    assert hedge_marker("capacity could set a record") == "could"
    assert stated_role("16 GW was installed in 2025") == "actual"
    assert stated_role("EIA forecast that 18.2 GW would be added in 2025") == "forecast"
    assert stated_role("the operator beat projections: 16 GW was installed in 2025") == "mixed"
    assert hardened_modality("the fleet will set a record", "capacity could set a record") == "will"


def test_a_verb_after_to_be_is_not_an_outcome() -> None:
    assert stated_role("18.2 GW is expected to be added in 2025") == "forecast"


def test_names_must_be_attested_and_an_acronym_may_be_spelled_out() -> None:
    corpus = "U.S. Energy Information Administration expects 14 GW"
    assert unattested_names("Analysts say EIA expects 14 GW", corpus.casefold(), corpus) == []
    assert "BloombergNEF" in unattested_names("Analysts say BloombergNEF expects 14 GW", corpus.casefold(), corpus)


def test_years_and_scopes_are_read_from_the_text() -> None:
    assert stated_years("From 2024 to 2025, 10.4 GW") == ["2024", "2025"]
    assert stated_scopes("18.9 GW of grid scale storage") == ["grid-scale"]
    assert stated_scopes("utility, C&I, and residential systems") == ["residential", "c&i"]


def test_hedge_forecast_makes_a_forecast_read_as_one() -> None:
    eia = "U.S. Energy Information Administration"
    rewritten = hedge_forecast("EIA's outlook adds 14 GW in 2025.", eia)
    assert rewritten == "EIA's outlook adds 14 GW in 2025, according to U.S. Energy Information Administration's forecast."
    assert stated_role(rewritten) == "forecast"
    passive = hedge_forecast("In 2025, 18.2 GW was added.", eia)
    assert passive == "In 2025, 18.2 GW is expected to be added."
    assert stated_role(passive) == "forecast"
    active = hedge_forecast("Developers added 14 GW in 2025.", eia)
    assert active == "Developers expected to add 14 GW in 2025."
    assert stated_role(active) == "forecast"
```

Add, pinning F3's hardened-modality repair:

```python
def test_hedge_forecast_takes_the_pages_own_modal() -> None:
    eia = "U.S. Energy Information Administration"
    assert hedge_forecast("Storage will grow 47% in 2025.", eia, marker="could") == "Storage could grow 47% in 2025."
    assert hedge_forecast("Storage would reach 14 GW in 2025.", eia) == "Storage is expected to reach 14 GW in 2025."
    assert hardened_modality("Storage could grow 47% in 2025.", "capacity could grow") == ""
    assert page_modal("Battery storage capacity could grow by 47% (14 GW) in 2025.") == "could"
    assert page_modal("The outlook was released in May 2025.") == ""
```

- [ ] **Step 2: Run to see them fail.** Run: `"$PY" -m pytest -q tests/test_agents/test_wording.py`. Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Move the rules.** Create `agents/wording.py` with a module docstring ("The wording rules the Evidence Verifier and the Report Writer share: hedges, forecast versus outcome, attested names, years and scopes (PD-19)."). Move these definitions out of `synthesizer.py` **unchanged**, with their comments: `_FIGURE_PATTERN` (line 174), `_PROPER_NOUN_PATTERN` (182), `_ACRONYM_PATTERN` (187), `_SENTENCE_INITIAL` (193), `_is_significant_figure` (221), `_figure_number` (241), `_significant_figures` (247), `_HEDGE_PATTERN` (367), `_HEDGE_REPORTING_PATTERN` (380), `_HEDGE_MAY_PATTERN` (386), `_STRONG_MODALS` (392), the clause-split pattern above `_clause_around` and `_clause_around` itself (410), `_content_tokens` (1669), `_corpus_tokens` (1678), `unattested_atoms` (1697), `_INITIAL_SKIP`, `_COUNTRY_PREFIXES`, `_NAME_PUNCT`, `_is_title_word`, `_run_initial`, `_name_runs`, `_run_words`, `_spelled_out`, `_name_attested` (1750–1875), `unattested_words` (1877), `hedge_marker` (1889), `hardened_modality` (1943), `_UNIT_WORDS` (2963), `_COMMON_ABBREVIATIONS` (2988), `_FORECAST_MARKER_PATTERN` (3303), `_forecast_role` (3311), `_REALIZED_OUTCOME_PATTERN` (3326), `_FUTURE_MODAL_PATTERN` (3331), `_realized_outcome` (3336) and `_stated_role` (3356), plus any private helper one of them calls that nothing else in `synthesizer.py` needs. Rename two on the way: `_clause_around` → `clause_around` and `_stated_role` → `stated_role`, updating every call site in `synthesizer.py` to the new names. In `synthesizer.py` add one `from deep_research.agents.wording import (...)` block naming every moved name it still uses (it is deleted whole in Task 4.10). Do not touch `evidence_verifier.py` (Task 1.2 owns it in this wave; Task 2.1 imports `stated_role` from `wording.py`).

- [ ] **Step 4: Add the new rules** to `wording.py`:

```python
def unattested_names(text: str, corpus: str, raw_corpus: str = "") -> list[str]:
    """The proper names ``text`` states that ``corpus`` does not: the name half of
    ``unattested_atoms``, for sentences whose figures are checked by the
    structured-figure rule instead (spec §6.2)."""
```

Move the proper-noun loop of `unattested_atoms` into `unattested_names` verbatim, and make `unattested_atoms` return its figure part followed by `unattested_names(text, corpus, raw_corpus)` (same result as before; `test_synthesizer.py` proves it). Then:

```python
_YEAR_TOKEN = re.compile(r"\b(?:19|20)\d{2}\b")


def stated_years(text: str) -> list[str]:
    """The four-digit years ``text`` names, in order, once each."""
    return list(dict.fromkeys(_YEAR_TOKEN.findall(text)))


# Question-independent segment and basis words (spec §6.2: "no ... scope that the
# cited findings' verified fields do not carry"). Longest first.
SCOPE_TERMS: tuple[str, ...] = (
    "commercial and industrial", "front-of-the-meter", "behind-the-meter",
    "utility-scale", "grid-scale", "all segments", "all sectors", "residential",
    "commercial", "industrial", "distributed", "community", "c&i",
)


def stated_scopes(text: str) -> list[str]:
    """The scope terms ``text`` states, hyphen and space spellings alike."""
    folded = " ".join(text.casefold().replace("-", " ").split())
    found: list[str] = []
    for term in SCOPE_TERMS:
        pattern = re.escape(term.replace("-", " "))
        if re.search(rf"(?<![a-z&]){pattern}(?![a-z])", folded) and term not in found:
            found.append(term)
    return found


_PAST_PASSIVE = re.compile(r"\b(was|were|has been|have been)\s+(added|installed|deployed|commissioned|built)\b", re.I)
_PAST_ACTIVE = re.compile(r"\b(added|installed|deployed|commissioned|built|reached|hit|exceeded|surpassed)\b", re.I)
_BASE_FORM = {"added": "add", "installed": "install", "deployed": "deploy", "commissioned": "commission",
              "built": "build", "reached": "reach", "hit": "hit", "exceeded": "exceed", "surpassed": "surpass"}


_WILL_WOULD = re.compile(r"\b(will|would)\b", re.I)
# Lower case only: "May" in "May 2025" is a month, not a hedge.
_PAGE_MODAL = re.compile(r"\b(could|might|may)\b")


def page_modal(text: str) -> str:
    """The first hedging modal the page's own words use ("could"), or ""."""
    match = _PAGE_MODAL.search(text)
    return match.group(1) if match else ""


def hedge_forecast(text: str, organisation: str, *, marker: str = "") -> str:
    """Spec §6.2: re-attach the verified hedge to a forecast stated as fact, once.

    ``marker`` is the page's own modal for the figure (``page_modal`` of its
    evidence words). A hardened "will"/"would" takes that modal, else "is
    expected to" (F3); a past-tense outcome verb becomes an expectation.
    """
    if _WILL_WOULD.search(text):
        modal = marker if marker in {"could", "might", "may"} else "is expected to"
        return _WILL_WOULD.sub(modal, text)

    def passive(match: re.Match[str]) -> str:
        plural = match.group(1).casefold() in {"were", "have been"}
        return f"{'are' if plural else 'is'} expected to be {match.group(2)}"

    def active(match: re.Match[str]) -> str:
        if re.search(r"\bbe\s+\Z", match.string[: match.start()], re.I):
            return match.group(0)
        return f"expected to {_BASE_FORM[match.group(1).casefold()]}"

    hedged = _PAST_ACTIVE.sub(active, _PAST_PASSIVE.sub(passive, text))
    if not _forecast_role(hedged):
        hedged = f"{hedged.rstrip().rstrip('.')}, according to {organisation}'s forecast."
    return hedged
```

and change `_realized_outcome`'s infinitive guard from `r"\bto\s+\Z"` to `r"\bto\s+(?:be\s+|have\s+been\s+)?\Z"` ("expected to be added" is a plan, not an outcome).

- [ ] **Step 5: Run the tests.** Run: `"$PY" -m pytest -q tests/test_agents/test_wording.py tests/test_agents/test_synthesizer.py`. Expected: PASS. A `test_synthesizer.py` failure means a moved rule changed behaviour: restore it (only `_realized_outcome`'s guard may change; if a synthesizer test pinned the old "to be <verb>" reading, report its id to the controller).

- [ ] **Step 6: Commit.**

```bash
git add src/deep_research/agents/wording.py src/deep_research/agents/synthesizer.py tests/test_agents/test_wording.py
git commit -m "refactor(wording): shared hedge, forecast and attested-name rules move out of the synthesizer"
```

**Acceptance:** the moved rules behave as before (the synthesizer's tests pass); the new rules pass their tests, including both hedge-rewrite paths (a past-tense outcome and a hardened "will"/"would", F3); nothing outside `synthesizer.py` imports a moved private name, and Task 2.1 imports `stated_role` from `wording.py`.

### Task 3.4: The Report Writer agent

**Role:** sp-hard-implementer. **Wave:** 3B, alone. **Depends on:** Gate G2, then Tasks 3.1 and 3.2 merged (the controller merges them after G2, rule R8); Task 3.3 was merged in wave 1B.

**Owns:** `src/deep_research/agents/report_writer.py` (new), `tests/test_agents/test_report_writer.py` (new).

**Superseded by D7/D8:** the D8 contract (`SDD/d8-contract.md`) deletes every code check on sentence wording in this body — untraced numbers, dates, scopes, names, forecast-versus-actual, the hedge rewrite, `_governing_position` and its helpers — and puts the Statement Check (`check_statements`, §5.4) in their place: one call after drafting, `consistent` keeps the sentence, `corrected` replaces it with `corrected_text`, `inconsistent` refuses it with its reason, and a failed batch keeps its sentences with the error recorded. Code keeps only the known-label requirement, the length limit and the full-text refusal record, and the code-built labels stay on every kept sentence (§6.2). Apply the contract where this body differs; do not rewrite the body.

**Interfaces:**
- Consumes: `verified_facts` (Task 3.1); `figure_label`, `render_written_report`, `render_finding_log`, `written_citations`, `report_as_of`, `report_scope` (`report.py`, Task 3.2 and existing); `hedge_forecast`, `page_modal`, `hardened_modality`, `stated_role`, `clause_around`, `stated_scopes`, `stated_years`, `unattested_names` (Task 3.3); `quantities_in`, `same_quantity`, `dates_in`, `without_dates` (`figures.py`, Task 1.2); `ScoredSource` and its `identity_anchors` (PD-25); `cosmetic_text`; `finding_fingerprint`; `publisher_identity`; `render_structured_reply_format`; `OUTPUT_LIMIT_RETRY_EFFORT`, `BaseAgent`, `AgentRun`, `AgentTask` (`agents/base.py`); `ReActRun`; `agent_error`; `agent_event`; the provider errors.
- Produces: the `report_writer.py` block of "Shared interfaces" (the filename helpers arrive in Task 4.1). The registry line format `F01 | figure 1: <value> <unit> | period <period> | kind <kind> | organisation <name> | label: <label>` is a contract: Task 4.9's replay double parses it.

- [ ] **Step 1: Write the failing tests** (`tests/test_agents/test_report_writer.py`). Build the agent like `_synthesizer` in `tests/test_agents/test_synthesizer.py` lines 314–331 (a `ScriptedCompleter` from `tests/agent_fakes.py`, a `ScratchpadMemory`, `AgentRuntimeConfig(max_iterations=2, tool_budget=0)`), and run it in the same tracker/session scope that file's async `run` tests use; construct `ProviderOutputLimitError` and `ProviderError` the way that file does.

```python
"""Spec §6.1-6.2: the Report Writer writes from verified findings only."""

from __future__ import annotations

import re

from deep_research.agents.report_writer import (
    ReportWriterDraft,
    WriterPointDraft,
    check_point,
    compose_written_report,
    finding_registry,
    writer_messages,
)
from deep_research.utils.types import FigureContext, FigureResult, FindingVerification, ResearchState, SubTopic
from tests.evidence_fakes import figure, make_finding, make_read, make_target

EIA = "U.S. Energy Information Administration"


def _checked(url, text, value, unit, *, organisation, attribution="own", kind="actual", period="2024",
             scope=None, target="topic-01-target-01", **fields):
    read = make_read(text, url=url, title=f"{organisation} page")
    finding = make_finding(read, text, figures=[figure(value, unit, period, kind)], target_ids=[target], **fields)
    result = FigureResult(figure=finding.figures[0], matched=True, evidence_words=text,
                          context=FigureContext(period=period, scope=scope, attribution=attribution,
                                                organisation=organisation, kind=kind))
    return finding.model_copy(update={"verification": FindingVerification(status="verified", figure_results=[result])})


EIA_2024 = _checked("https://www.eia.gov/todayinenergy/detail.php?id=64705",
                    "Generators added 10.4 gigawatts (GW) of new battery storage capacity in 2024,",
                    "10.4", "GW", organisation=EIA, release_date="2025-03-12")
STEO = _checked("https://ent.news/2025/1/940.pdf",
                "Data source: U.S. Energy Information Administration, Short-Term Energy Outlook, January 2025. "
                "Battery storage capacity grows by 47% (14 GW) in 2025.",
                "14", "GW", organisation=EIA, attribution="relayed", kind="forecast", period="2025",
                target="topic-02-target-01", vintage="January 2025 STEO")
WOODMAC_ALL = _checked("https://www.woodmac.com/press-releases/2025-record",
                       "The U.S. energy storage market hit a record 18.9 gigawatts of battery energy storage system "
                       "installations in 2025 across all segments.",
                       "18.9", "gigawatts", organisation="Wood Mackenzie", period="2025", scope="all segments",
                       target="topic-04-target-01")


def _task_state():
    targets = [make_target(organisation="EIA"),
               make_target("topic-02-target-01", kind="forecast", period="2025", organisation="EIA"),
               make_target("topic-04-target-01", period="2025", required=False)]
    topics = [SubTopic(coverage_id=t.coverage_id, title=t.target_id, rationale="r", search_queries=["q"],
                       success_criteria=["c"], priority=n, evidence_targets=[t]) for n, t in enumerate(targets, 1)]
    return ResearchState(session_id="s", original_question="How much battery storage was added in 2024, and what is forecast for 2025?",
                         sub_topics=topics, verified_findings=[EIA_2024, STEO, WOODMAC_ALL])


def _labels(registry):
    return {finding.source_url.split("/")[2]: label for label, finding in registry}


def test_the_registry_labels_citable_findings_answers_first() -> None:
    state = _task_state()
    targets = [t for topic in state.sub_topics for t in topic.evidence_targets]
    registry = finding_registry(state.verified_findings, targets)
    assert [label for label, _ in registry] == ["F01", "F02", "F03"]
    assert registry[2][1] is WOODMAC_ALL   # answers only an optional target


def test_writer_messages_list_every_figure_in_the_fixed_format(writer) -> None:
    messages = writer_messages(writer.build_task(_task_state()))
    line = re.compile(r"^F\d{2} \| figure 1: 14 GW \| period 2025 \| kind forecast \| organisation " + re.escape(EIA)
                      + r" \| label: relayed by ent\.news from " + re.escape(EIA) + r"; forecast \(January 2025 STEO\)$", re.M)
    assert line.search(messages[-1].content)


def test_check_point_refuses_untraced_numbers_and_unattested_names() -> None:
    assert check_point("Generators added 12 GW in 2024.", [EIA_2024], geographies=["United States"]).reasons
    assert check_point("BloombergNEF reports 10.4 GW added in 2024.", [EIA_2024], geographies=[]).reasons
    assert check_point("Generators added 10.4 GW in the United States in 2024.", [EIA_2024],
                       geographies=["United States"]).reasons == ()


def test_grid_scale_wording_on_an_all_segment_figure_is_refused(writer) -> None:
    task = writer.build_task(_task_state())
    label = _labels(task.registry)["www.woodmac.com"]
    draft = ReportWriterDraft(executive_summary=[WriterPointDraft(
        text="Wood Mackenzie reports 18.9 GW of grid-scale storage installed in 2025.", finding_labels=[label])], sections=[])
    composition = compose_written_report(task, draft)
    assert composition.summary == []
    [refused] = composition.rejected_points
    assert "grid-scale" in refused.reason and refused.finding_labels == [label]


def test_forecast_stated_as_fact_is_rewritten_once_and_kept(writer) -> None:
    task = writer.build_task(_task_state())
    label = _labels(task.registry)["ent.news"]
    draft = ReportWriterDraft(executive_summary=[WriterPointDraft(
        text="EIA's outlook adds 14 GW in 2025.", finding_labels=[label])], sections=[])
    composition = compose_written_report(task, draft)
    [point] = composition.summary
    assert point.text == f"EIA's outlook adds 14 GW in 2025, according to {EIA}'s forecast."
    assert composition.rejected_points == []


def test_a_hardened_forecast_takes_the_pages_own_modal(writer) -> None:
    # F3: the 2026-09-24 pre-flight lost its forecasts to "will" where the page says "could"
    could = _checked("https://ent.news/2025/1/941.pdf",
                     "Data source: U.S. Energy Information Administration, Short-Term Energy Outlook, January 2025. "
                     "Battery storage capacity could grow by 14 GW in 2025.",
                     "14", "GW", organisation=EIA, attribution="relayed", kind="forecast", period="2025",
                     target="topic-02-target-01", vintage="January 2025 STEO")
    task = writer.build_task(_task_state().model_copy(update={"verified_findings": [EIA_2024, could]}))
    label = _labels(task.registry)["ent.news"]
    draft = ReportWriterDraft(executive_summary=[WriterPointDraft(
        text="EIA's STEO says battery capacity will grow by 14 GW in 2025.", finding_labels=[label])], sections=[])
    composition = compose_written_report(task, draft)
    [point] = composition.summary
    assert point.text == "EIA's STEO says battery capacity could grow by 14 GW in 2025."
    assert composition.rejected_points == []


def test_a_release_date_is_a_date_not_an_untraced_number(writer) -> None:
    # F2: "2025-03-12" is checked whole against the findings, never as the numbers 03 and 12
    task = writer.build_task(_task_state())
    label = _labels(task.registry)["www.eia.gov"]
    kept = ReportWriterDraft(executive_summary=[WriterPointDraft(
        text="EIA reports that 10.4 GW was added in 2024 (released 2025-03-12).", finding_labels=[label])], sections=[])
    composition = compose_written_report(task, kept)
    assert len(composition.summary) == 1 and composition.rejected_points == []
    invented = ReportWriterDraft(executive_summary=[WriterPointDraft(
        text="EIA reports that 10.4 GW was added in 2024 (released 2025-04-30).", finding_labels=[label])], sections=[])
    [refused] = compose_written_report(task, invented).rejected_points
    assert "dates the cited findings do not carry: 2025-04-30" in refused.reason


def test_an_actual_stated_as_a_forecast_is_refused(writer) -> None:
    task = writer.build_task(_task_state())
    label = _labels(task.registry)["www.eia.gov"]
    draft = ReportWriterDraft(executive_summary=[WriterPointDraft(
        text="EIA expects 10.4 GW to be added in 2024.", finding_labels=[label])], sections=[])
    assert compose_written_report(task, draft).summary == []


def test_a_summary_restatement_and_an_unknown_label_are_refused(writer) -> None:
    task = writer.build_task(_task_state())
    label = _labels(task.registry)["www.eia.gov"]
    draft = ReportWriterDraft(executive_summary=[
        WriterPointDraft(text="Generators added 10.4 GW of battery storage in 2024.", finding_labels=[label]),
        WriterPointDraft(text="In 2024, 10.4 GW of battery storage was added.", finding_labels=[label]),
        WriterPointDraft(text="Generators added 10.4 GW in 2024.", finding_labels=["F99"]),
    ], sections=[])
    composition = compose_written_report(task, draft)
    assert [p.statement.statement_id for p in composition.summary] == ["S001"]
    assert [r.where for r in composition.rejected_points] == ["summary[1]", "summary[2]"]


async def test_a_failed_draft_still_composes_the_key_facts(writer_failing) -> None:
    run = await writer_failing.run(_task_state())
    assert "## Key facts" in run.result.markdown and "10.4 GW" in run.result.markdown
    assert any(e.error_type == "report_writer_provider_error" for e in run.errors)


async def test_a_truncated_draft_is_asked_once_more_at_high_effort(writer_truncated_then_ok) -> None:
    agent, completer = writer_truncated_then_ok
    run = await agent.run(_task_state())
    assert [name for name, _, _ in completer.calls] == ["ReportWriterDraft", "ReportWriterDraft"]
    assert completer.efforts == [None, "high"] and run.result.statement_count >= 1   # profile effort, then high (F10)
```

Write the three fixtures (`writer`: a scripted completer with no queued output; `writer_failing`: one queued `ProviderError`; `writer_truncated_then_ok`: a queued `ProviderOutputLimitError` then a `ReportWriterDraft` with one valid point citing the EIA label) in the same file, following `_synthesizer`. Mark the async tests with `pytest.mark.asyncio` if the repo's config does not do it automatically (check `tests/test_agents/test_synthesizer.py`).

- [ ] **Step 2: Run to see them fail.** Run: `"$PY" -m pytest -q tests/test_agents/test_report_writer.py`. Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement** `src/deep_research/agents/report_writer.py`. Mirror `SynthesizerAgent`'s class structure and member signatures (`synthesizer.py` 4349–4714: `__init__` with `provider`, `tracker`, `scratchpad`, `tools`, `config`, `model_profile`, `clock`; `output_schema`; `system_prompt`; `build_task`; `finalize`; `state_update`; `run`; `publish_document`; `publish_claim`, renamed `publish_finding`; `_require_tool`), copying `publish_document`, `publish_claim` (as `publish_finding`) and `_require_tool` verbatim and replacing the other bodies with this module's logic:

```python
"""The Report Writer (spec §6): prose from verified findings only, cited by label.

The model writes the executive summary and a few short sections, citing
findings by the labels one registry stamps. Code builds everything else -- the
key facts table, duplicates and revisions, the Not found list, the labels and
the sources -- and checks every sentence against the cited findings' verified
fields before it can reach the reader (§6.2).
"""

REPORT_WRITER_NAME = "report_writer"
DEFAULT_MAX_SECTIONS = 4
MAX_POINT_CHARS = 600
_SECTION_TITLE_CHARS = 120
# F10: the first attempt runs at the resolved profile's effort (config.yaml
# model_overrides.report_writer, the one effort source); a truncated draft is
# asked once more at high, the synthesizer's measured retry.
_WRITER_ATTEMPT_EFFORTS: tuple[str | None, ...] = (None, OUTPUT_LIMIT_RETRY_EFFORT)

REPORT_WRITER_SYSTEM_PROMPT = (
    "You write the reader-facing prose of a research report from verified findings. "
    "Every finding you may cite is listed with a label (F01, F02, ...), its verbatim "
    "snippet, and each of its figures with the figure's verified period, kind (actual "
    "or forecast), organisation and reader label. Code builds the key facts table, the "
    "Not found list and the sources; you write the executive summary and a few short "
    "explanatory sections."
)

REPORT_WRITER_INSTRUCTION = (
    "Rules:\n"
    "- Cite by label only: every point lists in finding_labels the one to three labels it "
    "rests on. Never write a URL.\n"
    "- Every number you write must be a figure of a finding the point cites, with its unit "
    "as listed (\"10.4 GW\"). Do not add, subtract, convert or round figures.\n"
    "- Name only organisations, dates and scopes that the cited findings' figures, labels or "
    "snippets state.\n"
    "- Use the scope words the finding states (\"utility-scale\", \"all segments\"), never "
    "the question's.\n"
    "- State an actual as what happened (\"added\", \"installed\"). State a forecast as a "
    "forecast of its organisation (\"EIA expects\", \"Wood Mackenzie projects\") and give its "
    "release when the label shows one.\n"
    "- For a figure one site relays from another organisation, name the organisation and "
    "the site (\"according to Wood Mackenzie, as reported by Utility Dive\").\n"
    "- The executive summary answers each part of the question directly, first: the actual "
    "figure the question asks for; then each organisation's latest forecast with its "
    "release; then later actuals, labelled as actuals. One point per fact; never state the "
    "same figure twice.\n"
    "- At most four sections, explaining segment basis, revisions, units or definitions, "
    "only as the findings state them.\n"
    "- Never write verdict or corroboration words: verified, confirmed, corroborated, "
    "independently, insufficient evidence, contested."
)

_WRITER_REPLY_EXAMPLES = (
    (
        "Example input: F01 | figure 1: 12 percent | period 2024 | kind actual | organisation "
        "Example Statistical Agency | label: Example Statistical Agency's own figure; actual; "
        "released 2025-02-01",
        '{"executive_summary":[{"text":"The Example Statistical Agency reports a 12 percent '
        'reduction in 2024.","finding_labels":["F01"]}],"sections":[]}',
    ),
)


class WriterPointDraft(ContractModel):
    text: str
    finding_labels: list[str] = Field(default_factory=list)


class WriterSectionDraft(ContractModel):
    title: str
    points: list[WriterPointDraft] = Field(default_factory=list)


class ReportWriterDraft(ContractModel):
    """The provider-facing reply: prose and labels, nothing else."""

    executive_summary: list[WriterPointDraft] = Field(default_factory=list)
    sections: list[WriterSectionDraft] = Field(default_factory=list)


class ReportWriterTask(AgentTask):
    session_id: str
    iteration: int = 0
    max_iterations: int = 0          # Task 4.1 renames this max_extra_passes
    question: str
    as_of: str = ""
    scope: str = ""
    generated_on: str = ""
    sub_topics: list[SubTopic] = Field(default_factory=list)
    targets: list[EvidenceTarget] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)       # the whole verified snapshot
    sources: list[ScoredSource] = Field(default_factory=list)
    registry: list[tuple[str, Finding]] = Field(default_factory=list)
    facts: list[FactRow] = Field(default_factory=list)
    not_found: list[NotFoundTarget] = Field(default_factory=list)
    answered: dict[str, list[str]] = Field(default_factory=dict)
    geographies: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class PointCheck:
    reasons: tuple[str, ...]
    forecast_as_fact: bool      # True: the one hedge rewrite may repair it (F3)
    organisation: str | None
    marker: str                 # the page's own modal for the rewrite, or ""


class WrittenReport(ContractModel):
    markdown: str
    evidence_markdown: str
    composition: ReportComposition
    statement_count: int = Field(ge=0)
    citation_count: int = Field(ge=0)
    refused_count: int = Field(ge=0)


def finding_registry(findings, targets) -> list[tuple[str, Finding]]:
    """One label per citable finding: answers to required targets first, then the rest."""
    citable = citable_findings(findings)
    answered = answered_target_ids(citable, [t for t in targets if t.required])
    first = list(dict.fromkeys(fid for ids in answered.values() for fid in ids))
    rank = {fid: n for n, fid in enumerate(first)}
    ordered = sorted(citable, key=lambda f: rank.get(finding_fingerprint(f), len(rank)))
    return [(f"F{n:02d}", finding) for n, finding in enumerate(ordered, start=1)]


def _figure_label_for(finding: Finding, context: FigureContext) -> str:
    return figure_label(
        organisation=context.organisation, attribution=context.attribution,
        relay_host=publisher_identity(finding.source_url) if context.attribution == "relayed" else None,
        kind=context.kind, release=release_text(finding),
        unchecked=bool(finding.verification and finding.verification.context_unchecked),
    )


def registry_lines(label: str, finding: Finding) -> list[str]:
    lines = [f"## {label}: {finding.source_title} ({publisher_identity(finding.source_url)})",
             f"snippet: {finding.snippet or finding.content}"]
    number = 0
    for result in finding.verification.figure_results if finding.verification else []:
        if not result.kept or result.context is None:
            continue
        number += 1
        context = result.context
        lines.append(
            f"{label} | figure {number}: {result.figure.value} {result.figure.unit} | period "
            f"{context.period or 'not stated'} | kind {context.kind} | organisation "
            f"{context.organisation} | label: {_figure_label_for(finding, context)}"
        )
    return lines


def writer_messages(task: ReportWriterTask) -> list[ChatMessage]:
    labels = {finding_fingerprint(f): label for label, f in task.registry}
    targets = "\n".join(
        f"- {t.target_id}: {t.question} ("
        + (", ".join(labels[i] for i in task.answered.get(t.target_id, []) if i in labels) or "not found")
        + ")"
        for t in task.targets if t.required
    ) or "(none)"
    registry = "\n\n".join("\n".join(registry_lines(label, f)) for label, f in task.registry) or "(none)"
    user = "\n\n".join([
        f"# Question\n{task.question}",
        f"# Required targets and the findings that answer them\n{targets}",
        f"# Verified findings\n{registry}",
        f"# Rules\n{REPORT_WRITER_INSTRUCTION}",
        "# Reply format\n" + render_structured_reply_format(_WRITER_REPLY_EXAMPLES),
    ])
    return [ChatMessage(role="developer", content=REPORT_WRITER_SYSTEM_PROMPT),
            ChatMessage(role="user", content=user)]


def _attested_corpus(cited: Sequence[Finding], geographies: Sequence[str],
                     sources: Sequence[ScoredSource] = ()) -> str:
    parts = list(geographies)
    urls = {finding.source_url for finding in cited}
    for source in sources:   # PD-25: the Source Evaluator's validated title and issuer
        if source.url in urls:
            issuer = source.identity_anchors.get("issuer")
            parts += [source.title, *(issuer if isinstance(issuer, list) else [issuer or ""])]
    for finding in cited:
        parts += [finding.snippet or "", finding.source_title, publisher_identity(finding.source_url),
                  release_text(finding) or "", finding.attributed_issuer or ""]
        for result in finding.verification.figure_results if finding.verification else []:
            if result.kept and result.context is not None:
                parts += [result.context.organisation, result.context.period or "",
                          result.context.scope or "", result.evidence_words or ""]
    return "\n".join(part for part in parts if part)


def check_point(text: str, cited: Sequence[Finding], *, geographies: Sequence[str],
                sources: Sequence[ScoredSource] = ()) -> PointCheck:
    """§6.2's guards for one sentence against the findings it cites."""
    if not cited:
        return PointCheck(("cites no checked finding",), False, None, "")
    reasons: list[str] = []
    untraced = untraced_numbers(text, cited)       # dates are not numbers (F2)
    if untraced:
        reasons.append("numbers not among the cited figures: " + ", ".join(untraced))
    corpus = _attested_corpus(cited, geographies, sources)
    names = unattested_names(text, corpus.casefold(), corpus)
    if names:
        reasons.append("names the cited findings do not carry: " + ", ".join(names))
    # F2: a date is checked whole against the findings (a release the writer
    # copies from a label is there); the years rule reads the rest of the text.
    folded = cosmetic_text(corpus)
    dates = [date for date in dates_in(text) if cosmetic_text(date) not in folded]
    if dates:
        reasons.append("dates the cited findings do not carry: " + ", ".join(dates))
    years = [year for year in stated_years(without_dates(text)) if year not in corpus]
    if years:
        reasons.append("years the cited findings do not carry: " + ", ".join(years))
    normalised = cosmetic_text(text)
    stated = quantities_in(text)
    figures = [f for f in verified_figures(cited) if f.quantity is not None]
    stated_figures = [(q, f) for q in stated for f in figures if same_quantity(q, f.quantity)]
    scope_sources = [f"{f.context.scope or ''} {_evidence_words(f)}" for _, f in stated_figures] or [corpus]
    attested_scopes = set(stated_scopes(" ".join(scope_sources)))
    unsupported = [s for s in stated_scopes(text) if s not in attested_scopes]
    if unsupported:
        reasons.append("scope not carried by the cited figures: " + ", ".join(unsupported))
    as_fact: list[str] = []
    for quantity, figure in stated_figures:
        role = stated_role(clause_around(normalised, quantity.start))
        if figure.context.kind == "forecast" and role == "actual":
            as_fact.append(figure.context.organisation)
        elif figure.context.kind == "actual" and role == "forecast":
            reasons.append("an actual stated as a forecast")
        elif role == "mixed":
            reasons.append("one clause reads as both forecast and outcome")
    hardened = hardened_modality(text, corpus)
    forecasts = [f for _, f in stated_figures if f.context.kind == "forecast"]
    # F3: the one rewrite repairs a forecast stated as fact or with hardened
    # modality, and only when nothing else is wrong with the sentence.
    repairable = bool(forecasts) and bool(as_fact or hardened) and not reasons
    if as_fact:
        reasons.append("a forecast stated as fact")
    if hardened:
        reasons.append(f"asserts with '{hardened}' what the page hedges")
    if repairable:
        organisation = as_fact[0] if as_fact else forecasts[0].context.organisation
        marker = page_modal(" ".join(_evidence_words(f) for f in forecasts))
        return PointCheck(tuple(reasons), True, organisation, marker)
    return PointCheck(tuple(reasons), False, None, "")


def _evidence_words(figure: VerifiedFigure) -> str:
    results = figure.finding.verification.figure_results if figure.finding.verification else []
    words = results[figure.index].evidence_words if figure.index < len(results) else None
    return words or (figure.finding.snippet or "")


def compose_written_report(task: ReportWriterTask, draft: ReportWriterDraft | None) -> ReportComposition:
    """Check every drafted point, rewrite a forecast-as-fact once, and compose (§6.1-6.2)."""
    by_label = dict(task.registry)
    ids = {label: finding_fingerprint(f) for label, f in task.registry}
    rejected: list[RejectedDraftPoint] = []
    stated_rows: set[str] = set()
    numbers = iter(range(1, 10_000))

    def build(point: WriterPointDraft, where: str, summary: bool) -> ReportPoint | None:
        text = " ".join(point.text.split())[:MAX_POINT_CHARS]
        wanted = [label.strip() for label in point.finding_labels]

        def refuse(reason: str) -> None:
            rejected.append(RejectedDraftPoint(where=where, text=text, finding_labels=list(point.finding_labels), reason=reason))

        if not text:
            return refuse("empty text")
        unknown = [label for label in wanted if label not in by_label]
        if unknown:
            return refuse("unknown labels: " + ", ".join(unknown))
        cited = [by_label[label] for label in wanted]
        check = check_point(text, cited, geographies=task.geographies, sources=task.sources)
        if check.forecast_as_fact and check.organisation:
            text = hedge_forecast(text, check.organisation, marker=check.marker)
            check = check_point(text, cited, geographies=task.geographies, sources=task.sources)
        if check.reasons:
            return refuse("; ".join(check.reasons))
        cited_ids = {ids[label] for label in wanted}
        stated = quantities_in(text)
        rows = {row.row_id for row in task.facts
                if (row.finding_id in cited_ids or cited_ids & set(row.duplicate_finding_ids))
                and any(same_quantity(r, s) for r in quantities_in(row.value) for s in stated)}
        if summary and rows and rows <= stated_rows:
            return refuse("restates " + ", ".join(sorted(rows)))
        stated_rows.update(rows)
        own_first = sorted(cited, key=lambda f: 0 if any(
            r.context is not None and r.context.attribution == "own"
            for r in (f.verification.figure_results if f.verification else [])) else 1)
        statement = ReportStatement(
            statement_id=f"S{next(numbers):03d}", text=text, finding_ids=[ids[label] for label in wanted],
            target_ids=sorted({t for t, fids in task.answered.items() if cited_ids & set(fids)}),
        )
        return ReportPoint(text=text, source_urls=list(dict.fromkeys(f.source_url for f in own_first)),
                           statement=statement)

    summary = [p for n, d in enumerate(draft.executive_summary if draft else [])
               if (p := build(d, f"summary[{n}]", True)) is not None]
    sections: list[ReportSection] = []
    for s, section in enumerate((draft.sections if draft else [])[:DEFAULT_MAX_SECTIONS]):
        points = [p for n, d in enumerate(section.points)
                  if (p := build(d, f"sections[{s}].points[{n}]", False)) is not None]
        title = " ".join(section.title.split())[:_SECTION_TITLE_CHARS]
        if points and title:
            sections.append(ReportSection(title=title, points=points))
    return ReportComposition(
        question=task.question, session_id=task.session_id, iteration=task.iteration,
        max_iterations=task.max_iterations, as_of=task.as_of, scope=task.scope,
        sub_topics=list(task.sub_topics), sources=list(task.sources), findings=list(task.findings),
        summary=summary, sections=sections, rejected=[r.reason for r in rejected],
        rejected_points=rejected, fact_rows=list(task.facts), not_found=list(task.not_found),
        finding_labels={label: finding_id for label, finding_id in ids.items()},
        generated_on=task.generated_on,
    )


def finding_memory_payload(finding: Finding, *, session_id: str) -> tuple[str, dict[str, JsonValue]]:
    """What long-term memory keeps of one cited finding of an accepted report."""
    figures = [
        f"{r.figure.value} {r.figure.unit} ({r.context.kind}, {r.context.period or 'period not stated'}, {r.context.organisation})"
        for r in (finding.verification.figure_results if finding.verification else [])
        if r.kept and r.context is not None
    ]
    metadata: dict[str, JsonValue] = {
        "session_id": session_id, "finding_id": finding_fingerprint(finding),
        "source_url": finding.source_url, "source_title": finding.source_title,
        "figures": "; ".join(figures),
        "verification": finding.verification.status if finding.verification else "unchecked",
        "context_unchecked": bool(finding.verification and finding.verification.context_unchecked),
    }
    return finding.snippet or finding.content, metadata
```

The agent's own logic:
- `build_task(state)`: `targets` = every target of every sub-topic; `findings = list(state.verified_findings)`; `registry = finding_registry(findings, targets)`; `answered = answered_target_ids(citable_findings(findings), targets)`; `facts = fact_rows(findings, targets)`; `not_found = not_found_targets(state.sub_topics, answered, state.acquisition_state_by_target)`; `as_of = report_as_of(findings=findings, reads=list(state.read_records.values()))`; `scope = report_scope(state.sub_topics)`; `generated_on = self._clock().date().isoformat()`; `geographies` = the targets' distinct non-empty `geography`; `sources = list(state.evaluated_sources)`; `instruction = state.original_question`.
- `draft(task)`: no provider call when `task.registry` is empty (record `report_writer_no_verified_findings`). Otherwise try `_WRITER_ATTEMPT_EFFORTS` in order: for `None`, call `self.provider.complete_structured(writer_messages(task), ReportWriterDraft, agent_name=self.name)` without `reasoning_effort`, so the resolved profile's effort applies (`model_overrides.report_writer`, the one effort source, F10); for a named effort, pass `reasoning_effort=effort`. A `ProviderOutputLimitError` records `report_writer_output_limit` and tries the next attempt; `ProviderError`, `StructuredOutputError` or `ValidationError` records `report_writer_provider_error` and stops; exhausting the ladder records `report_writer_provider_error` too. Every error is `agent_error(agent_name=REPORT_WRITER_NAME, ..., details={"exception_type": type(error).__name__, "attempt": n})`.
- `run(state)`: inside `self.tracker.agent_span(self.name)`, `task = self.build_task(state)`, `draft, errors = await self.draft(task)`, `composition = compose_written_report(task, draft)`, `result = WrittenReport(markdown=render_written_report(composition), evidence_markdown=render_finding_log(composition), composition=composition, statement_count=len(composition.statements), citation_count=len(written_citations(composition)), refused_count=len(composition.rejected_points))`; return `AgentRun(agent_name=self.name, result=result, react=ReActRun(agent_name=self.name, stop_reason="provider_error" if draft is None and task.registry else "finished", errors=errors), errors=errors, state_update=..., call_fingerprints=dict(self._call_fingerprints))`, like Task 2.1's agent.
- `state_update`: `{"report": result.markdown, "report_evidence": result.evidence_markdown, "composition": result.composition, "unique_source_count": result.citation_count, "errors": list(errors), "events": [report_written_event(result)]}` where `report_written_event` is `agent_event(agent_name=REPORT_WRITER_NAME, event_type="report_writer.report.written", message=..., metadata={"statements": ..., "citations": ..., "refused": ..., "fact_rows": len(composition.fact_rows), "not_found": len(composition.not_found)})` (check `agent_event`'s keyword names in `agents/events.py` line 18).
- `allowed_tools = ("write_document", "save_to_memory")`, as the synthesizer's.

- [ ] **Step 4: Run the tests.** Run: `"$PY" -m pytest -q tests/test_agents/test_report_writer.py tests/test_agents/test_report_layout.py tests/test_agents/test_verified_facts.py tests/test_agents/test_wording.py`. Expected: PASS. If `ReportComposition`'s validator rewrites the statements this module passes, stop and report it: Task 1.1's contract is that it keeps them.

- [ ] **Step 5: Commit.**

```bash
git add src/deep_research/agents/report_writer.py tests/test_agents/test_report_writer.py
git commit -m "feat(report_writer): the Report Writer cites verified findings by label with the 6.2 guards"
```

**Acceptance:** the named Review Focus tests pass (a forecast stated as fact, and one stated with hardened modality, each rewritten once and kept; a release date kept as a date and an invented one refused; grid-scale on an all-segment figure refused); a failed or truncated draft still publishes the code-built facts; no path cites a URL the findings do not carry.

### Task 3.5: Phase-3 integration: exports and pins

**Role:** sp-implementer. **Wave:** 3C, parallel with Task 3.6. **Depends on:** Task 3.4 merged.

**Owns:** `src/deep_research/agents/__init__.py`.

- [ ] **Step 1: Re-export** every public name of `verified_facts.py`, `report_writer.py` and the four new `report.py` functions from `agents/__init__.py` and `__all__` (`wording.py`'s names were exported by Task 1.5).
- [ ] **Step 2: Confirm no pin moved.** Run the Task 1.5 Step 2 print command: every value must equal its pin, because Phase 3 edits no agent module and not `agents/prompts.py` (Task 3.3's `synthesizer.py` edit was re-pinned in Task 1.5). If one moved, stop and report it.
- [ ] **Step 3: Run the tests.** Run: `"$PY" -m pytest -q tests/test_imports.py tests/test_evaluation/test_config.py tests/test_agents`. Expected: PASS.
- [ ] **Step 4: Commit** `src/deep_research/agents/__init__.py` with "chore(agents): export phase-3 names".

### Task 3.6: Step-3 proof on the audit-2 findings (Gate G3)

**Role:** sp-implementer, in `$W` (rule R1); the `--live` run is an operator step. **Wave:** 3C, parallel with Task 3.5. **Depends on:** Task 3.4 merged.

**Files:** Create (untracked) `scratch/ev_compose_audit2.py`.

**Superseded by D7/D8:** with D8 in place, the harness's wording checks are the scripted Statement Check's: the draft stays, the `ScriptedCompleter` (or the real provider in `--live`) answers `StatementCheckDraft` with one verdict per point, and C3 becomes "line (2) is kept unchanged and its rendered label reads `forecast (<release>)`", C4 "a scripted `inconsistent` verdict refuses line (4) with that reason", and C7 "every kept point carries a `statement_verdicts` entry and `unjudged_sentences` is empty" — `check_point` and `untraced_numbers` no longer run (the D8 contract, `SDD/d8-contract.md`). Apply the contract where this body differs; do not rewrite the body.

**Interfaces:** Consumes `rebuild()`, `QUESTION`, `fixture_plan()` (Task 1.6); `scripted_replies()`, `verify_scripted()` (Task 2.3); `ReportWriterAgent`, `compose_written_report`, `ReportWriterDraft`, `WriterPointDraft` (Task 3.4); `render_written_report`, `render_finding_log` (Task 3.2).

- [ ] **Step 1: Write the harness.** Behaviour:
  - Offline (default): `rebuilt = rebuild()`; `findings = verify_scripted(rebuilt)`, with two extra scripted overrides: every figure from `ent.news` gets `attribution="relayed"`, `organisation="U.S. Energy Information Administration"` (the STEO PDF's "Data source" line; Task 2.1's `source:` cue must admit it), and every figure from `woodmac.com` keeps `own`. Build `state = ResearchState(session_id="ev-proof-audit2", original_question=QUESTION, sub_topics=rebuilt.sub_topics, verified_findings=findings, read_records=rebuilt.reads, evaluated_sources=rebuilt.sources)`, build a `ReportWriterAgent` with a `ScriptedCompleter` whose one queued output is the scripted draft below, and run it.
  - The scripted draft, labels looked up in `task.registry` by host and figure value: (1) "Generators added 10.4 GW of new battery storage capacity in the United States in 2024." citing the eia.gov 10.4 GW finding; (2) "EIA's outlook adds 14 GW in 2025." citing the ent.news 14 GW finding (a forecast stated as fact: must be rewritten and kept); (3) "Wood Mackenzie projects 15 GW of energy storage installations in 2025." citing the woodmac.com Q1-2025 15 GW finding; (4) "Wood Mackenzie reports 18.9 GW of grid-scale storage installed in 2025." citing a woodmac.com 18.9 GW finding (must be refused for scope); (5) "In 2024, 10.4 GW of battery storage was added." citing the eia.gov finding again (a restatement: must be dropped).
  - `--live` (operator, off-peak): the same state, the writer built with the real provider exactly as Task 2.3's `--live` builds the verifier, with `model_profile=settings.llm.resolve_for("report_writer")`: until Task 4.1 adds that `model_overrides` entry, `resolve_for` falls back to the global profile (`reasoning_effort: high`), which is the measured writer effort (F10).
  - Write `scratch/ev-compose-report.md` and `scratch/ev-compose-evidence.md`, print the summary lines with their labels, then the checks:
    - **C1** a summary line states 10.4 GW and its label contains "actual", and its fact row's organisation is EIA (`same_organisation(row.organisation, "EIA")`);
    - **C2** at least two summary lines state 2025 forecasts whose labels start with different organisations and contain "forecast"; each carries a release when its finding does (print any forecast finding without a release field, and fail C2 in `--live` mode if one lacks it);
    - **C3** (offline only) line (2) is kept, rewritten to contain "forecast";
    - **C4** (offline only) line (4) is in `rejected_points` with a reason containing "grid-scale";
    - **C5** no two summary lines state the same fact row, and no two key facts rows share organisation, unit dimension, period, kind and value;
    - **C6** the rendered report matches none of `\b(verified|unverified|corroborat\w*|independently|insufficient evidence|contested|contradicted|not established)\b` (case-insensitive);
    - **C7** `untraced_numbers` is empty for every kept point.
  - Exit 0 only when every applicable check prints `PASS`.

- [ ] **Step 2: Run offline.** Run: `"$PY" scratch/ev_compose_audit2.py`. Expected: `PASS C1`–`PASS C7`, exit 0. A failure caused by the harness's scripted data (a label lookup that finds nothing) is fixed in the harness; a failure in composition, checks or labels goes back to Task 3.1, 3.2, 3.3 or 3.4 (rule R6).

- [ ] **Step 3: Run live (operator, off-peak).** Run the OFF-PEAK CHECK; if `OK`, run `"$PY" scratch/ev_compose_audit2.py --live`. Expected: `PASS` for C1, C2, C5, C6 and C7, one or two `ReportWriterDraft` calls, under 6 minutes. Report the printed summary to the controller.

- [ ] **Step 4: No commit** (scratch).

### Gate G3 — end of step 3

Run in `$W` once Tasks 3.1–3.6 are merged and review-clean (rule R8):

```bash
"$PY" -m pytest -q tests/test_agents/test_verified_facts.py tests/test_agents/test_wording.py tests/test_agents/test_report_layout.py tests/test_agents/test_report_writer.py
"$PY" -m pytest -q tests --ignore=tests/test_state.py
"$PY" -m pytest -q tests/test_state.py
"$PY" scratch/ev_compose_audit2.py
"$PY" scratch/ev_compose_audit2.py --live      # operator, off-peak only
```

**Pass condition:** the tests pass and the full suite is green (the old pipeline still runs, PD-3); the offline harness prints PASS for C1–C7; the live run prints PASS for C1, C2, C5, C6 and C7. That is spec §9 step 3's proof: the summary carries the 2024 actual and at least two 2025 forecasts with organisation and release; no duplicate figure line; no verdict wording. Do not start Phase 4 until G3 passes.

---

## Phase 4 — Cutover: Report Reviewer, gates, extra pass, graph, API and evaluation (spec step 4)

Phase goal: the graph runs planner → researcher → source evaluator → Evidence Verifier → Report Writer → Report Reviewer → (one targeted extra pass) → publish; the fact checker, claim clusters and critic are gone with every caller; the e2e matrices are reworked in the same step, so the safety net never drops (§11). PD-21 governs deletions: parallel tasks stop calling names; Task 4.10 deletes them. The suite is red between Task 4.1 and Gate G4 (PD-3); each task proves itself with its scoped tests.

### Task 4.1: Step-4 contract and mechanical renames

**Role:** sp-hard-implementer. **Wave:** 4A, alone. **Depends on:** Gate G3.

**Owns:** `utils/types.py`, `utils/__init__.py`, `utils/config.py`, `config.yaml`, `tests/test_config.py`, `tests/test_types.py`, `tests/test_state.py`, `tests/evidence_fakes.py`, `agents/report_writer.py`, `agents/synthesizer.py`, `tests/test_agents/test_report_writer.py`, `tests/test_agents/test_synthesizer.py`, the renamed `agents/report_reviewer.py` and `tests/test_agents/test_report_reviewer.py`, and **import lines only** in the files the Step 1 greps list.

**Interfaces:** Produces the step-4 blocks of "Shared interfaces" for `utils/types.py`, the config below, the module `agents/report_reviewer.py` (same contents as `report_review.py`; Task 4.2 rewrites it), `REPORT_REVIEWER_ROLE = "report_reviewer"`, and the three filename helpers in `report_writer.py`. Removes no importable name (PD-21).

- [ ] **Step 1: Inventory.** Run and keep the output for the commit message body:

```bash
grep -rln "deep_research.agents.report_review\b\|agents import report_review\b" src tests
grep -rn "REPORT_JUDGE_ROLE\|\"report_judge\"\|'report_judge'\|report_judge" src tests config.yaml
grep -rn "report_filename\|evidence_report_filename\|quality_report_filename" src tests
grep -rn "max_iterations" src tests | grep -v "agents.max_iterations\|AgentRuntimeConfig\|config.max_iterations\|AGENTS_MAX_ITERATIONS"
```

- [ ] **Step 2: Write the failing tests.** Append to `tests/test_types.py`:

```python
from deep_research.utils.types import REVIEW_RUBRIC_VERSION, ReportReview, ReviewDefect


def test_a_review_carries_review_defects_and_missing_targets() -> None:
    defect = ReviewDefect(defect_id="review-01", kind="coverage", severity="major",
                          target_ids=["topic-02-target-01"], problem="The 2025 forecast is missing.")
    review = ReportReview(status="incomplete", defects=[defect], missing_required_target_ids=["topic-02-target-01"])
    assert review.defects[0].material and REVIEW_RUBRIC_VERSION == 3


def test_extra_passes_default_to_one_and_their_targets_are_replaced() -> None:
    state = ResearchState(session_id="s", original_question="q")
    assert state.max_extra_passes == 1 and state.extra_pass_target_ids == []
    state = merge_research_state(state, {"extra_pass_target_ids": ["a"]})
    state = merge_research_state(state, {"extra_pass_target_ids": ["b"]})
    assert state.extra_pass_target_ids == ["b"]
    assert advance_research_iteration(state).iteration == 1
    with pytest.raises(ValueError):
        advance_research_iteration(advance_research_iteration(state))
```

Append to `tests/test_config.py`:

```python
def test_the_evidence_verifier_pipeline_config() -> None:
    settings = load_settings("config.yaml")
    assert settings.graph.max_extra_passes == 1
    assert settings.agents.tool_budget_overrides["researcher"] == 20
    assert settings.llm.resolve_for("evidence_verifier").reasoning_effort == "high"
    assert settings.llm.resolve_for("report_writer").reasoning_effort == "high"   # F10: the one effort source
    assert settings.llm.resolve_for("report_reviewer").timeout == 360.0
    assert set(settings.agents.tool_budget_overrides) <= set(PRODUCTION_AGENT_NAMES)
    assert PRODUCTION_AGENT_NAMES == ("planner", "researcher", "source_evaluator", "evidence_verifier", "report_writer")
    assert SERVICE_ROLE_NAMES == ("report_reviewer",)
    # Spec 7.3 (D9/PD-27): the four concurrency caps, one assertion each.
    assert settings.agents.sub_topic_concurrency == 5
    assert settings.agents.source_scoring_concurrency == 3
    assert settings.agents.verifier_batch_size == 5
    assert settings.agents.verifier_concurrency == 8
```

(Use the existing imports of `tests/test_config.py`; add `load_settings`, `PRODUCTION_AGENT_NAMES`, `SERVICE_ROLE_NAMES` if absent.)

- [ ] **Step 3: Run to see them fail.** Run: `"$PY" -m pytest -q tests/test_types.py tests/test_config.py -k "review_defects or extra_passes or evidence_verifier_pipeline"`. Expected: FAIL.

- [ ] **Step 4: Types** (`utils/types.py`), exactly as the step-4 block of "Shared interfaces": add `ReviewDefect`; set `StatementReviewDisposition` to `"supported" | "unsupported" | "not_reviewed"`; set `REVIEW_RUBRIC_VERSION = 3`; give `ReportReview` the final field list (defects typed `list[ReviewDefect]`, `missing_required_target_ids` added, the evidence-batch fields `reviewed_evidence_ids`, `omitted_evidence_ids`, `reviewed_batch_ids`, `expected_batch_ids`, `reviewed_target_ids` removed; keep `mean_score` and `material_defects` properties and adapt `validate_scored_review` so a scored review needs the seven dimensions and a disposition for every reviewed statement); add the new `ReportQualitySnapshot` fields (old ones stay until Task 4.10); rename `ResearchState.max_iterations` → `max_extra_passes` (`Field(default=1, ge=0)`) and add `extra_pass_target_ids: list[str]` (replaced on write, not appended) in `ResearchState` and `ResearchStateUpdate`; `advance_research_iteration` refuses `iteration >= max_extra_passes`; rename `ReportComposition.max_iterations` → `max_extra_passes` and add `ReportComposition.statement_verdicts: dict[str, str] = Field(default_factory=dict)` (statement id → `"consistent"` | `"corrected"` | `"unchecked"`; Task 4.3 fills it and gates on it, PD-10); remove `EvidenceTarget.support_policy` and the validators that read it (PD-16; the `SupportPolicy` alias stays until Task 4.10). Re-export `ReviewDefect` from `utils/__init__.py`. In `tests/evidence_fakes.py`, drop `support_policy` from `make_target`'s legacy branch.

- [ ] **Step 5: Config.** In `utils/config.py`: `PRODUCTION_AGENT_NAMES = ("planner", "researcher", "source_evaluator", "evidence_verifier", "report_writer")`; `SERVICE_ROLE_NAMES = ("report_reviewer",)`; in `GraphConfig` replace `max_iterations` with `max_extra_passes: int = Field(default=1, ge=0)` and its env key `GRAPH_MAX_ITERATIONS` with `GRAPH_MAX_EXTRA_PASSES`; delete `claim_batch_size`, `claim_batches_per_pass`, `critic_review_max_tokens`, `claim_verification_max_tokens` and their env keys. `config.yaml`, replacing the matching blocks (keep every comment that still describes something that exists; rewrite the per-agent effort comment to name the new agents):

```yaml
  model_overrides:
    planner:
      reasoning_effort: max
    researcher:
      reasoning_effort: high
    source_evaluator:
      reasoning_effort: high
    # The Context Check and Statement Check (spec 5.2, 5.4): batched, tool-free
    # calls, `agents.verifier_batch_size` items per call (Task 4.1).
    evidence_verifier:
      reasoning_effort: high
    # The writer's first attempt runs at this effort; a truncated draft is asked
    # once more at high (Task 3.4). This entry is the only effort source (F10).
    report_writer:
      reasoning_effort: high
    # The Report Reviewer is a service role, not an agent (was report_judge).
    report_reviewer:
      reasoning_effort: max
      timeout: 360.0
      retry_count: 1
...
  tool_budget_overrides:
    planner: 1
    researcher: 20
    source_evaluator: 0
    evidence_verifier: 0
    report_writer: 0
...
graph:
  max_extra_passes: 1
  checkpointing_enabled: false
```

and delete the `claim_batch_size`, `claim_batches_per_pass`, `critic_review_max_tokens` and `claim_verification_max_tokens` keys with their comments. The `agents:` block also gains the four §7.3 caps (D9), each with a comment naming the spec section:

```yaml
agents:
  # Spec 7.3 (D9/PD-27): the only concurrency bounds. Lower one from live
  # results without a code change; Task 4.14's telemetry names the knob to turn.
  sub_topic_concurrency: 5         # researcher sub-topics in flight
  source_scoring_concurrency: 3    # source-evaluator scoring batches in flight
  verifier_batch_size: 5           # Context and Statement Check items per call
  verifier_concurrency: 8          # verification calls in flight
```

In `AgentRuntimeConfig`, add `sub_topic_concurrency: int = Field(default=5, ge=1)`, `source_scoring_concurrency: int = Field(default=3, ge=1)`, `verifier_batch_size: int = Field(default=5, ge=1)` and `verifier_concurrency: int = Field(default=8, ge=1)`, and the four environment keys following the existing pattern: `"AGENTS_SUB_TOPIC_CONCURRENCY": ("agents", "sub_topic_concurrency")`, `"AGENTS_SOURCE_SCORING_CONCURRENCY": ("agents", "source_scoring_concurrency")`, `"AGENTS_VERIFIER_BATCH_SIZE": ("agents", "verifier_batch_size")` and `"AGENTS_VERIFIER_CONCURRENCY": ("agents", "verifier_concurrency")`. Task 4.13's researcher and source evaluator read the first two, and the Evidence Verifier reads the last two (PD-12, PD-27); each has its assertion in `test_the_evidence_verifier_pipeline_config` (Step 2). In `tests/test_config.py`, delete the claim-batch config tests (lines 770–793 at the plan's base commit: the tests that import `fact_checker.DEFAULT_MAX_CLAIMS` or assert those four keys), because their keys and their import are gone.

Only if Gate G2's live run recorded a failed Context Check batch (`evidence_verifier_context_check_failed`) or a batch over about 60 s, also add `timeout: 240.0` under `evidence_verifier` in `model_overrides` (review item 13, F8), with a comment naming the measurement. Otherwise add nothing.

- [ ] **Step 6: Mechanical renames.** `git mv src/deep_research/agents/report_review.py src/deep_research/agents/report_reviewer.py` and `git mv tests/test_agents/test_report_review.py tests/test_agents/test_report_reviewer.py`; update every import line Step 1 listed. Rename `REPORT_JUDGE_ROLE = "report_judge"` to `REPORT_REVIEWER_ROLE = "report_reviewer"` and every use. Move `report_filename`, `evidence_report_filename`, `quality_report_filename` (synthesizer.py 778–815) with their tests into `report_writer.py` / `tests/test_agents/test_report_writer.py`; `synthesizer.py` imports them from `report_writer`; update the other importers Step 1 listed. In `report_writer.py`, rename `ReportWriterTask.max_iterations` to `max_extra_passes` and read `state.max_extra_passes`.

- [ ] **Step 7: Keep every package importable.** Run the IMPORT SMOKE for `deep_research.agents, deep_research.graph, deep_research.runtime, deep_research.api, deep_research.cli, deep_research.main`. A module that now fails **at import time** (a module-level construction or annotation using a removed field) gets the smallest edit that restores import; runtime failures are left to the Phase-4 task that owns the module. `deep_research.evaluation` and `deep_research.e2e_evaluation` may fail to import until Tasks 4.7 and 4.11; record it in the report.

- [ ] **Step 8: Run the tests.** Run: `"$PY" -m pytest -q tests/test_types.py tests/test_config.py tests/test_agents/test_report_writer.py` and, alone, `"$PY" -m pytest -q tests/test_state.py`. Expected: PASS, except `test_types.py`/`test_state.py` tests that exercise claim or critique behaviour and fail only because of this contract: list their ids in the report (Task 4.10 deletes them with the types they test).

- [ ] **Step 9: Commit** every file this task changed, by path, with "refactor: step-4 contract (review defects, extra passes, config) and mechanical renames".

**Acceptance:** the IMPORT SMOKE passes for the six packages; the config test passes; `report_reviewer.py` exists and nothing imports `report_review`.

### Task 4.2: The Report Reviewer

**Role:** sp-hard-implementer. **Wave:** 4B, parallel with Tasks 4.3–4.7. **Depends on:** Task 4.1 merged.

**Owns:** `src/deep_research/agents/report_reviewer.py`, `tests/test_agents/test_report_reviewer.py`.

**Interfaces:**
- Keeps the names the graph uses: `ReportReviewer` (with `review(packet, *, previous) -> ReportReview` and `review_records`), `build_report_review_input(state, composition) -> ReportReviewInput` (the `terminal` parameter goes), `ReportReviewInput` (with `fingerprint`, `composition_fingerprint`, `expected_statement_ids`, `rubric_version`, `reader_content`), `semantic_review_passes`, `composition_semantic_fingerprint`, `REPORT_REVIEWER_ROLE`.
- The packet: `question`; `answer_contract`; `reader_content` (the rendered report); `statements` (id, text, its code-built reader label, and the labels of the findings each cites); `findings` (label → source title, host, snippet, figure labels); `fact_rows` (the key facts lines); `not_found` (target questions); `deterministic` (the snapshot's `hard_failures`, `unjudged_sentences`, `duplicate_fact_rows`, `unresolved_citations`, `uncited_settled_points`). No claim, verdict, cluster or evidence-batch field.
- The reviewer MAY record a defect for a sentence whose prose contradicts its code-built label (spec §6.3), with that statement's id; the Statement Check (§5.4) is the primary guard for sentence wording, and this defect is the reviewer's own judgement, never a gate.
- The reply schema: `ReportReviewDraft(dimensions: ReviewDimensionScores, statement_dispositions: list[StatementDispositionDraft], defects: list[ReviewDefectDraft], rationale: str)` with `ReviewDefectDraft(kind: str, severity: str, statement_ids: list[str], target_ids: list[str], problem: str)`.

- [ ] **Step 1: Write the failing tests** in `tests/test_agents/test_report_reviewer.py`: keep the file's tests of dimension scoring, `semantic_review_passes` thresholds and packet fingerprinting after porting their fixtures to the new packet; delete every test of claims, verdict badges, critic gap normalisation, evidence batches and batch follow-ups (their behaviour is gone); and add:

```python
async def test_one_call_judges_every_statement(reviewer_with_reply) -> None:
    reviewer, completer = reviewer_with_reply(all_supported=True)
    review = await reviewer.review(packet(), previous=None)
    assert review.status == "scored" and [n for n, _, _ in completer.calls] == ["ReportReviewDraft"]
    assert set(review.per_statement_dispositions.values()) == {"supported"}


async def test_an_unsupported_statement_is_a_material_defect(reviewer_with_reply) -> None:
    reviewer, _ = reviewer_with_reply(unsupported=["S002"])
    review = await reviewer.review(packet(), previous=None)
    assert "S002" in review.derived_defect_statement_ids and not semantic_review_passes(review)


async def test_a_missing_disposition_leaves_the_review_incomplete(reviewer_with_reply) -> None:
    reviewer, _ = reviewer_with_reply(skip=["S003"])
    assert (await reviewer.review(packet(), previous=None)).status == "incomplete"


async def test_a_defect_for_prose_against_its_label_is_kept(reviewer_with_reply) -> None:
    reviewer, _ = reviewer_with_reply(contradicting=["S001"])
    review = await reviewer.review(packet(), previous=None)
    assert [d.statement_ids for d in review.defects if d.material] == [["S001"]]


async def test_a_truncated_reply_is_asked_once_more_then_a_failure_is_recorded(reviewer_truncating) -> None:
    review = await reviewer_truncating.review(packet(), previous=None)
    assert review.status == "provider_failed" and len(reviewer_truncating.provider.calls) == 2


def test_the_packet_holds_statements_findings_and_facts_but_no_claims() -> None:
    built = build_report_review_input(state_with_written_report(), state_with_written_report().composition)
    assert built.expected_statement_ids == ["S001", "S002", "S003"]
    assert "Generators added 10.4 gigawatts" in built.reader_content or built.findings
    assert not hasattr(built, "claims")
```

(`packet()`, `state_with_written_report()` and the reviewer fixtures are built in the file from `tests/evidence_fakes.py` and a composition produced by Task 3.4's `compose_written_report`; the reviewer uses a `ScriptedCompleter` like the file's existing fixtures do.)

- [ ] **Step 2: Run to see them fail.** Run: `"$PY" -m pytest -q tests/test_agents/test_report_reviewer.py`. Expected: FAIL.

- [ ] **Step 3: Implement.** In `report_reviewer.py`: drop the imports from `critic`; replace `CritiqueGapDraft`/`normalize_gaps` with `ReviewDefectDraft` and a local `_defects(drafts, packet) -> list[ReviewDefect]` (ids `review-01`…; an unknown `kind` or `severity` drops that defect with a recorded problem; unknown statement or target ids are removed from the defect, which stays if its problem stays); rebuild `build_report_review_input` from `state.composition` (statements from `composition.statements`, labels from `composition.finding_labels`, findings from `composition.findings`, fact rows from `composition.fact_rows`, Not found from `composition.not_found`, deterministic from `state.quality`); one request per review (`review_messages(packet)`), one re-ask at `OUTPUT_LIMIT_RETRY_EFFORT` after `ProviderOutputLimitError`; `ProviderError` → `provider_failed`; a reply missing a statement's disposition → that statement `not_reviewed` and status `incomplete`; `_derived_defects`: each `unsupported` statement becomes a `major` defect of kind `missing_support` naming it. Rewrite the prompt constants: judge the report against the question and the cited findings' snippets and labels; check that relays read as relays, actuals and forecasts are labelled, forecasts carry issuer and release, and no scope, period or kind is wrong; score the seven existing dimensions; give a disposition for every statement id. Delete the evidence-batch machinery and `review_defects_as_refinement_jobs` only where no doomed module imports them (PD-21; otherwise stop calling them). `composition_semantic_fingerprint` hashes the statements, finding ids, fact rows and Not found.

- [ ] **Step 4: Run the tests.** Run: `"$PY" -m pytest -q tests/test_agents/test_report_reviewer.py`. Expected: PASS.

- [ ] **Step 5: Commit** `src/deep_research/agents/report_reviewer.py` and `tests/test_agents/test_report_reviewer.py` with "feat(report_reviewer): one call merges the critic and the report review (spec 6.3)".

**Acceptance:** one provider call per review (two after a truncation); every statement dispositioned or the review is `incomplete`; no claim or critic dependency.

### Task 4.3: Quality gates

**Role:** sp-hard-implementer. **Wave:** 4B. **Depends on:** Task 4.1 merged.

**Owns:** `src/deep_research/agents/quality.py`, `tests/test_agents/test_quality.py`, plus `src/deep_research/agents/report_writer.py` and `tests/test_agents/test_report_writer.py` for the one change the gate needs: `compose_written_report` records each kept candidate's Statement Check outcome in `ReportComposition.statement_verdicts` (§6.4's "every kept sentence was judged by the Statement Check, or its batch failure is recorded"). Nothing else in those two files changes.

- [ ] **Step 1: Write the failing tests** (new section of `tests/test_agents/test_quality.py`; delete the file's tests of claim coverage, broad-plan coverage, critical targets, duplicate claims, contradicted claims and pursued-unmet accounting). Build states with Task 3.4's `compose_written_report` over `tests/evidence_fakes.py` findings:

```python
def test_a_clean_written_report_has_no_hard_failure() -> None:
    snapshot = compute_report_quality(clean_state(), clean_state().composition)
    assert snapshot.hard_failures == [] and snapshot.missing_required_target_ids == []


def test_each_gate_fires_on_its_own_defect() -> None:
    assert "unjudged_sentences" in compute_report_quality(*with_unjudged_sentence()).hard_failures
    assert "uncited_settled_points" in compute_report_quality(*with_uncited_point()).hard_failures
    assert "unresolved_citations" in compute_report_quality(*with_unknown_finding_id()).hard_failures
    assert "missing_as_of" in compute_report_quality(*with_composition(as_of="")).hard_failures
    assert "missing_scope" in compute_report_quality(*with_composition(scope="")).hard_failures


def test_a_missing_required_target_is_missing_but_accounted_when_listed_not_found() -> None:
    snapshot = compute_report_quality(*missing_forecast_state(listed_not_found=True))
    assert snapshot.missing_required_target_ids == ["topic-02-target-01"]
    assert "unaccounted_required_targets" not in snapshot.hard_failures
    unlisted = compute_report_quality(*missing_forecast_state(listed_not_found=False))
    assert "unaccounted_required_targets" in unlisted.hard_failures
```

Also add (the clean state's composition must hold at least one forecast row with a release; build `clean_state()` so it does):

```python
def test_a_forecast_row_without_a_release_is_counted_not_failed() -> None:
    state, composition = with_composition()
    rows = list(composition.fact_rows)
    first = next(n for n, row in enumerate(rows) if row.kind == "forecast")
    rows[first] = rows[first].model_copy(update={"release": None})
    snapshot = compute_report_quality(*with_composition(fact_rows=rows))
    assert snapshot.forecasts_without_release == 1 and snapshot.hard_failures == []
```

No test builds a duplicate fact row: `fact_rows()` merges same-fact rows, so the `duplicate_fact_rows` gate only guards hand-built compositions and future producers (PD-10, F11). `with_unjudged_sentence()` builds a composition whose kept statement has no `statement_verdicts` entry; `with_unjudged_sentence(recorded_failure=True)` gives it the entry `"unchecked"` and a `statement_check_failed` error in `composition.errors`, which is §6.4's recorded-batch-failure case:

```python
def test_a_recorded_batch_failure_keeps_the_sentence_but_is_not_a_gate_failure() -> None:
    state, composition = with_unjudged_sentence(recorded_failure=True)
    snapshot = compute_report_quality(state, composition)
    assert snapshot.unjudged_sentences == [] and snapshot.hard_failures == []
```

- [ ] **Step 2: Run to see them fail.** Run: `"$PY" -m pytest -q tests/test_agents/test_quality.py`. Expected: FAIL.

- [ ] **Step 3: Implement** `compute_report_quality(state, composition)` (drop `terminal`, and the `untraced_numbers` import with it):

```python
def compute_report_quality(state: ResearchState, composition: ReportComposition) -> ReportQualitySnapshot:
    """§6.4 and PD-10: deterministic gates over the written report and its findings."""
    targets = [t for topic in state.sub_topics for t in topic.evidence_targets]
    required = [t.target_id for t in targets if t.required]
    answered = answered_target_ids(state.verified_findings, targets)
    missing = [t for t in required if t not in answered]
    listed = {row.target_id for row in composition.not_found}
    unaccounted = [t for t in missing if t not in listed]
    by_id = {finding_fingerprint(f): f for f in composition.findings}
    points = [*composition.summary, *(p for s in composition.sections for p in s.points)]
    uncited = sum(1 for p in points if p.statement is None or not p.statement.finding_ids)
    unresolved = sum(1 for p in points if p.statement is not None and (
        any(i not in by_id for i in p.statement.finding_ids) or not p.source_urls))
    # §6.4, D8: every kept sentence was judged by the Statement Check, or its
    # batch failure is recorded. A kept sentence with neither is the gate.
    failure_recorded = any(
        error.error_type in {"evidence_verifier_statement_check_failed", "report_writer_statement_check_failed"}
        for error in composition.errors
    )
    judged = {"consistent", "corrected"}
    unjudged = [
        p.statement.statement_id for p in points
        if p.statement is not None
        and (verdict := composition.statement_verdicts.get(p.statement.statement_id)) not in judged
        and not (verdict == "unchecked" and failure_recorded)
    ]
    rows = composition.fact_rows
    # Invariant (F11): fact_rows() already merges same-fact rows, so this guards
    # hand-built compositions and future producers; no test fixture is spent on it.
    duplicates = sum(1 for n, a in enumerate(rows) for b in rows[n + 1:]
                     if a.kind == b.kind and a.value == b.value and same_period(a.period, b.period)
                     and same_organisation(a.organisation, b.organisation))
    statuses = [f.verification for f in state.verified_findings if f.verification is not None]
    failures = [name for name, failed in (
        ("unresolved_citations", unresolved > 0), ("uncited_settled_points", uncited > 0),
        ("duplicate_fact_rows", duplicates > 0), ("missing_as_of", not composition.as_of),
        ("missing_scope", not composition.scope), ("unjudged_sentences", bool(unjudged)),
        ("unaccounted_required_targets", bool(unaccounted)),
        ("missing_reader_report", not state.report), ("missing_evidence_ledger", not state.report_evidence),
    ) if failed]
    return ReportQualitySnapshot(
        required_target_ids=required, answered_target_ids=sorted(answered),
        missing_required_target_ids=missing, unaccounted_target_ids=unaccounted,
        verified_findings=sum(1 for v in statuses if v.status == "verified"),
        corrected_findings=sum(1 for v in statuses if v.status == "verified_corrected"),
        dropped_findings=sum(1 for v in statuses if v.status == "dropped"),
        context_unchecked_findings=sum(1 for v in statuses if v.context_unchecked),
        dropped_figures=sum(1 for v in statuses for r in v.figure_results if not r.kept),
        cited_findings=len({i for p in points if p.statement for i in p.statement.finding_ids}),
        cited_sources=len({u for p in points for u in p.source_urls}),
        duplicate_fact_rows=duplicates, uncited_settled_points=uncited,
        unresolved_citations=unresolved, unjudged_sentences=unjudged,
        refused_sentences=len(composition.rejected_points), hard_failures=failures,
        forecasts_without_release=sum(1 for row in rows if row.kind == "forecast" and not row.release),
    )
```

Keep `review_status_fields`. Leave the claim-era functions in place when anything outside `quality.py` imports them (PD-21); otherwise delete them.

- [ ] **Step 4: Run the tests.** Run: `"$PY" -m pytest -q tests/test_agents/test_quality.py`. Expected: PASS.
- [ ] **Step 5: Commit** both owned files with "feat(quality): the evidence-verifier gate set (spec 6.4, PD-10)".

**Acceptance:** exactly the PD-10 gates fire, each on its own defect; a kept sentence with no Statement Check verdict and no recorded batch failure fails `unjudged_sentences`, while a recorded batch failure does not; a missing required target is reported, and fails a gate only when Not found does not list it; `compose_written_report` fills `statement_verdicts` for every kept point.

### Task 4.4: Researcher and planner: the targeted extra pass; support policy out

**Role:** sp-hard-implementer. **Wave:** 4B. **Depends on:** Task 4.1 merged.

**Owns:** `agents/researcher.py`, `agents/planner.py`, `tests/test_agents/test_researcher.py`, `tests/test_agents/test_planner.py`, `tests/test_agents/test_planner_researcher_seam.py`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_agents/test_researcher.py`; delete the critic-driven tests `test_selection_puts_critic_flagged_gaps_first`, `test_refinement_selection_uses_one_slot_for_unsatisfied_topic`, `test_a_presentation_gap_does_not_send_an_answered_topic_to_acquisition`, `test_an_acquisition_gap_sends_an_answered_topic_back_to_research`, `test_refinement_gap_target_is_selected_even_when_prior_findings_exist`, `test_session_guidance_reports_every_gap_problem` and any other test that sets `state.critique` or `state.refinement_targets`):

```python
def _planned_state(**updates):
    topics = [SubTopic(coverage_id=f"topic-0{n}", title=f"T{n}", rationale="r", search_queries=[f"q{n}"],
                       success_criteria=["c"], priority=n,
                       evidence_targets=[make_target(f"topic-0{n}-target-01")]) for n in (1, 2, 3)]
    return ResearchState(session_id="s", original_question="q", sub_topics=topics).model_copy(update=updates)


def test_the_first_pass_runs_every_planned_sub_topic_in_priority_order() -> None:
    assert [t.coverage_id for t in select_sub_topics(_planned_state())] == ["topic-01", "topic-02", "topic-03"]


def test_an_extra_pass_runs_only_the_sub_topics_that_own_missing_targets() -> None:
    state = _planned_state(extra_pass_target_ids=["topic-02-target-01"])
    assert [t.coverage_id for t in select_sub_topics(state)] == ["topic-02"]
```

and in `tests/test_agents/test_planner.py` a test that a plan draft carrying `support_policy` still validates (the key is ignored, as `EvidenceTargetDraft` no longer has it) only if the draft model allows extra keys; otherwise delete the support-policy tests and assert `"support_policy" not in PLAN_INSTRUCTION`.

- [ ] **Step 2: Run to see them fail.** Run: `"$PY" -m pytest -q tests/test_agents/test_researcher.py -k "first_pass or extra_pass"`. Expected: FAIL.

- [ ] **Step 3: Researcher.** Replace `_ordered_sub_topics`/`select_sub_topics` (lines 206–343) with:

```python
def select_sub_topics(state: ResearchState, max_sub_topics: int = DEFAULT_MAX_SUB_TOPICS) -> list[SubTopic]:
    """The first pass researches every planned sub-topic; an extra pass only the
    sub-topics that own a missing required target (spec §6.5, §7.2)."""
    ordered = sorted(state.sub_topics, key=lambda topic: topic.priority)
    wanted = set(state.extra_pass_target_ids)
    if wanted:
        ordered = [t for t in ordered if any(target.target_id in wanted for target in t.evidence_targets)]
    return ordered[:max_sub_topics]
```

On an extra pass, `_planned_targets` returns only the targets in `state.extra_pass_target_ids`, so the extraction contract lists only the missing targets and their structured fields. Delete `_critic_gaps_by_target`, `_is_critic_gap_target`, `_cited_read_incidence`, `_refinement_satisfied_sub_topics`, `_critic_queries_for`, the critique paragraphs of `render_sub_topic_guidance`, and the `CritiqueGap` import; a sub-topic's queries are its planned `search_queries`.

- [ ] **Step 4: Planner.** Remove the `claim_clusters` import and the three blocks that use it (the support-policy eligibility test around lines 1323–1343, `_unanswerable_requirements` around 2283–2291, the metadata-dimension filter in `apply_answer_contract` around 2915–2922); remove `EvidenceTargetDraft.support_policy`, its prompt paragraph in `PLAN_INSTRUCTION` and the `"support_policy"` keys in `_PLAN_REPLY_EXAMPLES`; stop calling `support_policy_for_target`, `earned_support_policy`, `extend_plan` and `targets_requiring_replanning` (the agent's `extend_plan` method and `_recorded_omission_extension` go; the module-level names stay for Task 4.10 if exported, PD-21). Delete the tests of those paths.

- [ ] **Step 5: Run the tests.** Run: `"$PY" -m pytest -q tests/test_agents/test_researcher.py tests/test_agents/test_planner.py tests/test_agents/test_planner_researcher_seam.py`. Expected: PASS.
- [ ] **Step 6: Commit** the five owned files with "feat(researcher): targeted extra pass; planner drops support policy and plan extension".

**Acceptance:** an extra pass runs only the sub-topics owning missing targets and shows only those targets; no critic, claim-cluster or support-policy reference remains in either module's live code.

### Task 4.5: The quality record

**Role:** sp-implementer. **Wave:** 4B. **Depends on:** Task 4.1 merged.

**Owns:** `src/deep_research/agents/report.py`, `tests/test_agents/test_report.py`.

- [ ] **Step 1: Write the failing test** (append to `tests/test_agents/test_report.py`):

```python
def test_the_quality_record_carries_the_verified_findings_and_refusals() -> None:
    state = written_state()      # a ResearchState whose composition comes from compose_written_report
    record = json.loads(render_quality_json(state, state.composition, None, quality_status="partial"))
    assert {"quality", "review", "findings", "fact_rows", "not_found", "statements", "refused_sentences"} <= set(record)
    assert record["refused_sentences"][0]["text"] and record["refused_sentences"][0]["finding_labels"]
    assert all("verification" in f for f in record["findings"])
    assert "claims" not in record and "claim_clusters" not in record
```

- [ ] **Step 2: Run to see it fail.** Run: `"$PY" -m pytest -q tests/test_agents/test_report.py -k quality_record_carries`. Expected: FAIL.
- [ ] **Step 3: Implement.** Rewrite `render_quality_record` so the record holds: `question`, `session_id`, `iteration`, `generated_on`, `as_of`, `scope`, `quality_status`, `session_status`, `artifacts`, `quality` (the snapshot dump), `review` (status, mean score, dimensions, defects, dispositions, `missing_required_target_ids`, or `None`), `findings` (id, label, source URL, snippet, `verification` dump), `fact_rows`, `not_found`, `statements` (id, text, finding ids, target ids), `refused_sentences` (where, full text, finding labels, reason) and `configuration` as today; bump the quality contract version constant `render_quality_record` stamps. Leave the claim-era renderers in place (PD-21; Task 4.10 deletes them).
- [ ] **Step 4: Run the tests.** Run: `"$PY" -m pytest -q tests/test_agents/test_report.py tests/test_agents/test_report_layout.py`. Expected: PASS apart from claim-renderer tests that fail only because Task 4.1 changed a type; list their ids (Task 4.10 deletes them).
- [ ] **Step 5: Commit** both owned files with "feat(report): the quality record carries verified findings and refused sentences (spec 6.2)".

### Task 4.6: Outcome, API, CLI and README

**Role:** sp-hard-implementer. **Wave:** 4B. **Depends on:** Task 4.1 merged.

**Owns:** `runtime/outcome.py`, `api/models.py`, `api/sessions.py`, `api/app.py`, `api/events.py`, `cli.py`, `README.md`, `tests/test_api/*`, `tests/test_cli/*`, `tests/test_runtime/test_outcome.py`.

- [ ] **Step 1: Write the failing tests.** In `tests/test_runtime/test_outcome.py`, `tests/test_cli/test_render.py`, `tests/test_api/test_sessions.py` and `tests/test_cli/test_arguments.py`, rework the fixtures to a state with `verified_findings` and a new-style snapshot, and add:

```python
def test_coverage_and_evidence_counts_come_from_verified_findings() -> None:
    outcome = outcome_for(verified_state())
    assert (outcome.coverage.required_targets, outcome.coverage.answered_targets) == (3, 2)
    assert outcome.coverage.missing_required_target_ids == ("topic-02-target-01",)
    assert (outcome.evidence_counts.verified_findings, outcome.evidence_counts.dropped_findings) == (2, 1)


def test_the_summary_prints_findings_review_and_integrity_lines() -> None:
    lines = render_summary(outcome_for(verified_state()))
    assert any(l.startswith("Findings: 2 checked") for l in lines)
    assert any(l.startswith("Integrity: 0 duplicate fact rows; 0 uncited statements; 0 untraced figures; 0 forecasts without release") for l in lines)
    assert not any("critic" in l.casefold() or "claims:" in l.casefold() for l in lines)


def test_max_iterations_sets_the_extra_passes_and_accepts_zero() -> None:
    assert parse_arguments(["q", "--max-iterations", "0"]).max_iterations == 0
```

(`outcome_for`, `verified_state`, `render_summary` and `parse_arguments` are the names these test files already use or build; match them.)

- [ ] **Step 2: Run to see them fail.** Run: `"$PY" -m pytest -q tests/test_runtime/test_outcome.py tests/test_cli tests/test_api`. Expected: FAIL.
- [ ] **Step 3: Implement.**
  - `runtime/outcome.py`: `CoverageProgress(required_targets, answered_targets, missing_required_target_ids: tuple[str, ...], not_found_target_ids: tuple[str, ...])` read from `state.quality` and `state.composition.not_found`; `EvidenceCounts` keeps `read_records`, `network_reads`, `cache_reads`, `unique_works`, `publishers`, `source_urls`, `findings`, `assessed_sources`, `cited_assessed_sources` and replaces the claim counts with `verified_findings`, `corrected_findings`, `dropped_findings`, `context_unchecked_findings`, `cited_findings` (from `state.quality`).
  - `api/models.py` and `api/sessions.py`: `CoverageProgressResponse` and `EvidenceCountsResponse` mirror the new dataclasses; `ResearchRequest.max_iterations` keeps its name (PD-15), allows 0, and is passed as `max_extra_passes=`.
  - `cli.py`: `--max-iterations` becomes a non-negative int with help "extra research passes for missing required targets (default: graph.max_extra_passes, 1)" and is passed as `run_research(..., max_extra_passes=...)`; `STATUS_NOTES["max_iterations"]` = "extra passes exhausted with required targets still missing, and the report not accepted" (PD-23; an accepted report with targets under Not found finishes `completed`); delete `_claim_lines` and the critic score in `_verdict_lines` (print the review status and mean instead); `_evidence_lines` prints `Findings: {checked} checked ({corrected} with corrected context, {unchecked} unchecked context), {dropped} dropped; {cited} cited` and `Integrity: {duplicate_fact_rows} duplicate fact rows; {uncited_settled_points} uncited statements; {len(untraced_figures)} untraced figures; {forecasts_without_release} forecasts without release` (PD-24); `_coverage_line` prints `Required targets: {answered}/{required} answered` plus the not-found ids; `_unresolved_lines` lists the review's material defects and the missing targets; `PROGRESS_EVENT_TYPES` / `is_streamed_event` name the new nodes and the `evidence_verifier.verification.completed` and `report_writer.report.written` events.
  - `README.md`: rewrite "Source Evaluator And Fact Checker" as "Source Evaluator And Evidence Verifier" (Figure Match, Context Check, labels, drop reasons), "Synthesizer And Critic" as "Report Writer And Report Reviewer", "LangGraph Orchestration" (the node list, the routes and statuses of Task 4.8 with PD-23's meaning of `completed` and `max_iterations`, `graph.max_extra_passes`), the CLI flags and summary lines, "Quality Semantics" (the PD-10 gates and acceptance), "Individual Agent Evaluation" (the new agent list) and "Whole-Report Quality Evaluation" (the case ids of Task 4.9). Delete the "Graph-historical harness" subsection and every `--mode graph-historical` example (PD-14, retired).
  - `README.md`, a new short "Upgrading" subsection (F15; no code): the env key `GRAPH_MAX_ITERATIONS` is now `GRAPH_MAX_EXTRA_PASSES`, and an old export is ignored without a warning; `graph.max_iterations` is now `graph.max_extra_passes`; a custom config must drop `agents.claim_batch_size`, `agents.claim_batches_per_pass`, `agents.critic_review_max_tokens`, `agents.claim_verification_max_tokens`, the `fact_checker`, `synthesizer`, `critic` and `report_judge` entries of `llm.model_overrides` (the reviewer's entry is now `report_reviewer`) and any `agents.tool_budget_overrides` entry for a removed agent (that one fails validation with a message naming the key; state in the README which of the others fail and which are ignored, after checking `utils/config.py`); checkpoints written before the cutover cannot be resumed (`ResearchState` rejects their fields).
- [ ] **Step 4: Run the tests.** Run: `"$PY" -m pytest -q tests/test_runtime/test_outcome.py tests/test_cli tests/test_api`. Expected: PASS. `tests/test_cli/test_report_quality_acceptance.py` keeps its structural pathology checks, ported to the writer and reviewer; its critic- and fact-checker-only tests are deleted.
- [ ] **Step 5: Commit** every owned file changed with "feat(cli,api): verified-finding coverage and counts; extra passes; README".

**Acceptance:** a user's `python -m deep_research "<question>" --max-iterations 1` still parses; no summary line mentions the critic or claims; the API returns the new counts.

### Task 4.7: Per-agent evaluation

**Role:** sp-hard-implementer. **Wave:** 4B. **Depends on:** Task 4.1 merged.

**Owns:** everything under `src/deep_research/evaluation/` and `tests/test_evaluation/`.

- [ ] **Step 1: Inventory.** `grep -rn "fact_checker\|critic\|synthesizer\|Claim\b\|Critique\|verified_claims\|support_policy\|report_judge" src/deep_research/evaluation tests/test_evaluation` — every hit is migrated or deleted by this task.
- [ ] **Step 2: Write the failing tests.** `tests/test_evaluation/test_cases_evidence_verifier.py` (new): the three controlled cases and the live case below exist with `agent_name="evidence_verifier"`; each controlled case's reference fixture drives `EvidenceVerifierAgent` (with a `ScriptedCompleter` replying the case's Context Check) to its expected statuses; the gates `verification_recorded`, `no_invented_evidence`, `drop_reasons_named` pass on the reference output and fail on a mutated one (a finding without verification; a kept figure whose evidence words are not on its read; a dropped figure without a reason). `git mv tests/test_evaluation/test_cases_synthesizer.py tests/test_evaluation/test_cases_report_writer.py` and port it to the writer. Update `test_cases_registry.py` (case counts and names), `test_evaluators_agents.py` (delete the fact-checker and critic gate tests; add the three new gates), `test_dependencies_controlled.py`, `test_judging.py` (its `critic_live_case` fixture becomes `evidence_verifier_live_case`), `conftest.py` (delete `FactCheckerOutput` and `CriticOutput`; add `EvidenceVerifierOutput`), and `test_config.py` (the `AGENT_NAMES` pins and the fingerprint dict's keys: planner, researcher, source_evaluator, evidence_verifier, report_writer; compute the values on this branch with the Task 1.5 command; Task 4.10 re-pins them at the end). Delete `test_cases_fact_checker.py` and `test_cases_critic.py`.
- [ ] **Step 3: Implement.** `models.py`: `AgentName` and `AGENT_NAMES` = planner, researcher, source_evaluator, evidence_verifier, report_writer (and the kebab-case CLI names). `dependencies.py`: `_AGENT_CLASSES` maps `evidence_verifier` → `EvidenceVerifierAgent` and `report_writer` → `ReportWriterAgent`; delete the fact-checker and critic scenarios; add `_evidence_verifier_scenarios()` (tool-free: no service scripted) and rename the synthesizer scenarios to `report_writer`. `cases/__init__.py`: `_MODULES` as the agent list; delete the `claim()` builder. `git rm cases/fact_checker.py cases/critic.py`; `git mv cases/synthesizer.py cases/report_writer.py` and rebuild its cases on verified findings (states with `verified_findings`, compositions from `compose_written_report`). New `cases/evidence_verifier.py`, with exactly the case count `cases/__init__.py` validates per agent: `scope-corrected-to-all-segments` (a page stating "18.9 GW ... across all segments", the finding's scope "grid-scale", expected `verified_corrected` with scope "all segments"), `relay-labelled-as-relay` (a relay page "according to Wood Mackenzie, 16 GW", expected `relayed` / "Wood Mackenzie"), `invented-evidence-words-rejected` (expected `dropped` / `evidence_not_on_page`), and the live case `evidence-verifier-live-benchmark` (the benchmark's EIA and Wood Mackenzie pages, rubric: correct scope, period, kind and attribution). `evaluators.py`: delete the fact-checker and critic gate tuples and functions and the planner's support-policy gate; add the three Evidence Verifier gates; rename the synthesizer gates to `report_writer` (`valid_report`, `citations_known_only` over the cited findings' URLs, `refusals_logged`, `no_persistence_calls`, `no_false_publication_claim`). Update `targets.py`, `runner.py`, `judging.py`, `reporting.py`, `datasets.py`, `failure_taxonomy.py` and `cli.py` wherever the inventory showed an agent-specific branch.
- [ ] **Step 4: Run the tests.** Run: `"$PY" -m pytest -q tests/test_evaluation` and `"$PY" -c "import deep_research.evaluation"`. Expected: PASS, except the two fingerprint-pin tests if a sibling task later changes a pinned module (Task 4.10 re-pins).
- [ ] **Step 5: Commit** every owned file changed, by path, with "feat(evaluation): evidence_verifier and report_writer cases; fact_checker and critic removed".

**Acceptance:** `AGENT_NAMES` has the five agents; `python -m deep_research.evaluation` lists evidence_verifier and report_writer cases and no fact_checker or critic case.

### Task 4.8: Graph and runtime cutover

**Role:** sp-hard-implementer. **Wave:** 4C, parallel with Task 4.9. **Depends on:** Tasks 4.2, 4.3 and 4.4 merged.

**Owns:** `graph/*.py` (with `graph/__init__.py`), `runtime/assembly.py`, `runtime/__init__.py`, `runtime/recall.py`, `runtime/memory_bridge.py`, `runtime/errors.py`, `main.py`, `tests/graph_fakes.py`, `tests/research_fakes.py`, `tests/test_graph/*`, `tests/test_runtime/*` except `test_outcome.py`.

**Interfaces:** the "Graph, runtime and entry points" block of "Shared interfaces"; `EvidenceVerifierAgent` (Task 2.1); `ReportWriterAgent`, `finding_memory_payload` (Task 3.4); `render_written_report`, `render_finding_log` (Task 3.2); `render_quality_json` (Task 4.5); `ReportReviewer`, `build_report_review_input` (Task 4.2); `compute_report_quality` (Task 4.3); `select_sub_topics` reading `extra_pass_target_ids` (Task 4.4).

- [ ] **Step 1: Write the failing tests.** In `tests/test_graph/test_state.py`, replace the critique routing tests with:

```python
def _routed(**fields):
    review = fields.pop("review", ReportReview(status="scored", dimensions={d: 0.9 for d in REVIEW_DIMENSIONS}))
    quality = fields.pop("quality", ReportQualitySnapshot())
    return graph_route(ResearchState(session_id="s", original_question="q", report_review=review, quality=quality, **fields))


def test_missing_targets_buy_one_extra_pass_then_publish() -> None:
    missing = ReportReview(status="scored", missing_required_target_ids=["t2"], dimensions={d: 0.9 for d in REVIEW_DIMENSIONS})
    assert _routed(review=missing) == ("extra_pass", "extra_pass_requested")
    # PD-23: passes spent, gates clear, reviewer accepts -> completed, the target under Not found
    assert _routed(review=missing, iteration=1) == ("finalize", "report_accepted")
    assert _routed(review=missing, max_extra_passes=0) == ("finalize", "report_accepted")
    rejected = missing.model_copy(update={"dimensions": {d: 0.5 for d in REVIEW_DIMENSIONS}})
    assert _routed(review=rejected, iteration=1) == ("finalize", "extra_passes_exhausted")
    unlisted = ReportQualitySnapshot(hard_failures=["unaccounted_required_targets"])
    assert _routed(review=missing, iteration=1, quality=unlisted) == ("finalize", "extra_passes_exhausted")


def test_the_final_routes_and_their_statuses() -> None:
    assert _routed() == ("finalize", "report_accepted")
    assert _routed(quality=ReportQualitySnapshot(hard_failures=["unjudged_sentences"])) == ("finalize", "report_not_accepted")
    assert _routed(review=ReportReview(status="provider_failed")) == ("finalize", "review_unavailable")
    assert graph_status(ResearchState(session_id="s", original_question="q", report_review=ReportReview(status="provider_failed"),
                                      quality=ReportQualitySnapshot())) == "incomplete"
    spent = ReportReview(status="scored", missing_required_target_ids=["t2"], dimensions={d: 0.9 for d in REVIEW_DIMENSIONS})
    assert graph_status(ResearchState(session_id="s", original_question="q", iteration=1, report_review=spent,
                                      quality=ReportQualitySnapshot())) == "completed"
    failing = spent.model_copy(update={"dimensions": {d: 0.5 for d in REVIEW_DIMENSIONS}})
    assert graph_status(ResearchState(session_id="s", original_question="q", iteration=1, report_review=failing,
                                      quality=ReportQualitySnapshot())) == "max_iterations"
```

and in `tests/test_graph/test_orchestrator.py`, with agents from `tests/graph_fakes.py` reworked to the new set (a scripted researcher, a verifier fake whose Context Check call raises `ProviderError`, the real `ReportWriterAgent` with a `ScriptedCompleter`, a `FakeReviewer`, a `FakePublisher`):

```python
async def test_run_publishes_when_the_context_check_fails(...) -> None:
    # the verifier's only batch raises ProviderError -> its findings are cited with
    # "unchecked context"; the run ends status completed or incomplete, never failed;
    # the publisher received the report, the evidence log and the quality JSON, and
    # the report text contains "unchecked context".


async def test_extra_pass_that_finds_nothing_publishes_with_not_found(...) -> None:
    # a required target no finding answers; the reviewer node stamps it missing;
    # the graph runs one extra pass (the researcher is called with
    # extra_pass_target_ids == [that target]); the second review still names it;
    # the FakeReviewer accepts (0.9 on every dimension, no defect) and no gate fails
    # (Not found lists the target), so the run finalizes once with status completed
    # and quality accepted (PD-23); the report's "## Not found" lists the target,
    # and the researcher was called exactly twice.
```

Write both bodies in full with the reworked fakes. `tests/test_graph/test_nodes.py`: delete the synthesizer, critic and refine node tests; add tests that the writer node stamps `compute_report_quality`'s snapshot, that the reviewer node stamps `missing_required_target_ids` from the snapshot whatever the review status, and that the extra-pass node sets `extra_pass_target_ids` and advances the iteration. `tests/test_runtime/test_assembly.py`: the agents built are the five new names, and `build_report_reviewer` resolves the `report_reviewer` role.
- [ ] **Step 2: Run to see them fail.** Run: `"$PY" -m pytest -q tests/test_graph tests/test_runtime --ignore=tests/test_runtime/test_outcome.py`. Expected: FAIL.
- [ ] **Step 3: Implement `graph/state.py`.** Node constants and `NODE_NAMES` per "Shared interfaces"; delete `FACT_CHECKER_NODE`, `SYNTHESIZER_NODE`, `CRITIC_NODE`, `REFINE_NODE`, `ROUTE_REFINE`; then:

```python
GRAPH_ROUTES = {
    "report_accepted": "The Report Reviewer accepted the report and no gate failed; any required target with no verified finding is listed under Not found.",
    "report_not_accepted": "The report was scored but not accepted (a gate failed, a material defect, or a mean below 0.80), and no required target is missing.",
    "review_unavailable": "The Report Reviewer did not score the report (provider failure or an invalid reply); it is published as partial.",
    "extra_pass_requested": "Required targets have no verified finding and an extra pass remains; the researcher runs for those targets only.",
    "extra_passes_exhausted": "Required targets still have no verified finding, no extra pass remains, and the report was not accepted; the targets are listed under Not found.",
    "halted": "The run stopped on a non-recoverable error.",
}
GRAPH_STATUSES = ("completed", "max_iterations", "incomplete", "failed")
_STATUS_BY_ROUTE_REASON = {
    "report_accepted": "completed",
    "report_not_accepted": "incomplete",
    "review_unavailable": "incomplete",
    "extra_pass_requested": "incomplete",
    "extra_passes_exhausted": "max_iterations",
    "halted": "failed",
}


def graph_route(state: ResearchState) -> tuple[str, str]:
    """Where the graph goes after the Report Reviewer, and why (spec §6.3-6.5, PD-23)."""
    if is_halted(state):
        return ROUTE_END, "halted"
    review = state.report_review
    missing = review is not None and bool(review.missing_required_target_ids)
    if missing and state.iteration < state.max_extra_passes:
        return ROUTE_EXTRA_PASS, "extra_pass_requested"
    if review is None or review.status != "scored":
        return ROUTE_FINALIZE, "review_unavailable"
    if state.quality is not None and not state.quality.hard_failures and semantic_review_passes(review):
        return ROUTE_FINALIZE, "report_accepted"
    if missing:
        return ROUTE_FINALIZE, "extra_passes_exhausted"
    return ROUTE_FINALIZE, "report_not_accepted"


def graph_quality_status(state: ResearchState) -> str:
    return QUALITY_STATUS_ACCEPTED if graph_route(state)[1] == "report_accepted" else QUALITY_STATUS_PARTIAL
```

`initial_graph_state` and `graph_recursion_limit` take `max_extra_passes`. Delete `_acceptance_satisfied`, `_wants_another_pass`, `repair_is_terminal`, the repair-progress and repair-stop machinery (`_material_gap_ids`, `open_material_gap_ids`, `pending_repair_work`, `_selectable_acquisition_keys`, `_unattempted_repair_keys`, `progress_snapshot`, `repair_capacity_spent`, `_leads_exhausted`, `evidence_exhausted`, `provider_failed`, `repair_stop_reason`) and everything only they use.
- [ ] **Step 4: Implement `graph/nodes.py`.** `report_writer_node(agent)`: run the writer through `agent_node`'s path, then `quality = compute_report_quality(merged, merged.composition)` and stamp it, emitting the quality event the synthesizer node emitted. `report_reviewer_node(reviewer)`: `report_review_node`'s body, with the packet from `build_report_review_input(state, state.composition)`, the reviewed record then stamped `review.model_copy(update={"missing_required_target_ids": list(state.quality.missing_required_target_ids) if state.quality else []})` whatever its status (PD-5), and one `route_decided_event` per decision. `extra_pass_node`: `advance_research_iteration` and set `extra_pass_target_ids = review.missing_required_target_ids`. `route_after_review` returns `graph_route(...)[0]`. `finalize_report_node`: render `render_written_report`, `render_finding_log` and `render_quality_json` from the final composition, publish the three files with `publish_document`, and, only when `graph_quality_status` is accepted, save each cited finding with `publish_finding(*finding_memory_payload(finding, session_id=...))`. `ReportPublisher` gets `publish_finding` in place of `publish_claim`. Delete `synthesizer_node`, `critic_node`, `refine_node`, `route_after_critic`, `route_after_refine`, `route_refinement`, `refinement_targets_for`, `_merged_jobs`, `invalidation_update`, `_clusters_of_statements`, `_claim_is_invalidated`.
- [ ] **Step 5: Implement `graph/orchestrator.py`, runtime and `main.py`.** `ResearchAgents(planner, researcher, source_evaluator, evidence_verifier, report_writer)`; `AGENT_NODE_ORDER` = those five names; edges: START → planner → researcher → source_evaluator → evidence_verifier → report_writer → report_reviewer; conditional edges from report_reviewer: `extra_pass` → extra_pass → researcher, `finalize` → finalize_report → END, `end` → END; the terminal publisher is the report writer. `_session_outputs` drops the critic score. `runtime/assembly.py`: `AGENT_NAMES` and constructors for the five agents (the verifier and the writer built like the synthesizer; `allowed_tools` from each class), `build_report_reviewer` with `REPORT_REVIEWER_ROLE`. `main.run_research(..., max_extra_passes=None)` defaults to `settings.graph.max_extra_passes`. `graph/__init__.py` and `runtime/__init__.py` export the new names and drop the deleted ones.
- [ ] **Step 6: Run the tests.** Run: `"$PY" -m pytest -q tests/test_graph tests/test_runtime --ignore=tests/test_runtime/test_outcome.py` and the IMPORT SMOKE for `deep_research.graph, deep_research.runtime, deep_research.main`. Expected: PASS.
- [ ] **Step 7: Commit** every owned file changed with "feat(graph): evidence verifier, report writer and report reviewer graph with one targeted extra pass".

**Acceptance:** Review Focus items 2 and 3 pass as named; `graph_route` has exactly the six reasons above; nothing in `graph/` or `runtime/` names the fact checker, critic, claims or refinement.

### Task 4.9: E2E replay doubles and cases

**Role:** sp-hard-implementer. **Wave:** 4C, parallel with Task 4.8. **Depends on:** Task 4.2 merged.

**Owns:** `src/deep_research/e2e_evaluation/replay.py`, `src/deep_research/e2e_evaluation/replay_matrix.py`, `tests/test_e2e_evaluation/test_replay_doubles.py` (new).

- [ ] **Step 1: Write the failing tests** (`tests/test_e2e_evaluation/test_replay_doubles.py`): feed each new double the real request its agent builds and assert the reply: `_reply_ContextCheckDraft` answers one `FigureCheckDraft` per `F<nn> | figure <n>` the request lists, confirming by default and applying a `ReplaySource`'s `context` override (`scope`, `attribution`, `organisation`, `kind`, `evidence_words`, `verdict`) — one request is one batch of `agents.verifier_batch_size` (5) findings, so the double keys its reply to the labels that request carries, never to a global order; `_reply_StatementCheckDraft` answers one `StatementVerdictDraft` per `S<nn>` label the request lists, `consistent` by default and the scenario's `statement` override (`corrected` with a replacement text, or `inconsistent` with its reason) where the scenario names one — its request is also a batch of 5, and the writer keeps, corrects or refuses each point from the reply; `_reply_ReportWriterDraft` answers one summary point per registry line (`"<organisation> reports <value> <unit> for <period>."` for an actual, `"<organisation> projects <value> <unit> for <period>."` for a forecast), and `compose_written_report` keeps every one of them; `_reply_ReportReviewDraft` scores the seven dimensions at the scenario's value (0.9 by default) and disposes of every statement id the request lists as `supported`, unless the scenario lists it as unsupported.
- [ ] **Step 2: Run to see them fail.** Run: `"$PY" -m pytest -q tests/test_e2e_evaluation/test_replay_doubles.py`. Expected: FAIL.
- [ ] **Step 3: Implement.** In `replay.py`: delete `_reply_ClaimsDraft`, `_reply_ClaimVerdictDraft`, `_reply_ClaimEquivalenceDraft`, `_reply_CritiqueDraft`, `_reply_ReportDraft` and their imports; add the four doubles above; `ReplaySource` gains `context: dict[str, str] = field(default_factory=dict)` for the Context Check overrides and `statement: dict[str, str] = field(default_factory=dict)` for the Statement Check overrides, plus one row that scripts a `statement_check_failed` exception so the keep-on-batch-failure path (`statement_verdicts[id] == "unchecked"`) runs in the matrix. In `replay_matrix.py`, rework `REPLAY_CASE_MANIFEST`: keep `broad-constraints`, `comparative-conflict` (two organisations, two attributed rows, never a conflict), `refinement-evidence-recovery` (renamed `extra-pass-recovers-missing-target`), `blocked-html-pdf-fallback`, `stalled-refinement` (renamed `extra-pass-finds-nothing`: the missing target stays missing; one extra pass; published once with the target under Not found), `unsupported-mechanism`, `judge-failure` (renamed `review-unavailable`), `non-constraint-answer`, `empty-but-clean`, `memory-is-not-read`, `validated-cache-reuse`, `decision-context-late-candidate`; convert `same-work-mirror` (a mirror is one row, labelled by its organisation), `primary-attribution` (renamed `relay-labelled-as-relay`), `current-versus-forecast` (renamed `forecast-versus-actual-kept-apart`) and `reopen-unanswered-target` (renamed `missing-target-triggers-one-extra-pass`); retire `semantic-duplicate-claims` ("claim equivalence is gone; duplicate figures are one fact row by field key, covered by `revision-noted` and `same-work-mirror`") and `late-contradiction` ("no stage issues verdicts; two organisations' figures are two rows"); add `figure-not-on-page-dropped`, `evidence-words-not-on-page-rejected`, `scope-corrected-to-all-segments` and `revision-noted`. Each row states its sources, its `context` overrides, its declared result (accepted/partial and exit code) and its invariants over production state (for example `scope-corrected-to-all-segments`: the kept figure's scope is "all segments", the finding is `verified_corrected`, and no reader sentence says "grid-scale"). Leave `GRAPH_ONLY_HISTORICAL_MANIFEST` alone: Task 4.11 deletes the whole graph-historical harness (PD-14).
- [ ] **Step 4: Run the tests.** Run: `"$PY" -m pytest -q tests/test_e2e_evaluation/test_replay_doubles.py`. Expected: PASS. (The matrix runs in Task 4.11, once the graph of Task 4.8 is merged.)
- [ ] **Step 5: Commit** the three owned files with "test(e2e): evidence-verifier replay doubles and matrix cases".

### Task 4.10: Deletion sweep, exports and fingerprint pins

**Role:** sp-hard-implementer. **Wave:** 4D, parallel with Task 4.11. **Depends on:** Tasks 4.2–4.8 and 4.13 merged, and the reviews of 4.2, 4.3, 4.4, 4.5, 4.7 and 4.13 clean (rule R5: it edits their files, and Task 4.13 owns `researcher.py` and `test_source_evaluator.py` last).

**Owns:** the files it deletes; `agents/__init__.py`, `utils/types.py`, `utils/__init__.py`, `utils/claims.py`, `agents/prompts.py`, `agents/evidence.py`, `agents/identity.py`; the dead names in `agents/{quality,report,report_writer,report_reviewer,planner,researcher,wording,figures,verified_facts}.py`; `tests/test_imports.py`, `tests/test_types.py`, `tests/test_state.py`, `tests/test_evaluation/test_config.py`, `tests/test_agents/{test_evidence,test_identity,test_prompts,test_report,test_tool_free_prompts,test_native_react_boundary,test_source_evaluator,test_wording,test_figures,test_verified_facts}.py`.

- [ ] **Step 1: Confirm nothing outside the doomed set still imports it.** Run `grep -rn "agents.fact_checker\|agents.claim_clusters\|agents.critic\|agents.synthesizer\|utils.claims" src tests --include=*.py | grep -v "src/deep_research/agents/\(fact_checker\|claim_clusters\|critic\|synthesizer\).py\|tests/test_agents/test_\(fact_checker\|claim_clusters\|critic\|synthesizer\|synthesis_seam\|evidence_quality_seam\).py\|e2e_evaluation"`. Expected: only `agents/__init__.py` and the test files this task owns. Anything else goes back to its Phase-4 owner (rule R6).
- [ ] **Step 2: Delete.** `git rm` `agents/fact_checker.py`, `agents/claim_clusters.py`, `agents/critic.py`, `agents/synthesizer.py`, `utils/claims.py`, `tests/test_agents/test_fact_checker.py`, `tests/test_agents/test_claim_clusters.py`, `tests/test_agents/test_critic.py`, `tests/test_agents/test_synthesizer.py`, `tests/test_agents/test_synthesis_seam.py`, `tests/test_agents/test_evidence_quality_seam.py`.
- [ ] **Step 3: Remove the dead names.** From `utils/types.py`, every symbol of the acceptance grep (Caller inventory) and the fields `ResearchState.{verified_claims, claim_clusters, critique, refinement_targets, progress_history, repair_stop_reason, unique_claim_count}`, the claim fields of `ReportComposition`, `ReportQualitySnapshot`, `ReportStatement`, `ReportPoint`, `RejectedDraftPoint` and `ReportTerminalState` (critic status and score, critical-target counts), with `merge_research_state`'s claim merging and its lazy `claim_clusters` import. From `agents/prompts.py`, the fact-checker and critic prompt constants. From `agents/evidence.py`, the pair-only machinery (`source_origin_id`, `shares_lineage`, `eligible_independent_pair`, `EvidenceEligibility`, and `COPIED_TRANSPORT_RELATIONS` if nothing else reads it). From `agents/identity.py`, `merge_claim_snapshot`, `atomic_fingerprint`, `claim_cluster_id`, and `claim_fingerprint` if `grep -rn claim_fingerprint src tests` shows no surviving reader. From `quality.py`, `report.py`, `report_reviewer.py`, `planner.py` and `researcher.py`, every name Phase 4 left without a caller (`grep -rn "<name>" src tests` prints only its definition). Then rewrite `agents/__init__.py`, `utils/__init__.py` and `tests/test_imports.py` to the surviving public names, and remove the claim and critique tests from the owned test files. Also remove the D8 dead helpers — each one only when `grep -rn "<name>" src tests` shows no importer outside its own module and its own tests: `agents/wording.py`'s `hedge_forecast` and `page_modal` (D8's cutover left both dead) and `unattested_names` and `stated_scopes` if nothing reads them; `agents/figures.py`'s `bare_numbers` and `agents/verified_facts.py`'s `untraced_numbers` (both went with the `untraced_figures` gate) — `figure_in_text` stays while the P1-2 fallback reads it, and `dates_in`/`without_dates` go only if `bare_numbers` was their only caller. Remove each with its line in `agents/__init__.py`'s import block and its `__all__` entry, and with its tests. And from `utils/types.py`'s `FigureDropReason`, remove `figure_not_in_evidence` (D8 deleted the not_matched rescue branch that raised it) and keep `context_unavailable` (the P1-2 ruling reads it when an unjudged figure fails `figure_in_text`).
- [ ] **Step 4: Re-pin the fingerprints** (PD-17) with the Task 1.5 command: all five pins move (`agents/prompts.py` changed); record one comment line "Evidence Verifier plan, Task 4.10: agent set and shared prompts changed"; delete the historical single-value tests (including every `CRITIC_PROMPT_FINGERPRINT` test and the constant).
- [ ] **Step 5: Run the checks.** Run the acceptance grep of the Caller inventory over `src tests` excluding `src/deep_research/e2e_evaluation` and `tests/test_e2e_evaluation`: it prints nothing. Run the IMPORT SMOKE without `deep_research.e2e_evaluation`. Run `"$PY" -m pytest -q tests --ignore=tests/test_state.py --ignore=tests/test_e2e_evaluation` and, alone, `"$PY" -m pytest -q tests/test_state.py`. Expected: PASS.
- [ ] **Step 6: Commit** with "refactor: remove the fact checker, claim clusters, critic and synthesizer; exports and pins".

### Task 4.11: E2E matrix green; the graph-historical harness retired

**Role:** sp-hard-implementer. **Wave:** 4D, parallel with Task 4.10. **Depends on:** Tasks 4.8 and 4.9 merged, and the review of 4.9 clean (rule R5).

**Owns:** `e2e_evaluation/{cases,evaluators,models,runner,replay,replay_matrix}.py`, `tests/test_e2e_evaluation/*`. (The README's "Graph-historical harness" subsection is deleted by Task 4.6, which owns `README.md`.)

- [ ] **Step 1: Models, evaluators, runner.** `models.py`: `AGENT_NAMES` as Task 4.7's; `SnapshotPass` loses `claims` and `critic_targets`; `DeterministicEvaluation` replaces the claim fields (`checked_claims`, `claims_with_provenance`, `checked_claim_provenance_ratio`, `duplicate_claims`, `contradicted_claims`, `disclosed_contradictions`, `critic_targets`, `closed_critic_targets`, `repeated_claim_snapshot_passes`) with `verified_findings`, `dropped_findings`, `context_unchecked_findings`, `duplicate_fact_rows`, `unjudged_sentences`, `missing_required_targets`, `extra_passes`. `evaluators.py` reads them from production state and from the new CLI summary lines (Task 4.6); `runner.py`'s acceptance checks `duplicate_fact_rows == 0` in place of `duplicate_claims == 0`, and `unjudged_sentences == 0` (every kept sentence carries a Statement Check verdict — a recorded batch failure is an `"unchecked"` verdict, not an unjudged one).
- [ ] **Step 2: Run the real-agent matrix and fix until green.** Run: `"$PY" -m deep_research.e2e_evaluation suite --tier controlled --repetitions 3`. Every row passes its declared result and invariants in all three repetitions; the replay doubles script `ContextCheckDraft` replies in batches of `agents.verifier_batch_size` (5) and `StatementCheckDraft` replies for every `S<nn>` label a request lists (Task 4.9), so a row with a scripted `inconsistent` verdict pins the refusal path and a row with a scripted batch failure pins `unchecked`. The invariant `_invariant_read_downloaded_once` stays deterministic: Task 4.13's run-wide tool lock serialises the tool section, so a page two sub-topics request is downloaded once and the second admission comes from the cache. A failure caused by a double or a row is fixed here; a failure caused by product code goes to its Phase-4 owner (rule R6).
- [ ] **Step 3: Retire the graph-historical harness (PD-14; decided by the user).** Delete `GRAPH_ONLY_HISTORICAL_MANIFEST`; the `graph-historical` mode in `runner.py` (its `--mode` choice and its dispatch; if the remaining mode is then the only choice, delete the `--mode` argument and its dispatch too, since a one-choice flag is dead code); the scripted six-agent doubles in `cases.py` that only that mode uses (`ScriptedDependencies` and its helpers: first run `grep -rn "ScriptedDependencies" src tests` and keep anything the real-agent matrix still imports); and their tests. Then `grep -rn "graph-historical\|graph_historical\|GRAPH_ONLY_HISTORICAL" src tests README.md` prints nothing.
- [ ] **Step 4: Run the tests.** Run: `"$PY" -m pytest -q tests/test_e2e_evaluation` and the acceptance grep of the Caller inventory over `src/deep_research/e2e_evaluation tests/test_e2e_evaluation`: it prints nothing.
- [ ] **Step 5: Commit** every owned file changed with "test(e2e): evidence-verifier matrix green; graph-historical harness retired".

### Task 4.12: Reviewer acceptance probe on the audit-2 report (part of Gate G4)

**Role:** sp-implementer, in `$W` (rule R1); the `--live` run is an operator step. **Wave:** 4D, parallel with Tasks 4.10 and 4.11. **Depends on:** Tasks 4.2, 4.3 and 4.8 merged.

**Files:** Create (untracked) `scratch/ev_review_audit2.py`.

**Interfaces:** Consumes the state and the scripted draft of `scratch/ev_compose_audit2.py` (Task 3.6: import its builder functions; do not copy them); `ReportWriterAgent` (Task 3.4); `compute_report_quality` (Task 4.3); `ReportReviewer`, `build_report_review_input`, `semantic_review_passes` (Task 4.2); `build_report_reviewer` (`runtime/assembly.py`, Task 4.8; read its signature first).

- [ ] **Step 1: Write the harness.** Behaviour:
  - Default (no model call): build Task 3.6's audit-2 state and run the writer on its scripted draft; stamp `quality = compute_report_quality(state, composition)`; build `packet = build_report_review_input(state, composition)`; print the packet's statement ids, fact rows and `deterministic` block; exit 0 when `quality.hard_failures == []` and every summary statement id is in `packet.expected_statement_ids`.
  - `--live` (operator, off-peak): the same state; the writer built with the real provider (`model_profile=settings.llm.resolve_for("report_writer")`) writes the report; then the real reviewer, built exactly as a CLI run builds it (`build_report_reviewer`), reviews it with `await reviewer.review(packet, previous=None)`. Write `scratch/ev-review-report.md` and print the review's status, the seven dimension scores, the mean and every defect with its statement ids.
  - `--live` checks: **RV1** `review.status == "scored"`; **RV2** `semantic_review_passes(review)` (mean ≥ 0.80, no material defect); **RV3** `quality.hard_failures == []` for the live-written report. Exit 0 only when all three print `PASS`.
- [ ] **Step 2: Run the default mode.** Run: `"$PY" scratch/ev_review_audit2.py`. Expected: exit 0, no model call.
- [ ] **Step 3: Run live (operator, off-peak).** Run the OFF-PEAK CHECK; if `OK`, run `"$PY" scratch/ev_review_audit2.py --live`. Expected: `PASS RV1`–`PASS RV3`, one or two writer calls and one reviewer call, under 12 minutes. On a failure, each printed defect goes to its owner (rule R6): a writing defect to Task 3.4's writer prompt, a packet defect (the reviewer could not see a label, a release or the Not found list) to Task 4.2. Then re-run this step.
- [ ] **Step 4: No commit** (scratch).

### Task 4.13: Parallel sub-topics and scoring batches

**Role:** sp-hard-implementer. **Wave:** 4C, beside Tasks 4.8 and 4.9 (off their critical path: it is L beside 4.8's XL). **Depends on:** Tasks 4.1 (the four §7.3 config values) and 2.1 merged, and Task 4.4 merged with its review clean (rule R5: it edits `agents/researcher.py` and `tests/test_agents/test_researcher.py`, which Task 4.4 owns until then). **Size:** L.

**Owns:** `src/deep_research/agents/researcher.py`, `src/deep_research/agents/react.py`, `src/deep_research/agents/base.py`, `src/deep_research/agents/source_evaluator.py`, `src/deep_research/agents/evidence_verifier.py` (its two config reads only), `tests/agent_fakes.py` (the target-keyed completer only), `tests/test_agents/test_researcher.py`, `tests/test_agents/test_react.py`, `tests/test_agents/test_source_evaluator.py`, `tests/test_agents/test_evidence_verifier.py` (the config-read tests only). Task 4.10 re-pins the researcher fingerprint afterwards (PD-17) and owns these files last.

**Interfaces:**
- Consumes: `agents.sub_topic_concurrency`, `agents.source_scoring_concurrency`, `agents.verifier_batch_size` and `agents.verifier_concurrency` (Task 4.1); `CONTEXT_CHECK_BATCH_SIZE` and `CONTEXT_CHECK_CONCURRENCY` as the defaults (Task 2.1, PD-12); `ScratchpadMemory` (`memory/scratchpad.py`); `run_react_loop` and `ToolPolicyDecision` (`react.py`); `ResearcherAgent._policy_for_task`; `extract_findings`; the existing `test_researcher.py` provider-failure pins (`provider_failure_stopped_processing`).
- Produces: `ResearcherAgent(..., sub_topic_concurrency: int | None = None)` — default the configured `agents.sub_topic_concurrency`, so a test that pins order can construct the agent with 1; `run_react_loop(..., tool_lock: asyncio.Lock | None = None)`; `BaseAgent._complete_react_decision(..., scratchpad: ScratchpadMemory | None = None)` and `_record_step(step, *, scratchpad: ScratchpadMemory | None = None)`; `sub_topic_completed_event`'s metadata gains `elapsed_s`; `SourceEvaluatorAgent` scores its batches under `asyncio.Semaphore(config.source_scoring_concurrency)`; the Evidence Verifier reads its batch size and concurrency from `self.config`. Every cap is config (PD-27); no new module constant is added.

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_agents/test_researcher.py`:

```python
@pytest.mark.asyncio
async def test_two_loops_never_see_each_others_observations_or_acquisition_context(tracker) -> None:
    """Loop A's second turn carries A's own observation and A's own acquisition
    context, never B's, whatever order the turns complete in: each loop gets its
    own ScratchpadMemory and its own policy (D9, §7.2)."""


@pytest.mark.asyncio
async def test_findings_and_events_fold_in_plan_order_not_completion_order(tracker) -> None:
    """`runs`, `findings` and the sub-topic events come out in plan order even when
    the last plan topic finishes first."""


@pytest.mark.asyncio
async def test_a_page_two_loops_request_at_once_is_downloaded_once_and_admitted_from_cache(
    tracker, web_tools
) -> None:
    """The tool lock makes the second loop's fetch of the same URL a cache hit from
    the first loop's admission, so the run downloads the page once
    (`_invariant_read_downloaded_once`)."""


@pytest.mark.asyncio
async def test_a_provider_failure_lets_running_loops_finish_and_skips_unstarted_ones(tracker) -> None:
    """One loop dies on ProviderError; the loops already running finish, and every
    loop that never started records `provider_failure_stopped_processing`."""


@pytest.mark.asyncio
async def test_an_attempt_limit_in_one_loop_still_halts_the_run(tracker) -> None:
    """RequestAttemptLimitError in one loop is re-raised after every sibling settles,
    so the node still halts the run."""
```

Append to `tests/test_agents/test_react.py`:

```python
@pytest.mark.asyncio
async def test_the_tool_lock_serialises_only_the_tool_section(tracker) -> None:
    """Two interleaved loops never enter `tool.execute` at once, while their model
    turns overlap: the lock wraps decision → execute → after_action only."""
```

Append to `tests/test_agents/test_source_evaluator.py`:

```python
@pytest.mark.asyncio
async def test_a_failing_source_scoring_batch_leaves_the_other_batches_scored(...) -> None:
    """One batch's ProviderError marks that batch `unscored_provider`; the other
    batches are scored, keyed by URL, in `task.groups` order."""
```

And in `tests/agent_fakes.py` add a target-keyed completer beside the order-based `ScriptedCompleter` (which keeps serving single-loop callers): `complete_react` dispatches a scripted decision on the `target_id=` line of the ReAct packet, and `complete_structured` on the sub-topic title line, the way `e2e_evaluation/replay.py`'s `_researcher_turn` does; a decision addressed to a topic no loop asked for fails the test outright (the order-based completer hands decisions to the wrong loop as soon as a tool suspends).

- [ ] **Step 2: Run to see them fail.** Run: `"$PY" -m pytest -q tests/test_agents/test_researcher.py -k "each_others or plan_order or downloaded_once or provider_failure_lets or attempt_limit" tests/test_agents/test_react.py tests/test_agents/test_source_evaluator.py`. Expected: FAIL.

- [ ] **Step 3: The researcher's loop.** `ResearcherAgent.run` builds one coroutine per selected sub-topic and runs them under `asyncio.Semaphore(self._sub_topic_concurrency)`; the folds (`runs`, `findings`, `events`, the acquisition snapshot) iterate the plan-ordered task list, never the completion order; `asyncio.gather(..., return_exceptions=True)` collects, and after every coroutine settles the first `RequestAttemptLimitError` is re-raised so `graph/nodes.py` still halts the run. A `stop` flag is set by the first non-recoverable provider failure in a loop and checked as a loop acquires the semaphore, so an unstarted sub-topic keeps `provider_failure_stopped_processing` while a running one finishes. `_research_sub_topic` builds `ScratchpadMemory(session_id, agent_name, max_entries)` per loop and passes `decision_context=policy.context(...)` and the run-wide `tool_lock` into `run_react_loop`; `extract_findings` takes its `policy` explicitly (and returns the target-obligation flag) instead of reading `self._active_acquisition`; delete `_active_acquisition`, `_active_target_id` and the `_last_*` counters (their values become each loop's own locals and the `sub_topic_completed_event` metadata).

- [ ] **Step 4: The tool lock.** `run_react_loop` gains a keyword-only `tool_lock: asyncio.Lock | None = None` and holds it across the policy decision, `tool.execute` and `after_action` only — never across the model call. `BaseAgent._complete_react_decision` and `_record_step` take an optional `scratchpad` argument, so a loop's prompt renders its own notes instead of the shared `self._scratchpad`.

- [ ] **Step 5: Source-evaluator batches.** `SourceEvaluatorAgent.score_sources` gathers one coroutine per batch under `asyncio.Semaphore(self._config.source_scoring_concurrency)`; each batch's failure marks its own groups `unscored_provider` and stands alone (no batch marks a later one), and the results are assembled in `task.groups` order exactly as today.

- [ ] **Step 6: The caps come from config, and the sub-topic event gains its duration.** In `EvidenceVerifierAgent`, replace `CONTEXT_CHECK_BATCH_SIZE`/`CONTEXT_CHECK_CONCURRENCY` reads with `self.config.verifier_batch_size`/`self.config.verifier_concurrency` (module constants stay the defaults, PD-12); the same two values bound `check_statements`. Add `elapsed_s` (the loop's wall seconds, rounded to 0.1) to `sub_topic_completed_event`'s metadata, so per-sub-topic time survives concurrency: the CLI prints every sub-topic event at node completion, where log timestamps no longer separate them.

- [ ] **Step 7: Run the tests.** Run: `"$PY" -m pytest -q tests/test_agents/test_researcher.py tests/test_agents/test_react.py tests/test_agents/test_source_evaluator.py tests/test_agents/test_evidence_verifier.py tests/test_agents/test_base.py tests/test_config.py`. Expected: PASS. A multi-topic test that flips decisions under the target-keyed completer is ported to it here; a failure in a file this task does not own is reported (rule R2).

- [ ] **Step 8: Commit** every owned file changed with "feat(researcher): concurrent sub-topics under a run-wide tool lock; concurrent scoring batches; caps from config (D9)".

**Acceptance:** no run exceeds `agents.sub_topic_concurrency` sub-topic loops or `agents.source_scoring_concurrency` scoring calls at once; two loops never share a scratchpad or an acquisition context; findings and events are in plan order regardless of completion order; a page two loops request is downloaded once and admitted from cache; `provider_failure_stopped_processing` and the `RequestAttemptLimitError` halt are unchanged; the Evidence Verifier's batch size and concurrency come from config; `sub_topic.completed` carries `elapsed_s`.

### Task 4.14: Concurrency and budget telemetry

**Role:** sp-implementer. **Wave:** 4D, beside Tasks 4.11 and 4.12. **Depends on:** Tasks 4.5, 4.6, 4.8 and 4.10 merged, and their reviews clean (rule R5: it edits `agents/report.py`, `cli.py`, `graph/nodes.py`, `runtime/assembly.py` and `utils/types.py`, which those tasks own). **Size:** M.

**Why this sequencing (disjoint from 4.5, 4.6 and 4.13).** The telemetry lands in the two artifacts those tasks own — the quality JSON (`agents/report.py`, Task 4.5) and the CLI summary (`cli.py`, Task 4.6) — so it runs after both are merged and review-clean rather than beside them (same pattern as Task 4.10's lock wait); 4.13's files (`researcher.py`, `react.py`, `base.py`, `source_evaluator.py`, `evidence_verifier.py`, `agent_fakes.py`) are not touched at all: the provider-call gauge lives in `providers/` and `observability/`, so the two tasks can run in parallel and neither waits on the other.

**Owns:** `src/deep_research/observability/run_telemetry.py` (new), `src/deep_research/observability/__init__.py` (exports only), `src/deep_research/providers/retry.py`, `src/deep_research/providers/deepseek_provider.py`, `src/deep_research/providers/openai_provider.py`, `src/deep_research/providers/factory.py` (passing the collector the way it passes `request_budget`), `src/deep_research/utils/types.py` (one `ResearchState` field), `src/deep_research/agents/report.py` (the quality record's telemetry block only), `src/deep_research/cli.py` (one summary line), `src/deep_research/runtime/assembly.py`, `src/deep_research/graph/nodes.py` (create and thread the collector only), `tests/test_observability_run_telemetry.py` (new), `tests/test_retry_policy.py`, `tests/test_cli/test_render.py`, `tests/test_agents/test_report.py`.

**Interfaces:**
- Consumes the existing seams only: `RequestBudget`'s observer stream (`set_observer`, `RequestBudgetUpdate`, the callable the CLI's `RequestBudgetStream` already receives — attempt reserved = one call in flight, reported tokens = one call done, and the collector decrements on any completion so a failed attempt does not leak); `ProviderResponseTelemetry`'s `configured_max_tokens`, `usage` and `finish_reason_category`; the providers' `_record_tokens` call sites; `providers/retry.py`'s retry loop; the tracker's per-call spans (the provider calls already carry `agent_name`, and the span knows its seconds).
- Produces: `RunTelemetry` (frozen: `rate_limit_errors`, `rate_limit_recovered`, `peak_calls_in_flight`, `peak_agent`, `stages: tuple[StageTelemetry, ...]` with `agent`, `calls`, `seconds`, `slowest_seconds`, `operations: tuple[OperationTelemetry, ...]` with `agent`, `max_output_tokens`, `configured_cap`, `truncations`, `cap_key`); `RunTelemetryCollector` (a no-op default, so every harness that builds a provider directly keeps working); `render_telemetry_line(telemetry) -> str`; and `ResearchState.run_telemetry: RunTelemetry | None = None` (replaced on write, never appended).

- [ ] **Step 1: Write the failing tests** (`tests/test_observability_run_telemetry.py`, plus one addition each in the two owned test files, and a 429-accounting case in `tests/test_retry_policy.py`):

```python
def test_rate_limits_are_counted_and_recovered() -> None:
    """Three transient rate-limit failures, two retried to success, one that
    exhausted the ladder: 3 errors, 2 recovered."""


def test_peak_in_flight_is_the_high_water_mark() -> None:
    """Reservations and completions interleaved: the peak is 8, not the final 0,
    and it names the agent whose calls set it."""


def test_a_stage_aggregates_calls_seconds_and_slowest() -> None:
    """Three calls of 2 s, 9 s and 4 s: calls=3, seconds=15.0, slowest=9.0."""


def test_output_tokens_are_reported_against_the_cap() -> None:
    """The operation's maximum output tokens are compared with that call's
    `configured_max_tokens`, and `finish_reason_category == "length"` counts
    as a truncation."""


def test_the_advice_names_the_knob_and_the_cap() -> None:
    """N × 429 prints "rate limits hit N times; consider lowering
    agents.verifier_concurrency" (the knob of the agent at the peak); a call at
    93% of its cap prints "output within 93% of the report_writer cap; consider
    raising it"; with neither, no advice line is added."""
```

and `test_the_quality_record_carries_the_telemetry_block` in `tests/test_agents/test_report.py` (the record's `telemetry` key holds the four parts) and `test_the_summary_prints_one_telemetry_line` in `tests/test_cli/test_render.py`.

- [ ] **Step 2: Run to see them fail.** Run: `"$PY" -m pytest -q tests/test_observability_run_telemetry.py tests/test_retry_policy.py tests/test_cli/test_render.py tests/test_agents/test_report.py -k "telemetry or retry"`. Expected: FAIL (`ModuleNotFoundError: deep_research.observability.run_telemetry`).

- [ ] **Step 3: Collect.** In `providers/retry.py`, wrap the retry loop so each transient failure is reported to the collector and a later attempt's success marks it recovered; the provider's `_record_tokens` call site also records the call's seconds (from the span), its `usage.output_tokens`, its `configured_max_tokens` and whether `finish_reason_category == "length"`. The collector's budget observer gives `peak_calls_in_flight` and `peak_agent`. One `RunTelemetryCollector` per run is created in `runtime/assembly.py::build_runtime` beside `RequestBudget` (line 403) and handed to the providers exactly as the budget is (through `build_chat_provider`/`factory.py`), so every provider call of the run reports to the same collector; the collector defaults to a no-op, so Tasks 2.3, 3.6 and 4.12's harnesses keep building providers directly.

- [ ] **Step 4: Store and print.** Add `ResearchState.run_telemetry` (one field; `utils/types.py`), and let the terminal publication thread the collector from `build_runtime` through `build_graph` into the node that calls `_terminal_artifacts` (`graph/nodes.py`), stamping the state before the record is rendered. `render_quality_record` gains a `telemetry` block (the `RunTelemetry` dump, `None` when the run had no collector). `cli.py` prints one line, after the Integrity line:

```
Telemetry: peak 8 provider calls in flight (evidence_verifier); 3 rate limits (2 recovered); slowest call report_reviewer 223.4 s; report_writer output 61,200 of 65,536 tokens (93% of its cap); 0 truncated
```

followed, only when triggered, by the §7.3 advice: `rate limits hit N times; consider lowering <knob>` when N > 0 (`<knob>` is the config key of the agent at the peak: `agents.verifier_concurrency`, `agents.sub_topic_concurrency` or `agents.source_scoring_concurrency`), and `output within X% of the <op> cap; consider raising it` when a call used 90% or more of its cap or was truncated (`<op>` is the operation, with the config key that bounds it — `planner_final_max_tokens`, `report_review_max_tokens`, `react_decision_max_tokens`, else `llm.max_tokens`). Advice only: nothing is auto-tuned (§12).

- [ ] **Step 5: Run the tests.** Run: `"$PY" -m pytest -q tests/test_observability_run_telemetry.py tests/test_retry_policy.py tests/test_cli tests/test_agents/test_report.py tests/test_runtime/test_outcome.py`. Expected: PASS.

- [ ] **Step 6: Commit** every owned file changed with "feat(telemetry): rate limits, peak in flight, per-stage times and output caps (spec 7.3)".

**Acceptance:** a run's quality JSON carries the four §7.3 parts, and the CLI prints them in one line; the advice lines appear only when their trigger fires and name a real config key; no behaviour changes: nothing auto-tunes concurrency or budgets, and a run without a collector still works.

### Gate G4 — end of step 4

Run in `$W` once Tasks 4.1–4.14 are merged and review-clean (rule R8):

```bash
"$PY" -m pytest -q tests --ignore=tests/test_state.py
"$PY" -m pytest -q tests/test_state.py
"$PY" -m deep_research.e2e_evaluation suite --tier controlled --repetitions 3
"$PY" -c "import deep_research.agents, deep_research.graph, deep_research.runtime, deep_research.api, deep_research.cli, deep_research.main, deep_research.evaluation, deep_research.e2e_evaluation"
grep -rnwE "<the Caller inventory's acceptance pattern>" src tests    # prints nothing
"$PY" scratch/ev_review_audit2.py
"$PY" scratch/ev_review_audit2.py --live      # operator, off-peak only
"$PY" -m deep_research --help
```

**Pass condition:** every command succeeds; the grep prints nothing; `--help` shows `--max-iterations` described as extra research passes; the reviewer probe exits 0 in its default mode and prints `PASS RV1`–`PASS RV3` live; the CLI summary of the reviewer probe's default mode carries the one Telemetry line (Task 4.14). That is spec §9 step 4's proof: the full test suite green and the reworked e2e replay matrix green, plus the proof that the real Report Reviewer accepts a report built to §6 (F7). Do not start Phase 5 until G4 passes.

---

## Phase 5 — The planner's floor (spec step 5)

Phase goal: the benchmark question plans five or fewer required targets, one per organisation, measure, period and kind the question asks for, with no MWh, facility-type or definition requirement.

### Task 5.1: The final target fields

**Role:** sp-implementer. **Wave:** 5A, alone. **Depends on:** Gate G4.

**Owns:** `utils/types.py`, `utils/__init__.py`, `tests/evidence_fakes.py`, `tests/test_types.py`.

- [ ] **Step 1: Write the failing test** (append to `tests/test_types.py`):

```python
def test_a_target_is_its_structured_fields_and_needs_a_measure() -> None:
    target = make_target()
    assert not hasattr(target, "required_dimensions") and not hasattr(target, "critical")
    with pytest.raises(ValidationError):
        make_target(measure="")
```

- [ ] **Step 2: Run to see it fail.** Run: `"$PY" -m pytest -q tests/test_types.py -k needs_a_measure`. Expected: FAIL.
- [ ] **Step 3: Implement.** `EvidenceTarget` keeps exactly the final fields of "Shared interfaces" (`measure: str = Field(min_length=1)`); remove `required_dimensions`, `critical`, their validators and `counted_evidence_targets`' legacy branch; remove the `if "required_dimensions" in EvidenceTarget.model_fields` branch from `make_target`.
- [ ] **Step 4: Run.** `"$PY" -m pytest -q tests/test_types.py` and, alone, `tests/test_state.py`. Expected: PASS (tests elsewhere that build legacy targets are fixed by 5.2 and 5.3).
- [ ] **Step 5: Commit** the four owned files with "refactor(types): targets are their structured fields (spec 7.1)".

### Task 5.2: The planner asks for the question's targets only

**Role:** sp-hard-implementer. **Wave:** 5B, parallel with Task 5.3. **Depends on:** Task 5.1 merged.

**Owns:** `agents/planner.py`, `tests/test_agents/test_planner.py`.

- [ ] **Step 1: Write the failing tests** (append; delete the tests of `required_dimensions`, `critical`, the answer-form and evidence-period boilerplate):

```python
def test_a_draft_target_without_a_measure_is_a_plan_problem() -> None:
    draft = _structured_draft(measure="")
    assert any("measure" in problem for problem in plan_problems_for(draft))


def test_the_answer_contract_adds_no_boilerplate_to_targets() -> None:
    targets = _draft_targets(_structured_draft(), "topic-01")
    [stamped] = apply_answer_contract([_topic_with(targets)], _contract())
    assert stamped.evidence_targets[0].model_dump() == targets[0].model_dump()


def test_the_plan_instruction_states_the_floor() -> None:
    for phrase in ("one target per organisation, measure, period and kind",
                   "required only for what the question names",
                   "optional", "paywalled"):
        assert phrase in PLAN_INSTRUCTION
```

(`plan_problems_for` is the module's existing problem-listing entry point — `target_problems` or `_plan_problems`, whichever takes one sub-topic draft; `_topic_with` and `_contract` build the `SubTopic` and `AnswerContract` of Task 1.4's tests.)
- [ ] **Step 2: Run to see them fail.** Run: `"$PY" -m pytest -q tests/test_agents/test_planner.py -k "measure or boilerplate or floor"`. Expected: FAIL.
- [ ] **Step 3: Implement.** `EvidenceTargetDraft` drops `required_dimensions` and `critical`; `_draft_targets` stamps only the structured fields and `required`; a target with an empty `measure` is a plan problem (so the existing review-and-repair loop, `MAX_PLAN_REVIEW_CALLS = 2`, asks for it); `apply_answer_contract` stops adding answer-form and evidence-period items. In `PLAN_INSTRUCTION`, replace every paragraph about required dimensions, answer form and evidence period with:

```
"Plan one target per organisation, measure, period and kind the question asks for, "
"and nothing else required. A target is required only for what the question names: "
"for 'how much grid-scale battery storage was added in 2024, and what do the latest "
"forecasts project for 2025', that is the 2024 actual and each organisation's latest "
"2025 forecast. Targets you add yourself (an energy figure in MWh for a capacity "
"question, facility types, definitions) are optional, and so is any issuer whose "
"figures are only behind a paywall. Optional targets never fail a run. Keep the "
"temporal contract: the latest forecasts, no cutoff inferred from a year in the "
"question, actuals labelled apart from forecasts. For the example question, about "
"five targets are expected.\n"
```

and update `_PLAN_REPLY_EXAMPLES` to targets without `required_dimensions` or `critical`.
- [ ] **Step 4: Run.** `"$PY" -m pytest -q tests/test_agents/test_planner.py`. Expected: PASS.
- [ ] **Step 5: Commit** both owned files with "feat(planner): the question's targets only (spec 7.1)".

### Task 5.3: Target consumers

**Role:** sp-implementer. **Wave:** 5B, parallel with Task 5.2. **Depends on:** Task 5.1 merged.

**Owns:** `agents/researcher.py`, `agents/report_reviewer.py`, `evaluation/cases/planner.py`, `evaluation/evaluators.py`, `e2e_evaluation/replay.py`, `e2e_evaluation/replay_matrix.py`, `tests/test_agents/test_researcher.py`, `tests/test_agents/test_report_reviewer.py`, `tests/test_agents/test_planner_researcher_seam.py`, `tests/test_evaluation/test_cases_planner.py`, `tests/test_evaluation/test_evaluators_agents.py`, `tests/test_e2e_evaluation/test_real_agents.py`.

- [ ] **Step 1: Inventory.** `grep -rn "required_dimensions\|\.critical\b\|critical=" src tests` — every hit in an owned file is migrated: the researcher's unit mentions (around researcher.py 780) come from `target.unit_dimension`; the reviewer's target view shows the structured fields; the planner evaluation gates `_dimensions_are_checkable_passes` and `_no_vague_dimensions_passes` are replaced by `targets_have_measure` (every target has a non-empty measure and a known unit dimension or none); the e2e planner double and the matrix's `_topic(critical=...)` builder stop passing the removed fields. A hit in a file not owned here goes to the controller.
- [ ] **Step 2: Tests.** Update the owned tests to the final target shape and add `test_targets_have_measure_gate` in `test_evaluators_agents.py` (passes on a structured plan, fails on a target with an empty measure).
- [ ] **Step 3: Run.** `"$PY" -m pytest -q tests/test_agents/test_researcher.py tests/test_agents/test_report_reviewer.py tests/test_agents/test_planner_researcher_seam.py tests/test_evaluation tests/test_e2e_evaluation`. Expected: PASS.
- [ ] **Step 4: Commit** the owned files changed with "refactor: target consumers read the structured target fields".

### Task 5.4: Phase-5 integration

**Role:** sp-implementer. **Wave:** 5C, parallel with Task 5.5. **Depends on:** Tasks 5.2 and 5.3 merged.

**Owns:** `agents/__init__.py`, `tests/test_evaluation/test_config.py`. Re-pin the planner and researcher fingerprints (Task 1.5 Step 2 procedure), export any new public name, run `"$PY" -m pytest -q tests/test_imports.py tests/test_evaluation/test_config.py`, commit "chore: re-pin planner and researcher fingerprints".

### Task 5.5: Step-5 proof: the plan probe (Gate G5)

**Role:** sp-implementer, in `$W`; the `--live` run is an operator step. **Wave:** 5C. **Depends on:** Task 5.2 merged.

**Files:** Create (untracked) `scratch/ev_plan_probe.py`.

- [ ] **Step 1: Write the probe.** Build the planner with `build_agent("planner", settings, tracker=tracker, provider=provider, tools=build_tools(settings, tracker=tracker, memory=..., request_budget=budget), session_id="ev-plan-probe", reputation=None)` from `runtime/assembly.py` (tracker, settings and provider as in Task 2.3's `--live`; memory: an in-memory `LongTermMemory` as `runtime/assembly.build_runtime` builds for tests, or `None` if `build_tools` accepts it), run it on `QUESTION` from `initial_graph_state(session_id="ev-plan-probe", question=QUESTION)` inside `tracker.session_span`, and print every target (id, required, measure, unit dimension, period, kind, organisation). Checks: **Q1** at most five required targets; **Q2** no required target with `unit_dimension == "energy"`; **Q3** no required target whose measure contains "facility", "type of" or "definition"; **Q4** a required target with period 2024, kind actual and organisation EIA (`same_organisation`), and required 2025 forecast targets for at least two organisations. `--offline` instead feeds the planner a `ScriptedCompleter` whose draft is the audit-3 plan (six targets, one MWh) and prints what the stamped plan keeps, to show the code path; it checks nothing about the model.
- [ ] **Step 2: Run live (operator, off-peak).** OFF-PEAK CHECK, then `"$PY" scratch/ev_plan_probe.py`. Expected: `PASS Q1`–`PASS Q4`, about 3 minutes.
- [ ] **Step 3: No commit** (scratch).

### Gate G5 — end of step 5

```bash
"$PY" -m pytest -q tests --ignore=tests/test_state.py
"$PY" -m pytest -q tests/test_state.py
"$PY" -m deep_research.e2e_evaluation suite --tier controlled --repetitions 1
"$PY" scratch/ev_plan_probe.py      # operator, off-peak only
```

**Pass condition:** the suites are green; the probe prints PASS for Q1–Q4 (spec §9 step 5: five or fewer required targets, no MWh, facility-type or definition requirements). Do not start Phase 6 until G5 passes.

---

## Phase 6 — Live proof (spec step 6)

### Task 6.1: Live-proof labels

**Role:** sp-implementer, in `$W`. **Wave:** 6A, parallel with the final whole-branch review. **Depends on:** Gate G5.

**Files:** Modify (untracked) `scratch/run_live_proof.py`.

- [ ] **Step 1: Per-label arguments.** Change `RUNS` to `dict[str, tuple[str, list[str], dict[str, str]]]` (question, extra CLI arguments, extra environment); keep the old labels as `(AUDIT or SMOKE, [], {})`; add `"ev-preflight": (AUDIT, ["--max-iterations", "0", "--request-tavily-attempt-ceiling", "12"], {"AGENTS_MAX_SUB_TOPICS": "2"})` and `"ev-1": (AUDIT, [], {})`. `run()` appends the extra arguments, merges the extra environment, and writes both to `run.env` (`EXTRA_ARGS=...`, `EXTRA_ENV=...`). Keep `PROOF_MAX_ITERATIONS` as it is (it still maps to `--max-iterations`); update the docstring's "macro refinement budget the critic may spend" to "extra research passes for missing required targets".
- [ ] **Step 2: A stage-time summary.** After the run, read `cli.log` and print each stage's duration: the time between consecutive `graph.node.completed` lines, per node name and pass, and the total; then print the researcher's slowest sub-topic and its per-turn mean, never the sum — each sub-topic's `sub_topic.completed` event carries `elapsed_s` (Task 4.13), so `slowest = max(elapsed_s)` and `per_turn_mean = elapsed_s / iterations` across the sub-topics (concurrent sub-topics no longer separate in the log's timestamps). Append them to `SUMMARY.tsv` as a `stages=` column, a `researcher=` column (`slowest=…; per_turn_mean=…`) and a `telemetry=` column (the Telemetry line, Task 4.14).
- [ ] **Step 3: Dry check.** `"$PY" -c "import runpy; m = runpy.run_path('scratch/run_live_proof.py'); print(sorted(m['RUNS']))"` lists `ev-1` and `ev-preflight`. No commit (scratch).

### Task 6.2: Capped live pre-flight (operator)

**Depends on:** Task 6.1; the final whole-branch review's fixes merged; OFF-PEAK CHECK prints `OK`.

- [ ] Run `"$PY" scratch/run_live_proof.py ev-preflight`. **Pass**, every item:
  - exit 0. The runner passes `--require-quality`, so 0 means quality `accepted`; exit 4 (the report was not accepted) fails the pre-flight;
  - `output/live-proof/ev-preflight/` holds the report, the evidence log and the quality JSON, and the report has every §6.1 section; no traceback in `cli.log`;
  - timing: the slowest sub-topic's `elapsed_s` (Task 6.1) plus every other stage's seconds ≤ 30 minutes as the projected first pass — concurrent sub-topics overlap, so the sum of per-sub-topic times is not the projection; the researcher's per-turn mean ≤ 27 s; the Evidence Verifier, Report Writer and Report Reviewer are each within 1.5 times their "Runtime budget" row;
  - zero `evidence_verifier_context_check_failed` errors in the quality JSON, and the Findings line shows 0 unchecked context;
  - the Integrity line shows `0 forecasts without release` (PD-24);
  - the Telemetry line (Task 4.14) shows zero unrecovered 429s and `0 truncated`.

  On a failure, stop: fix through the owning task's fix loop, re-run the affected gate's offline commands, then repeat 6.2. A per-turn mean over 27 s or a projected first pass over 30 minutes is fixed by lowering the named config knob before `ev-1` — `agents.sub_topic_concurrency` to 3, no code change and no re-run of 6.2, because the sequential budget already fits 45 minutes — and `agents.max_iterations: 6` (review item 15) is the last resort. Recurring 429s are lowered the same way on the knob the Telemetry advice names (the knob of the agent at the peak; `agents.verifier_concurrency` first), and a truncated or near-cap call by raising the cap that advice names. A failed or slow Context Check batch is review item 13's one config line.

### Task 6.3: The live run `ev-1` (operator)

**Depends on:** Task 6.2 passed; OFF-PEAK CHECK prints `OK`.

- [ ] Run `"$PY" scratch/run_live_proof.py ev-1` with the default configuration (one extra pass at most). The run is never hard-stopped. Record the commit, exit code, wall time and stage summary from `output/live-proof/SUMMARY.tsv`.

### Task 6.4: Independent audit against spec §10

**Role:** a fresh read-only reviewer that has not seen the implementation. **Depends on:** Task 6.3.

- [ ] Give the reviewer the spec, `output/live-proof/ev-1/` (report, evidence log, quality JSON, `cli.log`, `run.env`) and read access to the reads the evidence log names. It checks spec §10 item by item — (1) wall time ≤ 45 minutes; (2) the summary answers both halves: EIA's 2024 figure with its release, at least two organisations' latest 2025 forecasts each with its release, 2025 actuals labelled as actuals; (3) every number traces to a checked figure on its cited page and no relay is presented as its originator; (4) no wrong scope, period or kind (no all-segment figure called grid-scale; no actual presented as a forecast); (5) the Report Reviewer accepted with no gate failure; (6) its own rating — and reports each with evidence (the line of the report and the page text). **Pass:** all six hold and the rating is GREAT.

**If the audit fails:** the failures are defects with owners (the task whose code produced them); fix through the normal fix loop, re-run Gates G4/G5 offline, and repeat 6.2–6.4 off-peak. Per spec §2, live runs stay at one extra pass until the report is judged great.

---

## Over-engineering review: decisions for the user

Each item goes beyond the spec's letter, or is a choice the spec leaves open. Each carries a recommendation. None is silently included: the plan does what the "Plan" column says. Items marked **decided** were ruled on by the user after the plan audit (`agent://FablePlanAudit`) or during execution (D7, D8 and D9: `agent://FableParallel`); the rest stand as recommended unless the user rules otherwise.

| # | Item | Plan | Recommendation and trade-off |
|---|---|---|---|
| 1 | Per-task git worktrees and merge-on-DONE (R1, R4) | included | Keep. This is what makes the parallel waves safe. The alternative, one shared tree, saves the merges but gives phantom test failures from siblings' half-edits and review diffs polluted by other tasks' commits. |
| 2 | New module `agents/wording.py` (PD-19) | included; **decided**: Task 3.3 runs in wave 1B | Keep. The verifier and the writer both need `stated_role`. Moving 3.3 to wave 1B lets Task 2.1 import `stated_role` from `wording.py` from the start, so no task edits `evidence_verifier.py` across phases. |
| 3 | Naming an agency's own page from its host (PD-18) | included, as the last fallback after the Source Evaluator's validated issuer (PD-25) | Keep (about 15 lines). PD-25 is the mechanism; PD-18 only covers a page the Source Evaluator recorded no issuer for. Only government and education hosts qualify, and the page must name the organisation. |
| 4 | "source:" as an attribution cue (Task 2.1) | included | Keep. The EIA STEO read is a mirror on ent.news that credits EIA in a source line. Without the cue, that forecast loses its EIA attribution and cannot answer the "EIA forecast" target. |
| 5 | A truncated Context Check batch is asked once more in two halves | included | Keep. It is bounded (two extra calls at most), and truncation is the likely failure. The alternative, going straight to `context_unchecked`, is simpler but leaves a whole batch unchecked (up to 5 findings, `agents.verifier_batch_size`). |
| 6 | Missing required targets computed by code, not by the reviewer (PD-5) | included | Keep. The spec lists them as reviewer output; computing them from fields (§6.6) makes the extra pass deterministic and immune to a reviewer outage. |
| 7 | The writer cites labels only, so there is no URL guard (PD-6) | included | Keep. A URL the findings do not carry cannot occur. |
| 8 | The graph-historical e2e harness (PD-14) | **RETIRED — decided** | Task 4.11 deletes it; Task 4.6 deletes its README subsection; Gate G4 has no graph-historical command. The real-agent matrix covers the new graph end to end. Saves about an hour in Task 4.11 and all future upkeep of doubles for a graph that no longer exists. |
| 9 | Keep the names `--max-iterations` and API `max_iterations` (PD-15) | included | Keep. Existing invocations keep working. Renaming to `--max-extra-passes` would be cleaner but breaks callers, and D6 allows no alias. |
| 10 | Delete historical single-value fingerprint tests when they break (PD-17) | included | Delete. They pin history, not behaviour; the drift-alarm pins stay. |
| 11 | A wall-clock guard that skips the extra pass after a slow first pass | not included | Do not add. Task 6.2's timing criteria measure what the guard would protect; add it only if the pre-flight projects a first pass over 30 minutes after item 15's fallback (6 turns). |
| 12 | A code cap of five required targets | not included; **decided**: `agents.max_sub_topics: 5` in `config.yaml` (Task 1.3) | A config value, not code. §7.1 relies on the planner; Gate G5 and the pre-flight check it. |
| 13 | An `evidence_verifier` timeout override in `config.yaml` | not included; **decided** trigger | Add `timeout: 240.0` under `model_overrides.evidence_verifier` (one line, Task 4.1 Step 5) only if Gate G2's live run or the pre-flight records a failed Context Check batch (`evidence_verifier_context_check_failed`) or a batch over about 60 s. A timed-out batch shows as "unchecked context", not as slowness, so a slow-batch trigger would miss it. |
| 14 | The e2e row `extra-pass-finds-nothing`, beyond the spec's six Evidence Verifier cases | included | Keep. It pins Review Focus item 3. |
| 15 | The researcher's turn cap 5 → 7 (`agents.max_iterations: 7`, Task 1.3) | included; **decided** | Without it the tool budget of 20 changes nothing: the ReAct loop stops at 5 model turns. Costs about 5 minutes of first pass (35 turns at about 18.6 s). If the pre-flight projects a first pass over 30 minutes, use 6. |
| 16 | An accepted report with passes spent finishes `completed` (PD-23) | included; **decided** | Matches §6.4 ("or is listed under Not found") and §6.5. `max_iterations` now means passes spent and the report not accepted, so the CLI never prints `max_iterations`/`partial` beside an accepted report. |
| 17 | R8 exception: Tasks 3.1 and 3.2 start in wave 2A beside Task 2.1; Task 3.3 in wave 1B | included; **decided** | Saves about 75 minutes on the critical path; no file overlap. Their proof stays Gate G3, and Task 3.4 still waits for Gate G2. |
| 18 | Three small live probes: researcher extraction (Task 1.6 `--live`), reviewer acceptance (Task 4.12), stricter pre-flight criteria (Task 6.2) | included; **decided** | About 10 minutes of off-peak model time in total. Each turns a "discovered in the 45-minute live run" risk into a gate check. |
| 19 | The Source Evaluator's validated issuer as the page owner, and in the writer's attested corpus (PD-25) | included; **decided** | A few lines. Removes the refusal of "Wood Mackenzie projects …" when only `woodmac.com` was attested, and makes PD-18 a last resort. |
| 20 | `duplicate_fact_rows` kept as an invariant (PD-10) | included | Keep the one-line gate; no test fixture is spent on it, because `fact_rows()` already merges same-fact rows. The alternative, dropping the gate and its snapshot field, departs from §6.4's list for no runtime gain. |
| 21 | Four integration tasks (1.5, 2.2, 3.5, 5.4) exist only for re-exports and fingerprint pins | included | Keep: they are off the critical path and keep `agents/__init__.py` and `test_config.py` single-owner per wave. The alternative folds each into its phase's last implementation task (fewer dispatches, one more shared file per wave). |
| 22 | `same_organisation`'s token heuristics (initials, prefix of 4 or more, legal suffixes), Task 3.1 | included | Keep for now; it is tested. With PD-25 supplying validated publisher names, it could shrink later. |
| 23 | A `release` field (with evidence words) on `FigureCheckDraft`, so the Context Check can point at the page's edition line | not included | Add only if the pre-flight's `forecasts_without_release` is above 0. PD-24's label keeps the reader informed meanwhile. |
| 24 | D7: minimal writer guards (numbers, dates, scope, a known label), labels carry provenance, the reviewer flags prose that contradicts its label | superseded by D8 | D7's minimal *code* guards never shipped: the user's D8 replaces them with the LLM checks (Context Check for figures, Statement Check for sentences). What D7 added and stays: code-built labels on every figure, the reviewer's defect for contradicting prose (§6.3), and the two prompt lines. |
| 25 | D8: the Context Check judges each figure and the Statement Check each sentence; 5 items per call, 8 in flight; code keeps only "the quoted words are on the page" and mechanical rules | included; **decided** (user, spec `d34fd21`) | Removes every code wording check, and with them the false refusals that cost the G3 live runs two of three forecasts. Contract: `SDD/d8-contract.md`. The batch bounds become config in D9 (PD-12). |
| 26 | D9: researcher sub-topics concurrent (5) under one run-wide tool lock, source-evaluator scoring batches concurrent (3), every cap in config, §7.3 telemetry with advice only | included; **decided** (user, spec `3789d0a`, from `agent://FableParallel`) | The one change measured to move the first pass materially: ≈ 23–27 min → ≈ 14–17, and ≈ 24–28 with the extra pass, under the 30-minute target. Rejected alternatives: fewer or larger sub-topics (paid in turns per target; `AGENTS_MAX_SUB_TOPICS` stays the fallback), overlapping only first extractions (dominated), tool calls within a turn (high risk for ≤ 30 s), overlapping the verifier with the source evaluator (1 minute for graph complexity). Caps are config so live results can lower them without a code change: Task 4.13 implements, Task 4.14 reports. |
