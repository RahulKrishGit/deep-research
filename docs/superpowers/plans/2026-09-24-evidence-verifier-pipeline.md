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
- Context Check: one call per batch of 15 findings, batches run concurrently (at most 4 at once), no tools and no web search, reasoning effort `high` (§5.2).
- Researcher tool budget per sub-topic: 20 (`agents.tool_budget_overrides.researcher`, §7.2).
- Snippet: one or two sentences copied verbatim, enforced as at most 600 characters (`MAX_SNIPPET_CHARS`).
- Report Reviewer acceptance: mean ≥ 0.80 over the seven dimensions, no material defect, no gate failure (§6.3).
- Out of scope (§12): UI changes (README line 747 records that the Streamlit app was removed; there is no UI code to compile), other benchmark questions (they are exercised by the e2e matrix only), and parallel researcher sub-topics.
- Never read `.env`. Live model calls happen off-peak only: never start one if any minute of the next 50 minutes falls inside Mon–Fri 01:00–04:00 or 06:00–10:00 UTC. A started live run is never hard-stopped.
- New public names in `src/deep_research/agents/*.py` are imported by `src/deep_research/agents/__init__.py` and listed in its `__all__` (`tests/test_imports.py::test_agent_submodule_public_names_all_reach_all`). Public names in `src/deep_research/utils/types.py` that other packages use are re-exported from `src/deep_research/utils/__init__.py` the way `Finding` is today.
- No formatter, linter, or whole-suite run inside a task. Whole-suite runs happen only at the phase gates that name them.

## Review Focus

1. **A forecast the writer states as fact** ("EIA's outlook adds 14 GW in 2025"): it must be rewritten once by code, re-checked, and kept. The 2026-09-24 pre-flight lost two of its three 2025 forecasts to this refusal. Pinned by Task 3.4 `test_forecast_stated_as_fact_is_rewritten_once_and_kept` (the rewrite itself: Task 3.3 `test_hedge_forecast_makes_a_forecast_read_as_one`).
2. **The Context Check call fails for a batch** (outage, timeout, truncation twice): those findings stay citable with the "unchecked context" label, the run records the error, and the report still publishes. Pinned by Task 2.1 `test_failed_batch_marks_findings_context_unchecked` and Task 4.8 `test_run_publishes_when_the_context_check_fails`.
3. **The extra pass finds nothing for the missing target**: the report publishes once with the target under Not found; there is no second extra pass and no crash. Pinned by Task 4.8 `test_extra_pass_that_finds_nothing_publishes_with_not_found` and e2e row `extra-pass-finds-nothing` (Task 4.9).
4. **An all-segment figure written as grid-scale** (audit-2's 18.9 GW is "utility, C&I, and residential"): the sentence is refused and logged; it never reaches the reader. Pinned by Task 3.4 `test_grid_scale_wording_on_an_all_segment_figure_is_refused`.
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
"$PY" -m deep_research.e2e_evaluation suite --tier controlled --mode graph-historical --repetitions 3

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
- **PD-10 Gate set (§6.4).** `unresolved_citations`, `uncited_settled_points`, `duplicate_fact_rows`, `missing_as_of`, `missing_scope`, `untraced_figures`, `unaccounted_required_targets`, `missing_reader_report`, `missing_evidence_ledger`. Removed: `duplicate_claims`, `duplicate_source_rows`, `unscored_cited_sources`, `contradicted_settled_claims`, `broad_plan_coverage_below_0.80`, `unanswered_critical_targets`.
- **PD-11 Names (D5).** The renames apply to what an operator or reader sees: module `agents/report_writer.py` (class `ReportWriterAgent`, node and config key `report_writer`) and module `agents/report_reviewer.py` (class `ReportReviewer`, node `report_reviewer`, service role and `model_overrides` key `report_reviewer`). Unchanged: the artifact names (`report-<session>-<n>.md`, `-evidence.md`, `-quality.json`), the code name "evidence ledger", the config key `agents.report_review_max_tokens`, and the graph status value `max_iterations` (API and CLI vocabulary, now meaning "extra passes exhausted").
- **PD-12 Context Check constants.** Batch size 15, concurrency 4 and the 3,000-character passage window are module constants, not config.
- **PD-13 An unscored review** (provider failure or invalid reply) routes to publication with graph status `incomplete` and quality `partial`. It never fails the run.
- **PD-14 Graph-historical e2e harness.** It is kept and its scripted agent doubles are reworked to the new agent set. Its two coverage-gate rows are retired, because their gate is removed. (Retiring the whole harness is listed in the review section.)
- **PD-15 CLI and API names stay.** The CLI flag `--max-iterations` and the API request field `max_iterations` keep their names, so existing invocations keep working (priority 1); they now set `max_extra_passes`, with the same numbers as before (PD-2). The config key (`graph.max_extra_passes`, env `GRAPH_MAX_EXTRA_PASSES`) and the state field (`ResearchState.max_extra_passes`) are renamed, as D4 names them. (Renaming the flag too is listed in the review section.)
- **PD-16 When the old target fields leave.** `support_policy` leaves in step 4, because §8 removes "pair and support-policy fields" with the fact checker and nothing reads it after that. `required_dimensions` and `critical` leave in step 5 (§7.1), with the planner rewrite. In step 4 the planner stops validating `required_dimensions` against claim checkability (the only reason it imported `claim_clusters`), and nothing reads `critical` once the `unanswered_critical_targets` gate is gone.
- **PD-17 Fingerprint pins.** `agent_prompt_fingerprint` hashes an agent's whole module plus `agents/prompts.py` (`evaluation/config.py`), so any edit to `researcher.py`, `planner.py` or `synthesizer.py` moves a pin. Each phase's integration task re-pins `PINNED_TARGET_PROMPT_FINGERPRINTS` (and `PINNED_JUDGE_PROMPT_FINGERPRINT` if it moved) to the values the merged tree computes, and adds one comment line per re-pin naming the task. A historical test that asserts one past pin value (for example `assert PINNED_TARGET_PROMPT_FINGERPRINTS["researcher"] == "d81af60c3103"`) and fails because of this plan's intended edit is deleted, not re-pinned: it records history, not a contract. The drift-alarm tests (`test_every_target_prompt_fingerprint_is_pinned_against_prompt_drift`, `test_the_judge_fingerprint_is_pinned_beside_the_six_target_pins`) stay. (Listed in the review section.)
- **PD-18 Naming a page's own organisation.** An `own` figure is credited to the organisation the Context Check names only when code confirms the page is that organisation's own: the existing `first_party_host_evidences_issuer`, or a government/education host whose registrable label spells the name (initials or the name run together, a leading "U.S." dropped) while the page names it (`eia.gov` and "U.S. Energy Information Administration"). Otherwise the figure is credited to the host (`woodmac.com`). A lookalike host (`eia.news`) is never credited with the organisation. (Listed in the review section.)
- **PD-19 One home for the shared wording rules.** The hedge, forecast-versus-actual and attested-name rules the Report Writer keeps (§6.2) are also needed by the Evidence Verifier (the kind fallback for an unchecked figure). They move out of `synthesizer.py` into a new `agents/wording.py` in step 3, so the verifier never imports the writer and step 4 can delete `synthesizer.py` whole. (Listed in the review section.)
- **PD-20 Labels are rendered, not stored.** Reader labels (§6.1) are computed by `report.py` from the fact row or figure context fields at render time, by one function (`figure_label`), which the writer's prompt uses too. No label text is stored in state.
- **PD-21 Step 4 deletes last.** In step 4, tasks running in parallel never delete a name that `agents/__init__.py` or `utils/__init__.py` exports, or that `fact_checker.py`, `claim_clusters.py`, `critic.py` or `synthesizer.py` imports; they stop calling it. Task 4.10 deletes those modules, their tests and every name left without a caller, in one sweep. This keeps every package importable while six tasks change it at once.
- **PD-22 The capped pre-flight is the real CLI.** Step 6's pre-flight runs `python -m deep_research` through `scratch/run_live_proof.py` with caps (two sub-topics, no extra pass, a search ceiling), not a hand-wired node chain. `scratch/preflight_downstream.py` is claim-era; it is not reworked and not copied into this worktree.

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
    duplicate_fact_rows: int = Field(default=0, ge=0)
    uncited_settled_points: int = Field(default=0, ge=0)
    unresolved_citations: int = Field(default=0, ge=0)
    untraced_figures: list[str] = Field(default_factory=list)   # "S003: 12 GW"
    refused_sentences: int = Field(default=0, ge=0)
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
def bare_numbers(text: str) -> list[str]: ...
```

### `src/deep_research/agents/evidence_verifier.py` (Tasks 1.2 and 2.1, new)

```python
EVIDENCE_VERIFIER_NAME = "evidence_verifier"
CONTEXT_CHECK_BATCH_SIZE = 15
CONTEXT_CHECK_CONCURRENCY = 4
CONTEXT_PASSAGE_CHARS = 3000

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

def page_owner(read: ReadRecord) -> str: ...
def resolve_attribution(*, proposed: FigureAttribution | None, organisation: str | None,
                        finding: Finding, read: ReadRecord) -> tuple[FigureAttribution, str]: ...
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
def hedge_forecast(text: str, organisation: str) -> str: ...
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
    forecast_as_fact: bool            # the only reason is a forecast stated as fact
    organisation: str | None          # the forecast's organisation, for hedge_forecast
class WrittenReport(ContractModel):   # the agent's result; replaces SynthesizedReport
    markdown: str; evidence_markdown: str; composition: ReportComposition
    statement_count: int; citation_count: int; refused_count: int
def finding_registry(findings: Sequence[Finding], targets: Sequence[EvidenceTarget]) -> list[tuple[str, Finding]]: ...
def writer_messages(task: ReportWriterTask) -> list[ChatMessage]: ...
def check_point(text: str, cited: Sequence[Finding], *, geographies: Sequence[str]) -> PointCheck: ...
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

CLI and API (PD-15): the flag `--max-iterations N` and the request field `max_iterations` keep their names and pass `max_extra_passes=N`. The graph status value `max_iterations` (PD-11) means "extra passes exhausted with required targets still missing".

## File map

| Phase | Create | Modify | Delete |
|---|---|---|---|
| 0 | `scratch/run_live_proof.py` (copy, untracked), `scratch/baseline-*.txt` | — | — |
| 1 | `agents/figures.py`, `agents/evidence_verifier.py`, `tests/evidence_fakes.py`, `tests/test_agents/test_figures.py`, `tests/test_agents/test_evidence_verifier.py`, `scratch/ev_rebuild_audit2.py` | `utils/types.py`, `utils/__init__.py`, `agents/identity.py`, `agents/evidence.py`, `agents/researcher.py`, `agents/planner.py`, `agents/__init__.py`, `e2e_evaluation/replay.py`, `config.yaml`, tests | — |
| 2 | `scratch/ev_verify_audit2.py` | `agents/evidence.py`, `agents/researcher.py`, `agents/evidence_verifier.py`, `agents/__init__.py`, tests | — |
| 3 | `agents/verified_facts.py`, `agents/wording.py`, `agents/report_writer.py`, `tests/test_agents/test_verified_facts.py`, `tests/test_agents/test_wording.py`, `tests/test_agents/test_report_layout.py`, `tests/test_agents/test_report_writer.py`, `scratch/ev_compose_audit2.py` | `agents/report.py`, `agents/synthesizer.py`, `agents/evidence_verifier.py`, `agents/__init__.py`, `tests/test_evaluation/test_config.py` | — |
| 4 | `agents/report_reviewer.py` and `tests/test_agents/test_report_reviewer.py` (git mv), `evaluation/cases/evidence_verifier.py`, `evaluation/cases/report_writer.py` (git mv), `tests/test_evaluation/test_cases_evidence_verifier.py`, `tests/test_evaluation/test_cases_report_writer.py` (git mv), `tests/test_e2e_evaluation/test_replay_doubles.py` | `utils/*`, `config.yaml`, `agents/{quality,report,report_writer,researcher,planner,evidence,identity,prompts,__init__}.py`, `graph/*`, `runtime/*`, `main.py`, `cli.py`, `api/*`, `evaluation/*`, `e2e_evaluation/*`, `README.md`, tests | `agents/fact_checker.py`, `agents/claim_clusters.py`, `agents/critic.py`, `agents/synthesizer.py`, `utils/claims.py`, `evaluation/cases/fact_checker.py`, `evaluation/cases/critic.py`, and their tests |
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

- **R1 One worktree per implementation task.** The controller records `BASE=$(git -C "$W" rev-parse HEAD)` and creates `../ev-<id>` on branch `ev/<id>` (see "How to run things"). The implementer works and commits only there. Harness tasks and gates run in `$W` itself, because `scratch/` is untracked and exists only there.
- **R2 Owned files.** Each task lists the files it owns and edits nothing else. A failure it meets in a file it does not own is reported to the controller (test id and the first lines of the traceback), not fixed.
- **R3 Shared files have one owner per phase** (table below). Parallel tasks import new code from its module (`from deep_research.agents.figures import ...`), never from the `deep_research.agents` package, and do not run `tests/test_imports.py` or `tests/test_evaluation/test_config.py`; the phase's integration task does.
- **R4 Merge on DONE, review in parallel.** When an implementer reports DONE, the controller merges its branch at once (`git -C "$W" merge --no-ff ev/<id> -m "merge: Task N.M"`) so dependent tasks can start, and dispatches the task review on `BASE..ev/<id>` (the skill's `scripts/review-package PLAN_FILE BASE ev/<id>`, run in `$W`). Reviews of wave N run while wave N+1 implements.
- **R5 File lock.** A task's owned files stay locked from its dispatch until its review is clean (or parked at the fix-round cap). A task is dispatched only when every task it depends on is merged and none of its owned files is locked. The wave tables name the few places where this makes a task wait for a review.
- **R6 Fix rounds** run in the task's own worktree (resume the implementer), are committed on its branch, merged again, and re-reviewed on the fix range only. A finding that needs a change in a file another running task owns goes to that owner through `hub`; a finding in a file nobody owns goes to the phase's integration task.
- **R7 No merge during a harness or a gate.** The controller does not merge into `$W` while a harness task or gate command runs there; merges wait for it.
- **R8 Gates are barriers.** A gate runs when every task of its phase is merged and review-clean. No task of the next phase is dispatched before the gate passes (spec §9: "Each step's proof must pass before the next step starts"). A fix merged after a gate passed re-runs that gate's offline commands.
- **R9 Dispatch by readiness.** Waves are the planning view. A task is dispatched as soon as R5 allows, even while other tasks of its wave run.
- **R10 Cleanup.** After a clean review: `git -C "$W" worktree remove "../ev-<id>" && git -C "$W" branch -d "ev/<id>"`.

### Shared-file boundaries

| Shared file | Its single owner in each phase | Contract fixed before the wave |
|---|---|---|
| `src/deep_research/utils/types.py`, `src/deep_research/utils/__init__.py` | 1.1; 4.1 (additions and renames), then 4.10 (removals); 5.1 | "Shared interfaces", `utils/types.py` blocks |
| `tests/evidence_fakes.py` | 1.1; 4.1; 5.1 | the builders in Task 1.1 |
| `src/deep_research/agents/__init__.py` (re-exports) | 1.5; 2.2; 3.5; 4.10; 5.4 | none needed: names are exported after the wave that creates them |
| `tests/test_evaluation/test_config.py` (fingerprint pins) | 1.5; 2.2; 3.5; 4.7 (agent names), then 4.10 (pins); 5.4 | PD-17 |
| `config.yaml`, `src/deep_research/utils/config.py`, `tests/test_config.py` | 1.3; 4.1 | the config block in Task 4.1 |
| Graph wiring: `graph/*.py`, `runtime/assembly.py`, `runtime/__init__.py`, `main.py` | 4.8 | node names, routes, `ReportPublisher`, `run_research` in "Shared interfaces" |
| `src/deep_research/e2e_evaluation/replay.py`, `replay_matrix.py` | 1.3; 4.9, then 4.11; 5.3 | request formats: Task 2.1 (Context Check), Task 3.4 (writer registry lines), Task 4.2 (reviewer) |
| `src/deep_research/agents/report.py` | 3.2; 4.5, then 4.10 | |
| `src/deep_research/agents/researcher.py` | 1.3; 2.1; 4.4, then 4.10; 5.3 | |
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
| 1C | 1.5 Phase-1 integration: exports and pins | sp-implementer | 1.2, 1.3, 1.4 merged | `agents/__init__.py`, `tests/test_evaluation/test_config.py`, and any test file routed to it under R6 | S |
| 1C | 1.6 Step-1 proof harness | sp-implementer | 1.2, 1.3 merged | `scratch/ev_rebuild_audit2.py` | M |
| 2A | 2.1 Context Check and the Evidence Verifier agent | sp-hard-implementer | G1 | `agents/evidence.py`, `agents/researcher.py`, `agents/evidence_verifier.py`, `tests/test_agents/test_evidence_verifier.py`, `tests/test_agents/test_evidence.py`, `tests/test_agents/test_researcher.py`, `tests/test_agents/test_tool_free_prompts.py` | XL |
| 2B | 2.2 Phase-2 integration | sp-implementer | 2.1 merged | `agents/__init__.py`, `tests/test_evaluation/test_config.py` | S |
| 2B | 2.3 Step-2 proof harness | sp-implementer; `--live` by the operator | 2.1 merged | `scratch/ev_verify_audit2.py` | M |
| 3A | 3.1 Verified facts | sp-hard-implementer | G2 | `agents/verified_facts.py`, `tests/test_agents/test_verified_facts.py` | L |
| 3A | 3.2 Reader report and evidence log layout | sp-implementer | G2 | `agents/report.py`, `tests/test_agents/test_report_layout.py` | L |
| 3A | 3.3 Shared wording rules | sp-hard-implementer | G2 | `agents/wording.py`, `agents/synthesizer.py`, `agents/evidence_verifier.py`, `tests/test_agents/test_wording.py` | L |
| 3B | 3.4 The Report Writer agent | sp-hard-implementer | 3.1, 3.2, 3.3 merged | `agents/report_writer.py`, `tests/test_agents/test_report_writer.py` | XL |
| 3C | 3.5 Phase-3 integration | sp-implementer | 3.4 merged | `agents/__init__.py`, `tests/test_evaluation/test_config.py` | S |
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
| 4D | 4.10 Deletion sweep, exports and pins | sp-hard-implementer | 4.2–4.8 merged; waits (R5) for the reviews of 4.2–4.5 and 4.7 | deleted files; `agents/__init__.py`, `utils/types.py`, `utils/__init__.py`, `utils/claims.py`, `agents/prompts.py`, `agents/evidence.py`, `agents/identity.py`; dead names in `agents/{quality,report,report_reviewer,planner,researcher}.py`; `tests/test_imports.py`, `tests/test_types.py`, `tests/test_state.py`, `tests/test_evaluation/test_config.py`, `tests/test_agents/{test_evidence,test_identity,test_prompts,test_report,test_tool_free_prompts,test_native_react_boundary,test_source_evaluator}.py` | L |
| 4D | 4.11 E2E matrix green | sp-hard-implementer | 4.8 and 4.9 merged; waits (R5) for the review of 4.9 | `e2e_evaluation/{cases,evaluators,models,runner,replay,replay_matrix}.py`, `tests/test_e2e_evaluation/*` | L |
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

Parallel slots at the peak: wave 4B runs six implementers beside the review of 4.1, then up to six reviews beside 4.8 and 4.9 (at most 13 agents at once, under the cap of 32).

### Critical path and wall-clock estimate

Assumptions: the sizes above; a task review takes 15 minutes (S, M) or 25 (L, XL); one fix round (fix plus scoped re-review, 25 minutes) lands on each phase's critical path; the full suite takes about 10 minutes (Task 0.1 measures it) and each e2e mode about 15 minutes at three repetitions; off-peak waits are not included.

| Phase | Critical path | Minutes |
|---|---|---|
| 0 | 0.1 | 45 |
| 1 | 1.1 (75) → 1.3 (75; 1.2 and 1.4 beside it) → 1.6 (45; 1.5 beside it) → last review 15 → fix round 25 → G1 25 | 260 |
| 2 | 2.1 (120) → 2.3 (45; 2.2 beside it) → last review 15 → fix round 25 → G2 30 | 235 |
| 3 | 3.1 or 3.3 (75; 3.2 beside them) → 3.4 (120) → 3.6 (75; 3.5 beside it) → last review 25 → fix round 25 → G3 25 | 345 |
| 4 | 4.1 (75) → 4.2 (120; 4.3–4.7 beside it) → 4.8 (120; 4.9 beside it) → 4.11 (75; 4.10 beside it) → last review 25 → fix round 25 → G4 45 | 485 |
| 5 | 5.1 (45) → 5.2 (75; 5.3 beside it) → 5.5 (45; 5.4 beside it) → last review 15 → fix round 25 → G5 25 | 230 |
| 6 | the final review and its fixes (90; 6.1 and 6.2 beside them) → 6.3 (45) → 6.4 (40) | 175 |
| **Total** | | **1,775 ≈ 29.6 hours** |

The same estimates run one task at a time, with no review overlapping any implementation, come to about 3,300 minutes (55 hours). The waves save about 46%. Where time can still go: each extra fix round on a critical-path task adds 25–40 minutes, and a live step that meets a peak window (Mon–Fri 01:00–04:00 and 06:00–10:00 UTC) waits up to 3 hours, so G2, G5, 6.2 and 6.3 should be scheduled off-peak in advance.

## Runtime budget: how one report takes about 30 minutes (45 at most)

Measured baseline (audit-3, `output/live-proof/audit-3/cli.log`, first pass): planner 2 m 30 s, researcher 7 m 45 s (budget 10), source evaluator 1 m 20 s, fact checker 51 m 28 s, synthesizer 4 m 58 s, critic 2 m 19 s, report review 3 m 43 s; the second pass added 1 h 26 m (fact checker 1 h 1 m 28 s); total 2 h 40 m, 86 web calls. The fact checker and the critic go; research gets a bigger budget but a smaller plan.

| Stage | Pass-0 work | Budget | What bounds it |
|---|---|---|---|
| Planner | 1 plan request plus at most 2 plan-review calls | 3 min | `MAX_PLAN_REVIEW_CALLS = 2`; `planner_final_max_tokens` 65,536; five or fewer required targets (step 5) |
| Researcher | the planned sub-topics in priority order, one after another (§12), each ≤ 20 tool calls | 10 min | `agents.tool_budget_overrides.researcher: 20`; a sub-topic stops once its targets have findings; the organisation's own page first means fewer relay reads |
| Source Evaluator | 1–3 batched calls (batch 12, at most 36 sources) | 1.5 min | unchanged |
| Evidence Verifier | Figure Match (milliseconds), then about 30 findings in 2 Context Check batches of 15, run at once | 3 min | batch 15, at most 4 batches at once (PD-12); a truncated batch is asked once more in two halves; a failed batch marks its findings `context_unchecked` and the run goes on |
| Report Writer | 1 draft request; a second only after a truncated one | 4 min | two attempts (the synthesizer's measured ladder); code builds the key facts table, Not found and the labels, so the model writes only prose |
| Report Reviewer | 1 request; a second only after a truncated one | 4 min | `report_review_max_tokens` 65,536; `report_reviewer` timeout 360 s, `retry_count` 1; the evidence-batch follow-up calls are gone (§6.3: one call) |
| Publish | three file writes | seconds | |
| **First pass** | | **≈ 25.5 min** | |
| Extra pass, only when a required target has no verified finding | researcher on the sub-topics that own the missing targets only (usually 1–2), then source evaluator, Evidence Verifier on the new findings only, writer, reviewer | ≈ 13 min | `graph.max_extra_passes: 1`; targeted to the missing target ids (§6.5, §7.2) |
| **With the extra pass** | | **≈ 38.5 min** | ≤ 45 |

Failures never add a pass and never stop a run: a failed Context Check batch leaves its findings citable as "unchecked context"; a failed writer draft still publishes the code-built key facts table, Not found and sources; an unscored review publishes as `partial` with status `incomplete` (PD-13). Task 6.2 prints every stage's duration from the timestamped `cli.log`; the live run starts only if the capped pre-flight's Evidence Verifier, Report Writer and Report Reviewer stages each stayed within 1.5 times their budget above.

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
    figure_in_text,
    parse_figure,
    quantities_in,
    same_quantity,
    unit_dimension,
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

    Skipped: every known-unit quantity, four-digit years, a day beside a
    month name, single-digit labels ("3 states", "Q3"), and ordinals.
    """
    normalised = cosmetic_text(text)
    taken = [(found.start, found.end) for found in quantities_in(text)]
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

- [ ] **Step 8: Budget 20.** In `config.yaml` set `agents.tool_budget_overrides.researcher: 20` and change the comment above it from "…the fact checker keeps the researcher's ten…" to state that the researcher's per-sub-topic budget is 20 (spec §7.2). Leave the other keys for Task 4.1.

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

**Role:** sp-implementer. **Wave:** 1C, parallel with Task 1.6. **Depends on:** Tasks 1.2, 1.3 and 1.4 merged.

**Owns:** `src/deep_research/agents/__init__.py`, `tests/test_evaluation/test_config.py`, and any test file the controller routes here under rule R6 (for example a `test_researcher.py` assertion that pinned case-sensitive excerpt matching).

- [ ] **Step 1: Re-export the phase's public names.** In `agents/__init__.py` add `cosmetic_text` to the `from deep_research.agents.evidence import (...)` block; a new block `from deep_research.agents.figures import (Quantity, bare_numbers, figure_in_text, parse_figure, quantities_in, same_quantity, unit_dimension)`; a new block `from deep_research.agents.evidence_verifier import (EVIDENCE_VERIFIER_NAME, FigureMatch, figure_match, read_text)`; `FindingFigureDraft` in the researcher block; every name in `__all__`.

- [ ] **Step 2: Re-pin the prompt fingerprints (PD-17).** Print the values the merged tree computes:

```bash
"$PY" -c "from deep_research.evaluation.config import agent_prompt_fingerprint as f; from deep_research.evaluation.models import AGENT_NAMES as n; print({a: f(a) for a in n})"
```
Set `PINNED_TARGET_PROMPT_FINGERPRINTS["researcher"]` and `["planner"]` in `tests/test_evaluation/test_config.py` to the printed values (the other four must print unchanged; if one moved, stop and report it), with one comment line: "Evidence Verifier plan, Tasks 1.3 and 1.4: researcher and planner module edits." Delete each historical single-value test that now fails only because the researcher or planner pin moved (PD-17).

- [ ] **Step 3: Apply the routed fixes**, one assertion at a time, each to the §5.1 contract.

- [ ] **Step 4: Run the tests.** Run: `"$PY" -m pytest -q tests/test_imports.py tests/test_evaluation/test_config.py tests/test_agents` and, alone, `"$PY" -m pytest -q tests/test_state.py`. Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add src/deep_research/agents/__init__.py tests/test_evaluation/test_config.py
git commit -m "chore(agents): export phase-1 names; re-pin researcher and planner fingerprints"
```
(Add, by path, any routed test file you changed.)

**Acceptance:** `tests/test_imports.py` and `tests/test_evaluation/test_config.py` pass on the merged Phase-1 tree.

### Task 1.6: Step-1 proof on the audit-2 reads (Gate G1)

**Role:** sp-implementer; runs in `$W` (rule R1). **Wave:** 1C, parallel with Task 1.5. **Depends on:** Tasks 1.2 and 1.3 merged.

**Files:**
- Create (untracked): `scratch/ev_rebuild_audit2.py`

**Interfaces:**
- Produces: `rebuild(state_path: Path = STATE) -> Rebuilt` with `Rebuilt` fields `findings: list[Finding]`, `reads: dict[str, ReadRecord]`, `sources: list[ScoredSource]`, `sub_topics: list[SubTopic]`, `rejected: list[str]`, `rows: list[str]`. Later harnesses (Tasks 2.3, 3.5, 6.1) import it as `from scratch.ev_rebuild_audit2 import rebuild`.

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

### Gate G1 — end of step 1

Run, in `$W`, once every Phase-1 task is merged and review-clean (rule R8):

```bash
"$PY" scratch/ev_rebuild_audit2.py
"$PY" -m pytest -q tests --ignore=tests/test_state.py
"$PY" -m pytest -q tests/test_state.py
"$PY" -m deep_research.e2e_evaluation suite --tier controlled --repetitions 1
"$PY" -c "import deep_research.agents, deep_research.graph, deep_research.runtime, deep_research.api, deep_research.cli, deep_research.main, deep_research.evaluation, deep_research.e2e_evaluation"
```

**Pass condition:** the harness prints `PASS` for P1, P2 and P3 (25/25) and exits 0; both suite invocations are green, or show only the failures recorded in `scratch/baseline-*.txt`; the e2e real-agent suite is accepted (the old pipeline still runs, PD-3). Do not start Phase 2 until G1 passes.

---

## Phase 2 — Evidence Verifier (spec step 2)

Phase goal: a finding's figures come out of the Evidence Verifier with a verified period, scope, attribution and kind, or with a named drop reason; nothing the AI says is kept unless the page carries it. The agent exists and is unit-tested, but the graph does not run it yet (PD-3).

### Task 2.1: The Context Check and the Evidence Verifier agent

**Role:** sp-hard-implementer. **Wave:** 2A, alone. **Depends on:** Gate G1.

**Owns:** `src/deep_research/agents/evidence.py` (move three attribution helpers in; add `relay_attribution_on_page` and `own_organisation_on_page`), `src/deep_research/agents/researcher.py` (delete the moved helpers, lines 928–984; import them from `evidence.py`), `src/deep_research/agents/evidence_verifier.py`, `tests/test_agents/test_evidence_verifier.py`, `tests/test_agents/test_evidence.py`, `tests/test_agents/test_researcher.py`, `tests/test_agents/test_tool_free_prompts.py`.

**Interfaces:**
- Consumes: `figure_match`, `read_text`, `figure_in_text` (Task 1.2); the verification types (Task 1.1); `first_party_host_evidences_issuer`, `_issuer_name_pattern`, `_institutional_domain_label`, `_identity_words`, `_document_text` (existing, `evidence.py` lines 1031, 896, 927); `publisher_identity` (`agents/sources.py`); `finding_fingerprint`, `deduplicate_findings` (`agents/identity.py`); `_stated_role` (`agents/synthesizer.py` line 3356; Task 3.3 moves it to `agents/wording.py` as `stated_role` and updates this import); `render_structured_reply_format` (`agents/prompts.py` line 73); `agent_error` (`agents/errors.py`); `agent_event` (`agents/events.py`).
- Produces: everything listed for `evidence_verifier.py` and the Task 2.1 lines of `evidence.py` in "Shared interfaces". Nothing is re-exported here (Task 2.2 does it).

**Attribution resolution (PD-8)** — `resolve_attribution` returns the first row that applies:

| Context Check proposed | Code checks | Result |
|---|---|---|
| `relayed`, organisation X | X is the page's own publisher (`first_party_host_evidences_issuer(read, X)`) | `own`, X |
| `relayed`, organisation X | X is named in the snippet's passage beside an attribution cue (`relay_attribution_on_page`) | `relayed`, X |
| anything | the researcher admitted `attributed_issuer` Y (its quote is on the page) | `own` Y if Y is the page's own publisher, else `relayed` Y |
| `own`, organisation X | X is the page's own publisher | `own`, X |
| `own` with X unverified, or no reply at all | — | `own`, the page owner (`publisher_identity(read.resolved_url)`, e.g. `eia.gov`) |
| `unattributed`, or `relayed` with X unverified | — | `unattributed`, the page owner |

Wherever this table says "the page's own publisher", the check is `own_organisation_on_page(read, X)` (PD-18), which Step 3 adds to `evidence.py`; in the `resolve_attribution` code below, call it wherever the code shows `first_party_host_evidences_issuer`:

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
    figure_match,
    resolve_attribution,
    verify_finding,
)

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
                               finding=finding, read=read) == ("relayed", "Wood Mackenzie")
    assert resolve_attribution(proposed="relayed", organisation="BloombergNEF",
                               finding=finding, read=read) == ("unattributed", "utilitydive.com")


def test_an_admitted_attribution_makes_a_relay() -> None:
    read = make_read(RELAY_PAGE, url=RELAY_URL, title="Storage in 2025 | Utility Dive")
    finding = make_finding(read, "utility-scale installations reached 16 GW in 2025.",
                           attributed_issuer="Wood Mackenzie",
                           attribution_quote="According to Wood Mackenzie")
    assert resolve_attribution(proposed="unattributed", organisation=None,
                               finding=finding, read=read) == ("relayed", "Wood Mackenzie")
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

- [ ] **Step 3: Move the attribution helpers.** Move `_neighbouring_passage_text`, `_ATTRIBUTION_CUE_PATTERN`, `_POSSESSIVE_MARK`, `_ATTRIBUTION_CUE_REACH` and `_attribution_cue_adjacent` (with their comments) from `researcher.py` to `evidence.py`, renaming the public three to `neighbouring_passage_text`, `ATTRIBUTION_CUE_PATTERN` and `attribution_cue_adjacent`. In `researcher.py` import those three from `deep_research.agents.evidence` and update the two call sites in `_admitted_attribution`. Then add to `evidence.py`:

```python
def relay_attribution_on_page(read: ReadRecord, locator: str, organisation: str) -> bool:
    """True when the passage around ``locator`` credits ``organisation`` for a figure.

    The same rule a researcher attribution quote is admitted under: the name is
    in the snippet's own passage or its immediate neighbour, with an attribution
    cue ("according to", "reported by", a possessive, ...) beside it.
    """
    passage = neighbouring_passage_text(read, locator)
    name = organisation.strip()
    if not passage or not name:
        return False
    pattern = re.compile(_issuer_name_pattern(name), re.IGNORECASE)
    return any(attribution_cue_adjacent(passage, match) for match in pattern.finditer(passage))
```

- [ ] **Step 4: Implement the Context Check** in `evidence_verifier.py` (append below `figure_match`). Imports: `asyncio`, `dataclasses.replace`, `Literal`, `Sequence`, `ValidationError` (pydantic), `ProviderError`, `ProviderOutputLimitError`, `StructuredOutputError` (from `deep_research.providers`), `ChatMessage`, `render_structured_reply_format`, `BaseAgent`, `AgentRun`, `AgentTask`, `ReActRun`, `agent_error`, `agent_event`, the types, `finding_fingerprint`, `deduplicate_findings`, `publisher_identity`, `cosmetic_text`, `neighbouring_passage_text`, `first_party_host_evidences_issuer`, `relay_attribution_on_page`, and `_stated_role` from `deep_research.agents.synthesizer`.

```python
CONTEXT_CHECK_BATCH_SIZE = 15
CONTEXT_CHECK_CONCURRENCY = 4
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


def resolve_attribution(
    *,
    proposed: FigureAttribution | None,
    organisation: str | None,
    finding: Finding,
    read: ReadRecord,
) -> tuple[FigureAttribution, str]:
    """PD-8: the Context Check proposes, the page's own words decide."""
    name = (organisation or "").strip()
    if proposed == "relayed" and name:
        if first_party_host_evidences_issuer(read, name):
            return "own", name
        if relay_attribution_on_page(read, finding.locator or "", name):
            return "relayed", name
    issuer = finding.attributed_issuer
    if issuer:
        if first_party_host_evidences_issuer(read, issuer):
            return "own", issuer
        return "relayed", issuer
    if proposed == "own" and name and first_party_host_evidences_issuer(read, name):
        return "own", name
    if proposed in (None, "own"):
        return "own", page_owner(read)
    return "unattributed", page_owner(read)


def unchecked_context(finding: Finding, figure: FindingFigure, read: ReadRecord) -> FigureContext:
    """The recorded fields, used when no Context Check reply exists for a figure."""
    attribution, organisation = resolve_attribution(
        proposed=None, organisation=None, finding=finding, read=read
    )
    kind = figure.kind or (
        "forecast" if _stated_role(finding.snippet or "") == "forecast" else "actual"
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
        finding=finding, read=item.read,
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
                         context=unchecked_context(item.finding, figure, item.read))
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
            judged = await self.verify(pending, state.read_records, errors)
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
                     errors: list[ResearchError]) -> list[Finding]:
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
                                         match=match))
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

- [ ] **Step 1: Re-export** every new public name of `evidence_verifier.py` (`CONTEXT_CHECK_BATCH_SIZE`, `CONTEXT_CHECK_CONCURRENCY`, `CONTEXT_PASSAGE_CHARS`, `CONTEXT_CHECK_SYSTEM_PROMPT`, `CONTEXT_CHECK_INSTRUCTION`, `FigureCheckDraft`, `ContextCheckDraft`, `ContextItem`, `VerifiedFindings`, `EvidenceVerifierAgent`, `page_owner`, `context_passage`, `resolve_attribution`, `unchecked_context`, `verify_finding`, `context_check_messages`, `context_check_failed_error`, `evidence_verified_event`) and of `evidence.py` (`neighbouring_passage_text`, `ATTRIBUTION_CUE_PATTERN`, `attribution_cue_adjacent`, `relay_attribution_on_page`, `own_organisation_on_page`) from `agents/__init__.py` and `__all__`.
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
  - `--scripted` (default, offline): for every figure-bearing rebuilt finding, build a `ContextItem` with the production `figure_match` and `context_passage`, and a scripted reply per figure that confirms the recorded fields (period = recorded, scope = recorded, attribution `relayed` with the recorded `attributed_issuer` when there is one, else `own` with no organisation, kind = recorded, `evidence_words` = the snippet, verdict `confirm`). Three overrides exercise enforcement on real pages:
    1. every figure whose value is `18.9`: `scope` set to the first of `"utility, C&I, and residential"` or `"all segments"` that the finding's own passage contains (search `item.passage` with `excerpt_matches`), verdict `correct`, `evidence_words` = the snippet;
    2. the first finding from `enerknol.com`: `evidence_words` replaced by `"EnerKnol confirms 10.4 GW of grid-scale storage"` (not on the page);
    3. the first `forecast` figure from `eia.gov`: `period` set to `"2026"`, verdict `correct` (the page does not say 2026 for it).
    Run the production `verify_finding` over each item and attach the verification to its finding.
  - `--live` (operator, off-peak): build `state = ResearchState(session_id="ev-proof-audit2", original_question=QUESTION, raw_findings=rebuilt.findings, read_records=rebuilt.reads)`; build the agent directly (it is not in the runtime assembly until Task 4.8): `tracker = Tracker(LangSmithRuntimeConfig(tracing_enabled=False, project="ev-proof", api_key=None))`, `settings = load_settings("config.yaml")`, `provider = build_chat_provider(settings.llm, tracker, request_budget=RequestBudget(settings.request_budget))`, `agent = EvidenceVerifierAgent(provider=provider, tracker=tracker, scratchpad=ScratchpadMemory(session_id=state.session_id, agent_name="evidence_verifier", max_entries=20), tools=[], config=settings.agents, model_profile=settings.llm.resolve_for("evidence_verifier"))` (imports: `deep_research.main.load_settings`, `deep_research.observability.{LangSmithRuntimeConfig, Tracker}`, `deep_research.providers.build_chat_provider`, `deep_research.request_budget.RequestBudget`, `deep_research.memory.scratchpad.ScratchpadMemory`); run `run = await agent.run(state)` inside `async with tracker.session_span(state.session_id, state.original_question):` and take `run.state_update["verified_findings"]`.
  - Print one line per 18.9 GW figure (status, kept, scope, attribution, organisation) and the counts by status and by drop reason. Then the checks:
    - **V1** every kept figure with value `18.9` has a scope whose `_measure_scale(scope)` contains `"all"`, or contains both `"residential"` and `"commercial"`; and at least one such figure is kept;
    - **V2** for every kept figure, `excerpt_matches(read_text(read), evidence_words)` is true (re-checked here, independent of the verifier) or `evidence_words` is `None` and the finding is `context_unchecked`;
    - **V3** (`--scripted` only) the enerknol figure is dropped with `evidence_not_on_page` and the eia.gov forecast figure with `correction_not_on_page`.
  - Exit 0 only when every applicable check prints `PASS`.

- [ ] **Step 2: Run offline.** Run: `"$PY" scratch/ev_verify_audit2.py --scripted`. Expected: `PASS V1`, `PASS V2`, `PASS V3`, exit 0.

- [ ] **Step 3: Run live (operator, off-peak).** Run the OFF-PEAK CHECK; if it prints `OK`, run `"$PY" scratch/ev_verify_audit2.py --live`. Expected: `PASS V1`, `PASS V2`, at most two `ContextCheckDraft` calls plus at most two half-batch re-asks, under 5 minutes. If V1 fails (the model kept grid-scale), stop: the Context Check prompt or enforcement needs work before Phase 3. Report the printed 18.9 GW lines to the controller.

- [ ] **Step 4: No commit** (scratch).

### Gate G2 — end of step 2

Run in `$W` once Tasks 2.1–2.3 are merged and review-clean (rule R8):

```bash
"$PY" -m pytest -q tests/test_agents/test_evidence_verifier.py tests/test_agents/test_evidence.py tests/test_agents/test_figures.py tests/test_agents/test_researcher.py tests/test_types.py tests/test_imports.py
"$PY" scratch/ev_verify_audit2.py --scripted
"$PY" scratch/ev_verify_audit2.py --live      # operator, off-peak only
```

**Pass condition:** the tests pass (they include a unit test for every normalisation case of §5.1 and every enforcement rule of §5.2, including invented `evidence_words` rejected and an unsupported correction dropped); the scripted run prints PASS for V1–V3; the live run prints PASS for V1 and V2 (audit-2's 18.9 GW labelled all-segment by the real Context Check). Do not start Phase 3 until G2 passes.

---

## Phases 3–6 — outline (step-by-step bodies not yet written)

> **Status of this plan revision.** Everything above this line is complete and executable. For Phases 3–6, the task list, order, roles, owned files (wave table), interfaces ("Shared interfaces") and proofs below are decided and final, but the step-by-step TDD bodies (test code, implementation code, exact commands) are **not yet written**. Write each body in the form of Tasks 1.1–2.3 before dispatching it; never dispatch an implementer from this outline alone. Phases 1–2 can be executed now; the remaining bodies are the next planning pass.

### Phase 3 (spec step 3): the Report Writer on verified findings

- **3.1 Verified facts** (`agents/verified_facts.py`, signatures in "Shared interfaces"). Citable means status is not `dropped`. `same_organisation`: identical identity words once a leading "U.S."/country word is dropped; or one side is a single token (a host label on a government, education, `.com` or `.org` host, or an acronym) equal to the other side's initials (country word and a trailing legal-form word dropped; an all-capitals word contributes itself whole), to its words run together, or (four letters or more) to their prefix. So EIA, "U.S. Energy Information Administration" and eia.gov are one organisation, Wood Mackenzie and woodmac.com are one, BloombergNEF and bnef.com are one, and eia.news is never EIA. `same_period`: equal after `cosmetic_text`, or both reduce to one bare year. `finding_answers`: §6.6 plus PD-7. `fact_rows`: §5.3 and PD-9 (same organisation, unit dimension, period, kind and value is one row; the own page cites ahead of a relay; a revision folds two rows answering the same target with differing release keys, the latest kept and the earlier listed). `not_found_targets`: required targets with no answer, with the owning sub-topic's queries and the acquisition state's attempted and read URLs (keyed by `coverage_id`). `untraced_numbers`: every quantity and bare number in a sentence must equal a kept figure of a cited finding. Tests cover each organisation pair above, the 10.4 GW / 10.3 GW revision, own-plus-relay folding into one row, forecast and actual never merging, two organisations staying two rows, and "12 GW" untraced while "10,400 MW" traces to 10.4 GW.
- **3.2 Layout** (`agents/report.py`): `figure_label` per §6.1; `render_written_report` with the header (question, as-of, scope, counts), executive summary, key facts table (Organisation, Measure, Period, Value, Kind, Scope, Release or edition, Source), findings sections, Not found (question, queries, pages read), and sources (only those cited, in first-use order), each statement ending with its figures' labels; `render_finding_log` with every finding's snippet, read, locator and status, every figure kept or dropped with its reason and evidence words, and every refused sentence in full. A test asserts that no verdict or corroboration word appears.
- **3.3 Wording rules** (`agents/wording.py`, PD-19): move the helpers named in "Shared interfaces" out of `synthesizer.py` unchanged (`synthesizer.py` imports them back until Task 4.10 deletes it; `evidence_verifier.py` switches to `stated_role`). Add `unattested_names`, `stated_years`, `SCOPE_TERMS` (grid-scale, utility-scale, front-of-the-meter, behind-the-meter, residential, commercial, industrial, C&I, commercial and industrial, distributed, community, all segments, all sectors) with `stated_scopes`, and `hedge_forecast` (a past-tense outcome verb becomes "is/are expected to be …"; ", according to <organisation>'s forecast" is appended when no forecast marker remains). `_realized_outcome` stops counting a verb after "to be". Pinned by `test_hedge_forecast_makes_a_forecast_read_as_one` ("EIA's outlook adds 14 GW in 2025." and "In 2025, 18.2 GW was added.").
- **3.4 Report Writer** (`agents/report_writer.py`): the draft schemas; `finding_registry` (labels F01… over citable findings, answers to required targets first); `writer_messages`, whose registry lines have the fixed form `F01 | figure 1: 10.4 GW | period 2024 | kind actual | organisation <name> | label: <figure_label>` (Task 4.9's replay double parses it). The writing rules: cite labels only; use only numbers from the cited figures; state forecasts with organisation and release; name both parties of a relay; summary order is the 2024 actual, then each organisation's latest 2025 forecast, then 2025 actuals; no verdict words. `check_point` checks numbers (`untraced_numbers`); names (`unattested_names` over the cited snippets, evidence words, verified organisations, periods, scopes, releases, titles and plan geographies); years; scope (a scope term must appear in the verified scope or the evidence words of a figure the sentence states); kind, clause by clause (`stated_role` against the figure's verified kind); and `hardened_modality`. In `compose_written_report`, a refused sentence becomes a `RejectedDraftPoint` with its full text, labels and reason. A sentence refused only for stating a forecast as fact is rewritten once with `hedge_forecast` and checked again. A summary point that restates only facts already stated is dropped as a duplicate. The agent also builds the statements, fact rows, Not found list and labels. `ReportWriterAgent` has the two-attempt ladder, `WrittenReport`, `publish_document`, `publish_finding` and `finding_memory_payload`. Pinned by `test_forecast_stated_as_fact_is_rewritten_once_and_kept`, `test_grid_scale_wording_on_an_all_segment_figure_is_refused` and `test_a_failed_draft_still_composes_the_key_facts`.
- **3.5 Integration:** exports; re-pin the synthesizer fingerprint (PD-17).
- **3.6 Harness** `scratch/ev_compose_audit2.py`: `rebuild()` → `verify_scripted()` → a scripted draft built from real audit-2 findings (EIA's 10.4 GW 2024 actual; the January 2025 STEO's 14 GW forecast relayed by ent.news; Wood Mackenzie's Q1 2025 forecast of 15 GW / 49 GWh; "EIA's outlook adds 14 GW in 2025"; one "18.9 GW of grid-scale storage" sentence; one duplicate restatement) → compose → render. `--live` asks the real writer (off-peak). Checks: C1, the summary states 10.4 GW labelled actual with an EIA organisation and a release. C2, at least two summary lines state 2025 forecasts from two organisations, each labelled "forecast (<release>)". C3, the "adds 14 GW" sentence is rewritten and kept. C4, the grid-scale sentence is refused with a scope reason. C5, no duplicate figure line (in the summary or the key facts). C6, no verdict wording. C7, nothing untraced. `--live` must pass C1, C2, C5, C6 and C7.
- **Gate G3:** the four new test files, the full suite (the old pipeline is still green, PD-3), and the harness: `--scripted` passes C1–C7, `--live` passes C1, C2, C5, C6 and C7. Spec §9 step 3's proof: the summary carries the 2024 actual and at least two 2025 forecasts with organisation and release; there is no duplicate figure line and no verdict wording.

### Phase 4 (spec step 4): the cutover (tasks 4.1–4.11 as in the wave table)

- **4.1** Adds and renames the step-4 types. `utils/config.py`: `PRODUCTION_AGENT_NAMES` = planner, researcher, source_evaluator, evidence_verifier, report_writer; `SERVICE_ROLE_NAMES = ("report_reviewer",)`; `GraphConfig.max_extra_passes = 1` (env `GRAPH_MAX_EXTRA_PASSES`); `claim_batch_size`, `claim_batches_per_pass`, `critic_review_max_tokens`, `claim_verification_max_tokens` and their env keys are removed. `config.yaml`: overrides planner `max`, researcher `high`, source_evaluator `high`, evidence_verifier `high` (§5.2), report_writer `max`, report_reviewer `max` with timeout 360 and retry_count 1; `tool_budget_overrides` for planner 1, researcher 20, source_evaluator 0, evidence_verifier 0, report_writer 0; `graph.max_extra_passes: 1`. Also: `git mv` of `report_review.py` to `report_reviewer.py` with every import line updated, `REPORT_JUDGE_ROLE` renamed to `REPORT_REVIEWER_ROLE = "report_reviewer"`, and the three filename helpers moved into `report_writer.py`. Acceptance: IMPORT SMOKE for agents, graph, runtime, api, cli and main.
- **4.2 Report Reviewer.** One call. The packet holds the statements with their cited findings' snippets and labels, the key facts, Not found, and the gate results. Dispositions are supported, unsupported or not_reviewed; an unsupported statement becomes a material derived defect. A truncated reply is asked once more at high effort; a provider failure gives `provider_failed`; missing dispositions give `incomplete`. No critic imports. The node, not the model, stamps `missing_required_target_ids` (PD-5).
- **4.3 Quality.** `compute_report_quality(state, composition)` with the PD-10 gates and the new snapshot fields. Missing targets are the required targets no finding answers (`verified_facts`); unaccounted targets are the missing ones absent from Not found.
- **4.4 Researcher and planner.** The first pass runs every planned sub-topic in priority order. An extra pass runs only the sub-topics that own `state.extra_pass_target_ids`, and shows only those targets. The critic-driven selection helpers are deleted. The planner drops its `claim_clusters` import, the three dimension-validation blocks, `support_policy` (draft field, prompt paragraph and examples) and the `extend_plan` path.
- **4.5 Quality record.** `render_quality_json` carries the new snapshot, the review, the findings with their verification, the fact rows, Not found, the statements, and the refused sentences in full. The quality contract version is bumped.
- **4.6 Outcome, API, CLI, README.** Coverage becomes required, answered, missing and not found. Evidence counts cover reads, sources, and findings verified, corrected, dropped, unchecked and cited. The summary lines are rewritten: no critic score and no claim lines; the review mean is shown; the integrity line counts duplicate fact rows, uncited statements and untraced figures. `--max-iterations` accepts 0 and its help says "extra research passes for missing required targets". Progress events cover the new nodes, and the README sections are rewritten.
- **4.7 Evaluation.** `AGENT_NAMES` becomes planner, researcher, source_evaluator, evidence_verifier, report_writer. The fact_checker and critic cases, gates, scenarios, conftest outputs and tests are deleted. `cases/report_writer.py` is the `git mv` of the synthesizer cases, reworked to verified findings. A new `cases/evidence_verifier.py` holds the case count that `cases/__init__.py` enforces (scope correction, relay, invented evidence words; the live case uses the benchmark's EIA and Wood Mackenzie pages), with the gates `verification_recorded`, `no_invented_evidence` and `drop_reasons_named`.
- **4.8 Graph and runtime,** with the contract in "Shared interfaces". `graph_route`: halted → end. A missing required target with `iteration < max_extra_passes` → extra_pass (`extra_pass_requested`). A missing target with no pass left → finalize (`extra_passes_exhausted`, status `max_iterations`). An unscored review → finalize (`review_unavailable`, `incomplete`). Clear gates and a passing review → finalize (`report_accepted`, `completed`). Otherwise → finalize (`report_not_accepted`, `incomplete`). Quality is `accepted` only on `report_accepted`. The writer node runs `compute_report_quality`; the reviewer node stamps `missing_required_target_ids`; the extra-pass node sets `extra_pass_target_ids` and advances the iteration. Finalize renders `render_written_report`, `render_finding_log` and `render_quality_json`, and saves cited findings to memory only when the report is accepted. The critic, fact-checker, refine and repair machinery is deleted. Pinned by `test_run_publishes_when_the_context_check_fails` and `test_extra_pass_that_finds_nothing_publishes_with_not_found`.
- **4.9 E2E doubles and cases.** The `ClaimsDraft`, `ClaimVerdictDraft`, `ClaimEquivalenceDraft`, `CritiqueDraft` and `ReportDraft` doubles are deleted. New doubles: `ContextCheckDraft` (confirms by default, with per-source overrides), `ReportWriterDraft` (one point per registry figure, from the fixed line format) and the reviewer draft. `REPLAY_CASE_MANIFEST` is reworked. same-work-mirror, primary-attribution, current-versus-forecast and reopen-unanswered-target become Evidence Verifier cases; semantic-duplicate-claims and late-contradiction are retired with stated reasons. New rows: relay-labelled-as-relay, figure-not-on-page-dropped, evidence-words-not-on-page-rejected, scope-corrected-to-all-segments, revision-noted, forecast-versus-actual-kept-apart and extra-pass-finds-nothing. The two coverage-gate graph-only rows are retired (PD-14).
- **4.10 Deletion sweep.** Deletes the four modules, `utils/claims.py`, their tests and every dead name, and cleans up the types. Updates the exports and `test_imports.py`, re-pins the fingerprints (PD-17), and runs the acceptance grep outside e2e.
- **4.11 E2E matrix.** Reworks the models, evaluators, runner and graph-historical cases. Both matrix modes are green at three repetitions, and the acceptance grep passes over e2e.
- **Gate G4:** both full-suite invocations, both e2e modes at three repetitions, IMPORT SMOKE and the acceptance grep over everything, all green (spec §9 step 4).

### Phase 5 (spec step 5): the planner's floor

- **5.1** Final `EvidenceTarget` fields: `required_dimensions` and `critical` are removed and `measure` is required; `make_target` loses its legacy branch.
- **5.2** `PLAN_INSTRUCTION` follows §7.1. One target per organisation, measure, period and kind the question asks for. `required` only for what the question names. Self-added targets (MWh for a capacity question, facility types, definitions) and paywalled-only issuers are optional. About five targets for the benchmark, with the temporal contract kept. `apply_answer_contract` drops the answer-form and evidence-period boilerplate, and a target with no measure is a plan problem the existing repair loop sees.
- **5.3** Updates the consumers: the researcher's unit mentions come from `unit_dimension`; the reviewer's target view; the evaluation planner cases and gates (`targets_have_measure` replaces the dimension gates); the e2e planner double and matrix topics.
- **5.4** Re-pins the fingerprints.
- **5.5** `scratch/ev_plan_probe.py --live` (off-peak, about 3 minutes) runs the real planner on the benchmark question.
- **Gate G5:** the probe's checks pass. At most five required targets; no required energy (MWh) target; no required facility-type or definition target; the required targets include EIA's 2024 actual and at least two organisations' 2025 forecasts.

### Phase 6 (spec step 6): the live proof

- **6.1** `scratch/run_live_proof.py` gains two labels, with per-label arguments and environment recorded in `run.env`. `ev-preflight`: the benchmark question, `--max-iterations 0`, `AGENTS_MAX_SUB_TOPICS=2` and `--request-tavily-attempt-ceiling 12`. `ev-1`: the benchmark question on the default config.
- **6.2** The operator runs `ev-preflight` off-peak. Pass: exit 0 or 4; report, evidence log and quality JSON written; every stage within 1.5 times its budget.
- **6.3** The operator runs `ev-1` off-peak, never hard-stopped.
- **6.4** A fresh read-only reviewer audits `output/live-proof/ev-1/` against spec §10: wall time at most 45 minutes; the summary answers both halves with releases; every number is traced; relays are labelled; no wrong scope, period or kind; the reviewer accepted with no gate failure; rated GREAT.

---

## Over-engineering review: decisions for the user

Each item goes beyond the spec's letter, or is a choice the spec leaves open. Each carries a recommendation. None is silently included: the plan does what the "Plan" column says until you decide otherwise.

| # | Item | Plan | Recommendation and trade-off |
|---|---|---|---|
| 1 | Per-task git worktrees and merge-on-DONE (R1, R4) | included | Keep. This is what makes the parallel waves safe. The alternative, one shared tree, saves the merges but gives phantom test failures from siblings' half-edits and review diffs polluted by other tasks' commits. |
| 2 | New module `agents/wording.py` (PD-19) | included | Keep. The verifier and the writer both need `stated_role`. The alternative, the verifier importing from the writer, couples two stages. |
| 3 | Naming an agency's own page from its host (PD-18) | included | Keep. Without it, EIA's own 10.4 GW is labelled "eia.gov's own figure", and §10's "EIA's 2024 figure" reads worse. The risk is small: only government and education hosts qualify, and the page must name the organisation. |
| 4 | "source:" as an attribution cue (Task 2.1) | included | Keep. The EIA STEO read is a mirror on ent.news that credits EIA in a source line. Without the cue, that forecast loses its EIA attribution and cannot answer the "EIA forecast" target. |
| 5 | A truncated Context Check batch is asked once more in two halves | included | Keep. It is bounded (two extra calls at most), and truncation is the likely failure. The alternative, going straight to `context_unchecked`, is simpler but labels up to 15 findings "unchecked context". |
| 6 | Missing required targets computed by code, not by the reviewer (PD-5) | included | Keep. The spec lists them as reviewer output; computing them from fields (§6.6) makes the extra pass deterministic and immune to a reviewer outage. |
| 7 | The writer cites labels only, so there is no URL guard (PD-6) | included | Keep. A URL the findings do not carry cannot occur. |
| 8 | Keep and rework the graph-historical e2e harness (PD-14) | included | Retire it, if you agree. It is a regression guard for scripted six-agent doubles; the real-agent matrix covers the new pipeline. Retiring it saves about an hour in Task 4.11 and all future upkeep. |
| 9 | Keep the names `--max-iterations` and API `max_iterations` (PD-15) | included | Keep. Existing invocations keep working. Renaming to `--max-extra-passes` would be cleaner but breaks callers, and D6 allows no alias. |
| 10 | Delete historical single-value fingerprint tests when they break (PD-17) | included | Delete. They pin history, not behaviour; the drift-alarm pins stay. |
| 11 | A wall-clock guard that skips the extra pass after a slow first pass | not included | Do not add, unless the pre-flight shows the first pass over 30 minutes. |
| 12 | A code cap of five required targets | not included | Do not add. §7.1 relies on the planner, and Gate G5 plus the pre-flight check it. |
| 13 | An `evidence_verifier` timeout or retry override in `config.yaml` | not included | Add only if the pre-flight shows a batch taking more than 4 minutes. |
| 14 | The e2e row `extra-pass-finds-nothing`, beyond the spec's six Evidence Verifier cases | included | Keep. It pins Review Focus item 3. |
