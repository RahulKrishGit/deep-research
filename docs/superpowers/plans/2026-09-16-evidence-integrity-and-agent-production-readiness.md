# Evidence Integrity and Agent Production Readiness Implementation Plan

**Plan date:** 2026-09-16

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every agent in the six-stage research graph production-reliable and make the CLI publish concise reports whose settled, load-bearing claims are traceably supported by two genuinely independent, read-bearing works, while preserving honest partial output when that bar cannot be met.

**Architecture:** Replace the current URL-oriented evidence handoff with a typed evidence spine: reads produce provenance-bearing evidence units; sources receive canonical publisher, work, and role identities; claims are atomized and semantically clustered; the Fact Checker adjudicates the union of upstream and newly read evidence; the Synthesizer and Critic consume the same canonical claim/evidence graph. Add deterministic acquisition policies, targeted refinement, no-progress termination, production quality gates, and a quieter but more diagnostic CLI. Provider models may classify and judge, but local code owns identity, eligibility, counting, routing, and release decisions.

**Tech Stack:** Python 3.11+, Pydantic v2, asyncio, LangGraph, DeepSeek/OpenAI structured completion, HTTPX, BeautifulSoup, pdfplumber, pytest/pytest-asyncio, Ruff, LangSmith evaluation, Markdown CLI artifacts.

**Supersedes / builds on:** `docs/superpowers/plans/2026-09-14-cli-report-quality-and-agent-output-integrity.md`, unfinished Tasks 16–21 of `docs/superpowers/plans/2026-09-15-cli-live-canary-amendment.md`, `docs/superpowers/specs/2026-09-15-production-ready-reports-design.md`, and `docs/superpowers/validation/2026-09-15-verification-boundary-diagnosis.md`.

## Global Constraints

- Execute only on branch `codex/cross-agent-planner-fix-parity`, starting from reviewed baseline `935cc9e09dc4f5659d0c3acd44f40236e76bab79` or a direct descendant containing no unreviewed application changes.
- This plan succeeds the unfinished generic Tasks 16–21 in `docs/superpowers/plans/2026-09-15-cli-live-canary-amendment.md`. Earlier completed tasks and their immutable validation records remain historical evidence; do not rewrite them.
- Preserve the unrelated untracked `.deepseek-runs/` and `tools/` paths. Never stage them, lint them as part of a task gate, or delete them.
- Use `gpt-5.6-luna` with `max` reasoning and the host's fast/priority execution mode for every implementation dispatch and every fresh task review. One implementer owns one task until its review is clean.
- Implement sequentially. Tasks 1–10 modify shared contracts and graph state, so parallel edits would create false review failures and merge ambiguity.
- Follow RED → GREEN → REFACTOR. Each behavioral change begins with a focused failing test, captures the failure, makes the smallest production change, and reruns the focused test before broader gates.
- All tests through Task 11 are network-zero and fake-driven. Do not call DeepSeek, OpenAI, Tavily, LangSmith, or a public website during those tasks.
- Task 12 is the only paid/live phase. Before every live run, write and commit a predeclaration with the exact question, candidate SHA, ceilings, acceptance criteria, and stop rule, then obtain the user's explicit confirmation.
- Never print, persist, diff, or include `.env` values. Validation records name environment-variable keys and provider names only.
- Stage exact task paths. Before every commit, inspect `git status --short`, `git diff --check`, and `git diff --cached --stat`.
- Every task ends in this order: focused and regression gates pass; implementation commit is created; a fresh Luna Max fast reviewer checks specification compliance and code quality; review findings are fixed and re-reviewed; only then push with `git push origin codex/cross-agent-planner-fix-parity`.
- A task with unresolved Important or Critical review findings is not complete and must not be pushed. A review-only comment with no code change is recorded in the task log but does not require an empty commit.
- Keep `--require-quality` strict: exit `4` unless terminal quality is accepted. Do not lower critic threshold `7`, coverage threshold `0.80`, or any evidence-integrity gate to make a run pass.
- Legitimate acquisition only: obey robots policy and access controls. A denied HTML page may trigger discovery of an official document, API, repository copy, or independent source; it must never trigger evasion, cookie theft, CAPTCHA bypass, proxy rotation, or identity spoofing.
- Holdout ground-truth sources remain unseeded in readiness runs. A separately labeled seeded diagnostic is allowed only to distinguish acquisition failure from reasoning failure and never counts as release evidence.
- Do not claim production readiness from a single battery-storage question. Final release requires the controlled matrix and blind live matrix in Tasks 11–12.

---

## Evidence Review and Causal Diagnosis

### What the latest branch evidence establishes

The latest run on session `417fa9338e10450784b459f89af98b1c` improved the critic score to `5/10`, but terminal quality remained `partial` with only `4/6` planned topics covered. It consumed about `815,664` tokens. The trace attributed `157` searches and only `20` page/document reads to the Researcher, `121` searches and `58` reads to the Fact Checker, and `29` searches with no read-capable tool to the Critic. The reader report carried `16` checked claims, `5` marked verified, and multiple semantically repeated queue/report-cutoff facts. Its ranked constraint table used `not stated` for every deployment mechanism, exposed internal claim IDs in reader-facing prose, and cited origin URLs without reliably exposing the independent verification works.

The offline suite at baseline was strong (`3098 passed, 1 deselected`) and tracked `src`/`tests` were Ruff-clean. That proves contract regression coverage is broad; it does **not** prove the new production semantics because the current fixtures script provider verdicts and do not require upstream evidence content, canonical work independence, semantic claim uniqueness, or read-bearing critic inputs.

Prompt-only corroboration changes, publisher-based retention, larger read opportunity, and the first upstream-URL pooling change each produced either no critic improvement or only a small improvement. The evidence therefore rejects “add another prompt sentence” and “raise the budget again” as primary fixes. It supports repairing the evidence boundary and acquisition control flow.

This diagnosis incorporates the latest relevant commits: `0391c57` changed Researcher retention to publisher-oriented diversity; `3441ca2` made Planner success criteria require corroboration; `2bc6665` broadened Fact Checker passage URL admission; and `935cc9e` predeclared the evidence-pooling run. In current code, `valid_verification_passages` admits upstream URLs, but `claim_verification_messages` renders only the current verification loop's evidence and `verify_claim` exits before adjudication unless that loop retrieved a new independent domain. That mismatch is an observed boundary defect; whether the Researcher also fails to find B is separately measured by Task 6's audit chain rather than assumed.

Primary review artifacts are `output/report-417fa9338e10450784b459f89af98b1c-3.md`, `output/report-417fa9338e10450784b459f89af98b1c-3-evidence.md`, `docs/superpowers/validation/2026-09-15-verification-boundary-diagnosis.md`, and the LangSmith trace recorded for session `417fa9338e10450784b459f89af98b1c`. Task implementers must preserve those artifacts as immutable baseline evidence.

### Agent-by-agent critique

| Agent / layer | What is working | Measured deficiency | Targeted repair in this plan |
| --- | --- | --- | --- |
| Planner | Produces stable topic IDs, improved broad coverage, and now names corroboration in success criteria. | It spent its full 10-call loop largely on memory; scope/as-of constraints remain prose; success criteria are not decomposed into claim-sized evidence targets the next agents can address mechanically. Prompt changes did not create second-source evidence. | Add typed scope/as-of fields and locally stamped evidence targets; give the Planner one memory call; validate topic and target coverage without asking it to retrieve evidence. |
| Researcher | Finds many relevant documents; document reads succeed far more often than blocked HTML reads; coverage reached 83–100% in several runs. | Search dominates reads; some loops consume all calls on discovery; denied/obsolete leads recur; findings carry one URL and paraphrase but no validated passage/work identity; retention diversity does not ensure claim-level corroboration. | Enforce search→read→extract/discard progression, route documents correctly, keep a read registry, emit exact passage evidence per target, and expose retention counts for publishers and URLs separately. |
| Source Evaluator | Efficient, cumulative-source handling and false score defaults were repaired; it does not waste tool calls. | It scores source quality but cannot distinguish an original work, mirror, derivative article, or self-interested statement. Domain diversity can therefore masquerade as evidence independence. | Add source role, issuer, publisher, and canonical work assessment; leave quality scoring separate from independence eligibility. |
| Fact Checker | Conservative failure behavior and per-claim reason field exist; contradictions override verification. | This is the main blocker. The upstream-pooling change admits upstream URLs but not their actual passages into the adjudication prompt; `verify_claim` still requires a newly retrieved independent domain; same-work copies can count as separate publishers; semantic duplicate claims are verified repeatedly; verification loops issue 121 searches. | Build a claim-specific union evidence pool, allow zero-retrieval adjudication when upstream A+B qualify, enforce distinct publisher **and** work locally, classify every failure mechanically, and verify one canonical atomic claim per cluster. |
| Synthesizer | Produces an honest partial report and a strong separate ledger; does not silently promote every insufficient claim. | It ranks statements with no deployment mechanism, repeats equivalent claims, leaks internal IDs, omits verification sources from reader references, and gives unresolved material too much reader-facing space. | Compose only from canonical clusters, derive citations from selected evidence IDs, require mechanism/scope for ranked constraints, keep technical uncertainty in the ledger, and enforce reader-size/backmatter gates. |
| Critic | Consistently refuses weak reports and exposes major coverage/evidence gaps. | Its score is being treated as the only judge despite calibration uncertainty; its live “spot checks” are search snippets, not read evidence; it spends 40 tool calls per run; gaps lack claim/work failure identity and cannot drive precise refinement. | Make normal critique tool-free over the canonical report/ledger/quality packet, add typed gaps, and add monotonic calibration cases. Keep optional external auditing outside the graph. |
| Graph / CLI | Publishes immutable report+ledger pairs, preserves partial output, and supports strict exit `4`. | Refinement reruns broad stages even when only one claim lacks a second work; unchanged passes continue; the CLI floods budget/error rows but hides per-topic evidence health, semantic duplicates, elapsed time, and independent-work counts. | Add typed refinement targets, a deterministic progress fingerprint, early no-progress finalization, aggregated diagnostics, per-topic status, SLO data, and separate normal/debug event rendering. |

### Where the evidence is still inconclusive

- One broad question and four nearby canaries cannot establish performance across domains. The plan therefore treats battery storage as diagnosis evidence, not the release test.
- A `5/10` critic score is evidence of a weak report, not a precise measurement of how many defects exist. Deterministic hard gates and calibration fixtures remain co-equal evidence.
- The raw ledger showing one URL per claim does not distinguish “Researcher never found B” from “B was read but lost before claim extraction.” The new read→finding→pool→prompt→selected evidence audit in Task 6 makes that distinction mechanical.
- A different domain is not necessarily an independent work. Until work identity exists, current “verified” counts are provisional and must not be used as a production-readiness metric.

---

## Fixed Production Semantics

### Verification rule

A load-bearing atomic claim is `verified` only when all of the following are true:

1. Every selected support is tied to a successful page/document read, an exact bounded excerpt, and a locator.
2. The adjudicator selects at least two supports that each state the complete atomic claim.
3. At least one pair of selected supports has different canonical `publisher_id` values **and** different non-null canonical `work.key` values.
4. Neither member of that qualifying pair has source role `mirror`, `derivative`, or `unknown`. A `self_interested` origin may be one member only when the other member is `primary` or `independent_analysis`; two self-interested works never form a qualifying pair.
5. No selected contradiction defeats the claim under the existing contradiction-first rule.

The evidence pool is the union of claim-linked Researcher evidence and Fact Checker retrievals. If upstream evidence A+B already meets the candidate independence rule, the Fact Checker skips retrieval and asks the adjudicator to judge A+B directly. A new source C is neither required nor preferred.

Two copies of the same report are one work. A mirror inherits the issuing organization's publisher identity, so an OSTI-hosted copy and an LBNL-hosted copy of one LBNL report contribute one work and one publisher for verification. A genuinely standalone analysis by another publisher is a different work only when its own passage supports the claim; merely quoting or linking the primary report is `derivative` and ineligible as the second support.

Canonical publisher identity uses normalized issuing organization when the read metadata names one, then the standalone page's normalized publisher organization, then registrable domain as a fallback. A repository, CDN, syndication host, or mirror never becomes the publisher merely because it served the bytes. Ambiguous parent/issuer identity remains unknown and cannot create a qualifying pair.

Canonical work identity precedence is:

1. normalized DOI;
2. issuing-organization-namespaced report number;
3. normalized full-content SHA-256;
4. normalized title + publication year + issuing organization;
5. unknown.

Unknown or ambiguous work identity fails closed: the evidence may appear in the ledger, but it cannot satisfy the second-work gate.

`content_hash` is a batch match key, not automatic proof that unmatched content is a distinct intellectual work. Identical normalized hashes unify copies. A unique hash falls through to title/year/issuer identity; if that metadata is absent, the work remains unknown. Two different hashes never establish independence by themselves.

### Mechanical failure classification

For every non-verified load-bearing claim, record exactly one primary reason:

| Reason | Deterministic predicate |
| --- | --- |
| `no_independent_read` | Fewer than two claim-linked successful read units exist after acquisition, regardless of how many search results exist. |
| `same_work_only` | At least two identity-known read units exist, but every possible supporting pair collapses to one canonical work. |
| `same_publisher_only` | At least two identity-known, distinct-work read units exist, but every possible pair collapses to one canonical publisher. |
| `ambiguous_identity` | At least two read units exist, but fewer than two eligible units have known publisher and work identities. |
| `no_valid_supporting_passage` | All read units reached the adjudication prompt, but fewer than two were selected as complete support. |
| `model_declined_verification` | Two locally eligible selected supports exist, no contradiction exists, but the adjudicator returns a non-verified judgement. This is an alert-worthy model disagreement. |
| `loop_failed` | Acquisition ended in a non-provider loop failure before adjudication and upstream evidence was insufficient. |
| `provider_unavailable` | The structured adjudication provider failed and no verdict was produced. |

Apply reasons in this precedence order so one claim never receives different labels from equivalent evidence: `provider_unavailable`, `loop_failed`, `no_independent_read`, `ambiguous_identity`, `same_work_only`, `same_publisher_only`, `no_valid_supporting_passage`, `model_declined_verification`.

Audit boundary classification is equally mechanical:

- `acquisition`: fewer than two successful read units entered the registry for the evidence target after the acquisition loop.
- `evidence_handoff`: a read ID is neither used by an accepted finding nor assigned an explicit discard reason, a finding evidence ID disappears before its claim cluster, or a cluster evidence ID disappears before the adjudication prompt. Record the first failing boundary as `read_to_finding`, `finding_to_cluster`, or `cluster_to_prompt`.
- `publisher_identity`: every accepted evidence ID reaches the prompt, but publisher/work/role eligibility leaves no qualifying pair.
- `genuinely_missing_corroboration`: there is no unexplained boundary loss, identity handling is complete, and the acquired passages still do not provide two complete independent supports.

### Retention rule

- The Researcher cap is four distinct publishers per sub-topic pass, not four URLs.
- Within each publisher, retain findings round-robin across evidence targets, then by descending confidence and read time.
- `publishers_retained` counts canonical publishers.
- `source_urls_retained` counts canonical URLs.
- `findings_retained` counts finding rows.
- Keep deprecated `sources_retained` as an alias of `source_urls_retained` for one compatibility release; document its meaning and migrate every internal consumer to the explicit fields.

---

## Target Contract Shape

Task 1 owns these shared contracts. Later tasks consume them without redefining their meaning:

```python
WorkIdentityBasis = Literal[
    "doi", "report_number", "content_hash", "title_year_issuer", "unknown"
]
SourceRole = Literal[
    "primary", "independent_analysis", "derivative", "mirror",
    "self_interested", "unknown",
]
EvidenceOrigin = Literal["researcher", "fact_checker"]


class WorkIdentity(ContractModel):
    key: str | None = None
    basis: WorkIdentityBasis = "unknown"
    issuer_id: str | None = None


class EvidenceUnit(ContractModel):
    evidence_id: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    source_title: str = Field(min_length=1)
    locator: str = Field(min_length=1)
    excerpt: str = Field(min_length=1)
    publisher_id: str = Field(min_length=1)
    work: WorkIdentity
    source_role: SourceRole = "unknown"
    origin: EvidenceOrigin


class EvidencePassage(EvidenceUnit):
    stance: Literal["supports", "contradicts"]
```

`Finding.evidence_unit` is nullable only for backward deserialization. Every new Researcher finding must carry one. `ScoredSource` carries the evaluator's canonical `publisher_id`, `work`, and `source_role`. A helper overlays evaluated identity onto a finding without mutating historical snapshots.

Historical state must remain readable and conservative. Before validators derive a stable legacy `evidence_id`, URL-host publisher fallback, `work.basis="unknown"`, and `source_role="unknown"` for old `EvidencePassage` rows; old `Claim` rows default `cluster_id` to `claim_id`; old gaps normalize to typed coverage gaps. Legacy rows can render in a ledger but can never satisfy strict independence until reread/re-adjudicated. New producer builders enforce the stronger contract even where deserialization fields have safe defaults.

---

## Production Release Scorecard

No agent is production-ready merely because its unit tests pass. Task 12 must record every row below as pass/fail from controlled and live traces. Any failed row keeps the branch `NOT READY`.

| Agent / surface | Required production evidence |
| --- | --- |
| Planner | 100% strict-schema success across three controlled repetitions and its live case; zero scope/geography/date expansion; every sub-topic has atomic target IDs; at most one memory call; no invented source or verdict. |
| Researcher | Every successful read is used or explicitly discarded; zero unexplained read→finding loss; no third consecutive search without a read/terminal reason; zero repeated denied URLs; document fallback exercised; every accepted finding has exact read provenance. |
| Source Evaluator | Exactly one cumulative row per canonical URL; 100% role/work classification coverage for sources behind settled claims; zero mirror/derivative false-independence cases; quality scores unchanged by independence role; zero tool calls. |
| Fact Checker | 100% finding→cluster→prompt handoff; upstream A+B verifies without C in the controlled/live trace when passages qualify; zero same-work/same-publisher false verification; contradiction precedence always holds; 100% insufficiency-reason coverage; zero duplicate cluster adjudication. |
| Synthesizer | Every settled point maps to a canonical cluster and selected evidence IDs; 100% selected-support citation coverage; zero uncited settled points, internal IDs, duplicate clusters, malformed Markdown, or ranked `not stated` mechanisms; reader length and backmatter gates pass. |
| Critic | Zero discovery/read tool calls; 100% typed/routeable gaps; monotonic calibration passes all repetitions; never accepts a deterministic hard failure; scores at least `7` only when the strict evidence packet qualifies. |
| Graph | Target-only refinement; zero unchanged second refinement; deterministic `no_progress` stop; cumulative snapshots remain canonical; report and ledger publish atomically or both remain unpublished. |
| CLI / API | Strict exit codes, interrupt behavior, bounded normal output, complete debug output, per-topic/evidence/SLO summary, and backward-compatible additive API fields. |
| End-to-end | Every accepted broad run has coverage `>=0.80`, critic `>=7`, reader quality `>=0.80`, zero handoff failures, zero semantic duplicates/conflicting cluster verdicts, no unresolved target attributable to a spent tool budget, and no unexplained provider/tool failure. Narrow run `<=250,000` total tokens and `<=15` minutes; broad run `<=500,000` total tokens and `<=30` minutes. |

The token/time SLOs are release gates, not reasons to truncate evidence silently. If a run exceeds them, targeted refinement or prompt/context compaction must improve; the ceiling is never bypassed by omitting provenance or lowering evidence requirements.

---

### Task 0: Freeze the latest evidence-pooling run as the immutable baseline

**Files:**

- Create: `docs/superpowers/validation/2026-09-15-evidence-pooling-run-results.md`
- Update: `docs/superpowers/validation/2026-09-15-release-status.md`

**Evidence inputs:**

- `docs/superpowers/validation/2026-09-15-evidence-pooling-run-predeclaration.md`
- `output/cli-canary-20260915-223507-evidencepooling.log`
- `output/report-417fa9338e10450784b459f89af98b1c-3.md`
- `output/report-417fa9338e10450784b459f89af98b1c-3-evidence.md`
- LangSmith trace `01a0a8b6-3e6c-71f2-af92-d5f0ab488a5c`

**Contract:** Preserve what happened before changing production code; distinguish measured facts from interpretation and never relabel this partial run later.

- [ ] Record candidate code commit `2bc6665de59d0cec3d76f0bd0cdc5bd92ba3b180`, docs-only predeclaration tip `935cc9e09dc4f5659d0c3acd44f40236e76bab79`, session ID, trace ID/link, exact command, exit code, and environment-key presence without values.
- [ ] Compute and record byte sizes and SHA-256 hashes for the log, reader report, and evidence ledger using `Get-FileHash -Algorithm SHA256` and `Get-Item`. Do not copy the untracked artifacts into Git.
- [ ] Record terminal `partial`, critic `5/10`, coverage `4/6`, `36` findings, `10` reviewed sources, `16` checked claims, `5` provisional verified claims, `15` verification passages, and about `815,664` tokens. Label “verified” provisional because the run had no canonical-work gate.
- [ ] Record trace tool attribution separately from ledger error rows: Researcher `157` searches and `20` reads; Fact Checker `121` searches and `58` reads; Critic `29` searches and zero reads. Preserve exact trace-query method and do not equate tool calls, provider transport attempts, event rows, or tokens.
- [ ] Document the artifact defects: four of six topics covered, semantic queue/cutoff/PJM duplicates, blank insufficiency reasons in some rows, `not stated` mechanisms, internal claim IDs in reader prose, origin-only references, and stale release-status text.
- [ ] State the bounded causal conclusion: `2bc6665` widened the URL allow-list but did not put upstream passages into `claim_verification_messages` and did not remove `verify_claim`'s new-independent-retrieval precondition. Also state that the run alone cannot prove whether Researcher acquisition found a second work.
- [ ] Keep release status `NOT READY` and link this plan as the approved repair route. Do not edit any earlier predeclaration or result record.
- [ ] Run a content assertion for the candidate SHA, session ID, trace ID, `5/10`, `4/6`, `815,664`, and `NOT READY`; run `git diff --check` and a credential-pattern scan on the new/updated documents.
- [ ] Commit exact documentation paths with message `docs(validation): record evidence pooling baseline`.
- [ ] Dispatch a fresh Luna Max fast evidence reviewer. Require source-to-number reconciliation, hash/path accuracy, separation of measurement from inference, and absence of credential values. Fix and re-review every finding.
- [ ] Push the reviewed documentation commit to the branch remote.

---

### Task 1: Establish canonical read, publisher, and work identity contracts

**Files:**

- Create: `src/deep_research/agents/evidence.py`
- Modify: `src/deep_research/utils/types.py`
- Modify: `src/deep_research/tools/document_reader.py`
- Modify: `src/deep_research/tools/web_scraper.py`
- Modify: `src/deep_research/agents/sources.py`
- Test: `tests/test_agents/test_evidence.py`
- Test: `tests/test_types.py`
- Test: `tests/test_tools/test_document_reader.py`
- Test: `tests/test_tools/test_web_scraper.py`
- Test: `tests/test_agents/test_sources.py`

**Contract:** Reading produces a deterministic content fingerprint; source identity and work identity are separate; unknown identity never passes an independence gate.

- [ ] **RED:** Add tests for DOI normalization (`https://doi.org/10.1234/ABC` equals `doi:10.1234/abc`), issuer-namespaced report numbers, identical normalized-content hash grouping, title/year/issuer fallback, precedence order, and fail-closed unknown identity. Prove two unmatched hashes do not by themselves become two independent works. Expected failure: `deep_research.agents.evidence` and the new models do not exist.
- [ ] **RED:** Add a same-work test in which LBNL and OSTI URLs for one report resolve to the same `work.key` and issuer publisher; add a control in which an independent analysis has a distinct work and publisher.
- [ ] **RED:** Add publisher-precedence tests for named issuing organization, standalone publisher organization, registrable-domain fallback, CDN/repository host, syndication copy, and ambiguous issuer. Serving host alone must not make a mirror independent.
- [ ] **RED:** Extend tool tests to require `content_sha256` in every successful scraper/document payload and output summary. Hash normalized extracted text, not response headers or a URL; equal normalized content must hash equally.
- [ ] Implement `normalize_identifier`, `normalized_content_sha256`, batch-aware `resolve_work_identities`, `canonical_publisher_id`, `eligible_independent_pair`, and `evidence_id` as pure functions in `agents/evidence.py`. `resolve_work_identities` applies the precedence above across the run's units and treats content hash only as an equality signal. `eligible_independent_pair` returns true only for different known works and publishers; it rejects mirrors, derivatives, unknown roles, and two self-interested sources, while permitting one self-interested origin paired with a primary or independent analysis.
- [ ] Add the target contract models to `utils/types.py`. Keep `Finding.evidence_unit: EvidenceUnit | None = None` for old snapshots, but validate new construction through `build_finding_evidence` rather than weakening `EvidenceUnit`.
- [ ] Add `content_sha256` to both read-tool output schemas and bounded summaries. The document hash uses the concatenated normalized extracted chunk text so alternate containers of the same readable content can converge.
- [ ] Preserve `publisher_identity(url)` as the registrable-domain fallback, then add issuer-aware `canonical_publisher_id`. Do not reinterpret a normal standalone article as its quoted source's publisher.
- [ ] Run `python -m pytest tests/test_agents/test_evidence.py tests/test_types.py tests/test_tools/test_document_reader.py tests/test_tools/test_web_scraper.py tests/test_agents/test_sources.py -q`. Expected: all pass with no network.
- [ ] Run `python -m ruff check src tests` and `git diff --check`.
- [ ] Commit exact task paths with message `feat(evidence): add canonical read and work identity`.
- [ ] Dispatch a fresh Luna Max fast reviewer. Require explicit checks of identifier precedence, mirror/issuer semantics, URL normalization, hash determinism, backward state loading, and fail-closed eligibility. Fix and re-review every finding.
- [ ] Push the reviewed commit sequence to `origin/codex/cross-agent-planner-fix-parity`.

---

### Task 2: Make Planner output operational research targets and bound its tool use

**Files:**

- Modify: `src/deep_research/utils/types.py`
- Modify: `src/deep_research/utils/config.py`
- Modify: `src/deep_research/agents/base.py`
- Modify: `src/deep_research/agents/planner.py`
- Modify: `src/deep_research/agents/prompts.py`
- Modify: `src/deep_research/runtime/assembly.py`
- Modify: `config.yaml`
- Test: `tests/test_types.py`
- Test: `tests/test_config.py`
- Test: `tests/test_agents/test_base.py`
- Test: `tests/test_agents/test_planner.py`
- Test: `tests/test_runtime/test_assembly.py`

**Contract:** The Planner defines scope, time boundary, and claim-sized evidence targets. It recalls memory at most once and does not pretend to find sources.

```python
class EvidenceTarget(ContractModel):
    target_id: str
    question: str
    required_dimensions: list[str]


class SubTopic(ContractModel):
    # existing fields remain
    evidence_targets: list[EvidenceTarget] = Field(default_factory=list, max_length=4)
```

The shared deserialization model accepts an empty legacy target list; `PlannerAgent`'s new-plan builder separately enforces one to four targets. An old checkpoint therefore loads, but it cannot be accepted as a new production plan without replanning.

- [ ] **RED:** Add Planner tests requiring `scope_statement`, `as_of_requirement`, and one to four atomic `evidence_targets` per sub-topic. IDs must be stamped locally as `topic-01-target-01`; provider-supplied IDs are rejected or ignored.
- [ ] **RED:** Add validation cases that reject a target combining unrelated propositions, a plan whose targets do not cover every sub-topic, and a plan that silently broadens geography or date beyond the question.
- [ ] **RED:** Add config tests for `agents.tool_budget_overrides: dict[str, int]`, unknown-agent rejection, non-negative values, and `tool_budget_for(agent_name)` fallback. Add an assembly test proving each constructed agent receives its resolved budget.
- [ ] Extend `ResearchPlanDraft` with provider-facing scope, as-of, and target question fields; locally stamp IDs and normalize each target to one falsifiable proposition. Persist `plan_scope` and `plan_as_of` in `ResearchState` and `ResearchStateUpdate`.
- [ ] Update Planner instructions: memory may provide procedural guidance, but scope and targets must derive from the user's question. The Planner must not invent URLs, publishers, evidence, or verdicts.
- [ ] Add per-agent budgets without raising the global budget. Set Planner to `1`, Researcher to `10`, and Fact Checker to `10` in `config.yaml`; leave Critic unchanged until Task 8 removes its tools. This is a behavior-control change, not a request-volume experiment.
- [ ] Update target renderers consumed by the Researcher so each prompt carries the local target IDs, question, required dimensions, scope, and as-of boundary.
- [ ] Run `python -m pytest tests/test_types.py tests/test_config.py tests/test_agents/test_base.py tests/test_agents/test_planner.py tests/test_runtime/test_assembly.py -q`.
- [ ] Run `python -m ruff check src tests` and `git diff --check`.
- [ ] Commit with message `feat(planner): emit bounded evidence targets`.
- [ ] Dispatch a fresh Luna Max fast reviewer. Require checks for schema strictness, local ID ownership, scope drift, legacy state compatibility, and actual budget use at every Planner call site. Fix and re-review every finding.
- [ ] Push the reviewed commit sequence to the branch remote.

---

### Task 3: Enforce read-bearing Researcher acquisition and provenance-rich extraction

**Files:**

- Modify: `src/deep_research/agents/react.py`
- Modify: `src/deep_research/agents/steps.py`
- Modify: `src/deep_research/agents/researcher.py`
- Modify: `src/deep_research/agents/prompts.py`
- Modify: `src/deep_research/agents/events.py`
- Modify: `src/deep_research/utils/types.py`
- Modify: `src/deep_research/utils/config.py`
- Modify: `src/deep_research/runtime/assembly.py`
- Modify: `config.yaml`
- Create: `src/deep_research/tools/passage_selection.py`
- Modify: `src/deep_research/tools/document_reader.py`
- Modify: `src/deep_research/tools/web_scraper.py`
- Test: `tests/test_agents/test_react.py`
- Test: `tests/test_agents/test_steps.py`
- Test: `tests/test_agents/test_researcher.py`
- Test: `tests/test_agents/test_events.py`
- Test: `tests/test_agents/test_planner_researcher_seam.py`
- Test: `tests/test_config.py`
- Test: `tests/test_runtime/test_assembly.py`
- Test: `tests/test_tools/test_passage_selection.py`
- Test: `tests/test_tools/test_document_reader.py`
- Test: `tests/test_tools/test_web_scraper.py`

**Contract:** Search discovers a candidate; a successful read creates evidence; extraction may only cite text found in that read. Search snippets never become findings.

```python
class ToolPolicyViolation(ContractModel):
    code: Literal[
        "read_required", "memory_limit", "wrong_reader",
        "denied_page_repeated", "candidate_already_read",
    ]
    observation: str


ToolUsePolicy = Callable[
    [Sequence[ReActStep], ReActDecision], ToolPolicyViolation | None
]
```

- [ ] **RED:** Extend `run_react_loop` tests so a policy can reject a requested tool before execution, return a bounded observation to the next model turn, emit one structured event/error code, and avoid charging the external-tool budget for the rejected call. The model turn remains charged by `max_iterations`.
- [ ] **RED:** Add Researcher policy tests: at most two consecutive `web_search` calls without a successful read; `query_memory` at most once per sub-topic loop; `.pdf`, `.csv`, `.json`, `.md`, and `.txt` URLs must use `document_reader`; an HTML 401/402/403/451 blocks the exact URL immediately and opens a publisher-level HTML circuit only after two distinct denied pages, while still permitting a newly discovered explicit document on the same host; an already-read URL is not read twice.
- [ ] **RED:** Add extraction tests requiring `FindingDraft` to reference a successful read ID, locator, exact excerpt, and evidence target ID. Reject search-result URLs, excerpts not found after whitespace normalization, locators absent from the read payload, and model-supplied content hashes that disagree with the tool result.
- [ ] **RED:** Require extraction to partition every successful read ID into at least one accepted finding or one bounded disposition: `no_relevant_passage`, `duplicate_work`, `out_of_scope`, `stale`, or `malformed`. Reject missing IDs, unknown IDs, and an ID marked both used and discarded.
- [ ] **RED:** Add a long-document regression whose only relevant numeric passage is on a late PDF page beyond the current rendered prefix. With the evidence-target query, that page must appear in selected passages with its page/chunk locator; selection must be deterministic and bounded. Add the equivalent late-paragraph HTML case.
- [ ] Add the optional `tool_policy` hook to `run_react_loop`; implement `ResearchAcquisitionPolicy` in `researcher.py` using current-loop steps only. Keep the generic loop unaware of web semantics.
- [ ] Implement pure lexical `select_relevant_passages(passages, query, limit)` using normalized term/number overlap with stable source-order tie-breaking. Extend scraper/document results with bounded `selected_passages` carrying locators while retaining the full-content hash. Reads receive the current evidence-target query; the evidence renderer sends selected passages, not a truncated JSON dump, to extraction.
- [ ] Add strict config fields `agents.selected_passages_per_read: 4` and `agents.evidence_packet_chars: 24000`, with environment keys `AGENTS_SELECTED_PASSAGES_PER_READ` and `AGENTS_EVIDENCE_PACKET_CHARS`; wire them through runtime assembly to Researcher and Fact Checker and test YAML/environment/default precedence. These are prompt-shape bounds, not permission to omit a read without recording truncation.
- [ ] Build a bounded `ReadRegistry` from successful `web_scraper` and `document_reader` observations. Give every read a local ID and retain URL, title, locators/chunks, normalized content hash, status, and reader type outside the provider response schema.
- [ ] Change `FindingDraft` to return `read_id`, `locator`, `excerpt`, `content`, `source_url`, `source_title`, and `evidence_target_id`; add `ReadDispositionDraft(read_id, reason)` beside the findings list. Build `Finding.evidence_unit` only after validating all fields and the complete used/discarded partition against `ReadRegistry`.
- [ ] Add deterministic metadata extraction for DOI/report-number/title/year/issuer candidates. The model may propose metadata from the passage, but local code must select the content hash and canonical URL from the read registry.
- [ ] Apply the fixed retention rule: cap four canonical publishers per sub-topic pass, cycle across evidence targets inside each publisher, then rank by confidence and read order. Emit `publishers_retained`, `source_urls_retained`, `findings_retained`, and per-reason discard counts; keep deprecated `sources_retained` equal to the URL count while migrating all internal readers to explicit fields.
- [ ] End a sub-topic only when each target has at least one read-bearing finding, the model explicitly returns no worthwhile candidate, or the loop bound is reached. `interim_satisfaction` cannot skip a high-priority target with zero read-bearing findings.
- [ ] Bound the extraction packet by both per-passage and total characters, preserve at least one selected passage per successful read, and expose truncation counts. A truncated packet cannot claim complete read→finding handoff.
- [ ] Run `python -m pytest tests/test_agents/test_react.py tests/test_agents/test_steps.py tests/test_agents/test_researcher.py tests/test_agents/test_events.py tests/test_agents/test_planner_researcher_seam.py tests/test_tools/test_passage_selection.py tests/test_tools/test_document_reader.py tests/test_tools/test_web_scraper.py tests/test_config.py tests/test_runtime/test_assembly.py -q`.
- [ ] Run `python -m ruff check src tests` and `git diff --check`.
- [ ] Commit with message `feat(researcher): enforce search read evidence progression`.
- [ ] Dispatch a fresh Luna Max fast reviewer. Require checks for policy bypasses, multi-tool native turns, denied-page versus same-host-document behavior, late-document passage recovery, prompt bounds, exact excerpt validation, bounded event fields, and retention semantics. Fix and re-review every finding.
- [ ] Push the reviewed commit sequence to the branch remote.

---

### Task 4: Teach the Source Evaluator work identity and source role without conflating quality

**Files:**

- Modify: `src/deep_research/agents/source_evaluator.py`
- Modify: `src/deep_research/agents/prompts.py`
- Modify: `src/deep_research/agents/sources.py`
- Modify: `src/deep_research/agents/evidence.py`
- Modify: `src/deep_research/utils/types.py`
- Test: `tests/test_agents/test_source_evaluator.py`
- Test: `tests/test_agents/test_sources.py`
- Test: `tests/test_agents/test_evidence_quality_seam.py`
- Test: `tests/test_agents/test_tool_free_prompts.py`

**Contract:** Authority/relevance scoring answers “is this source useful?”; source role and work identity answer “is this independent evidence?” One must not alter the other silently.

- [ ] **RED:** Add controlled dossiers for an original national-lab report, an official repository mirror, a news article that merely repeats the report, an independent analytical article, and a company incident statement. Require roles `primary`, `mirror`, `derivative`, `independent_analysis`, and `self_interested` respectively.
- [ ] **RED:** Prove an authoritative mirror can score highly while remaining ineligible as a second work; prove a lower-scored independent analysis remains eligible if its passage directly supports the claim.
- [ ] **RED:** On evaluator-provider failure, preserve deterministic DOI/report/hash/title metadata, set role to `unknown`, keep the existing honest unscored/fallback quality state, and make the source ineligible as independent corroboration. Never invent role or issuer to keep coverage high.
- [ ] Extend the provider-facing source assessment with `source_role`, `issuing_organization`, normalized DOI/report-number candidates, publication year, and a bounded rationale. The provider never supplies final identity keys.
- [ ] Accept metadata candidates only when they are present in the canonical URL, title, or selected read text; otherwise mark the field unknown and record `identity_metadata_unsubstantiated`. A model classification cannot invent the issuer, year, DOI, or report number used by the independence gate.
- [ ] Resolve final identity locally using Task 1 precedence and the read registry. A `mirror` must carry a known work key and issuing organization from read metadata, inherit the issuer publisher, and match any other copy already in the run; otherwise downgrade its identity to unknown and fail closed.
- [ ] Carry `publisher_id`, `work`, and `source_role` on `ScoredSource`. Merge cumulative sources by canonical URL without dropping identity fields from an earlier valid assessment.
- [ ] Keep authority, recency, relevance, overall score, and low-confidence calculations unchanged except for necessary constructor updates. Do not add “independence” to `overall_score`.
- [ ] Render role and identity in the evidence ledger dossier, not as an unexplained reader-report score.
- [ ] Run `python -m pytest tests/test_agents/test_source_evaluator.py tests/test_agents/test_sources.py tests/test_agents/test_evidence_quality_seam.py tests/test_agents/test_tool_free_prompts.py -q`.
- [ ] Run `python -m ruff check src tests` and `git diff --check`.
- [ ] Commit with message `feat(source-evaluator): classify source role and work`.
- [ ] Dispatch a fresh Luna Max fast reviewer. Require checks that quality and independence remain orthogonal, mirrors fail closed, identity merges are stable, and no tool calls were added to the Source Evaluator. Fix and re-review every finding.
- [ ] Push the reviewed commit sequence to the branch remote.

---

### Task 5: Consolidate semantic claim duplicates and exclude non-load-bearing metadata

**Files:**

- Create: `src/deep_research/agents/claim_clusters.py`
- Modify: `src/deep_research/agents/fact_checker.py`
- Modify: `src/deep_research/agents/identity.py`
- Modify: `src/deep_research/agents/prompts.py`
- Modify: `src/deep_research/utils/types.py`
- Test: `tests/test_agents/test_claim_clusters.py`
- Test: `tests/test_agents/test_fact_checker.py`
- Test: `tests/test_agents/test_identity.py`
- Test: `tests/test_agents/test_evidence_quality_seam.py`

**Contract:** One atomic proposition is verified once. Publication metadata and report-cutoff notes remain context, not ranked deployment constraints.

```python
ClaimRole = Literal["load_bearing", "context", "source_metadata"]


class ClaimClusterDraft(ContractModel):
    member_ids: list[str]
    canonical_text: str
    equivalent: bool
```

- [ ] **RED:** Add regressions for the live duplicates: two paraphrases of the same queue-capacity statistic merge; three phrasings of the same report cutoff merge; two PJM transition-cycle phrasings merge. Add controls proving `2024` versus `2025`, `fell` versus `did not fall`, differing units, and differing populations never merge.
- [ ] **RED:** Add role tests: a report publication date and data-vintage statement are `source_metadata`; a mechanism linking queue delays to deployment is `load_bearing`; a useful definition is `context`. Only load-bearing claims enter verification and ranked constraints.
- [ ] **RED:** Add consolidation-provider failure and malformed-cluster cases. Preserve every source claim, flag all unresolved candidate pairs in `possible_semantic_duplicate_pairs`, record a recoverable error, and force partial quality rather than guessing a merge or verifying duplicates separately.
- [ ] Compute candidate duplicate blocks locally by overlapping coverage IDs, normalized named entities, number/unit/year atoms, and negation polarity. Send only candidate pairs or small blocks to a structured consolidation call; do not ask the model to compare every claim with every other claim.
- [ ] Merge only when the provider says equivalent **and** local atom compatibility passes. Union member source URLs, evidence-unit IDs, finding fingerprints, and coverage IDs into one canonical cluster.
- [ ] Stamp `cluster_id` locally from sorted member fingerprints. Keep `claim_id` as the exact claim fingerprint for snapshot compatibility, and add `cluster_id`, `claim_role`, and `member_claim_texts` to `Claim`.
- [ ] Add `possible_semantic_duplicate_pairs` for locally suspicious pairs the provider did not safely merge. Any such pair is a hard quality failure, not silently counted as unique.
- [ ] Preserve exact-fingerprint duplicate counting as a separate metric named `exact_duplicate_claims`; never label it as semantic duplication.
- [ ] Run `python -m pytest tests/test_agents/test_claim_clusters.py tests/test_agents/test_fact_checker.py tests/test_agents/test_identity.py tests/test_agents/test_evidence_quality_seam.py -q`.
- [ ] Run `python -m ruff check src tests` and `git diff --check`.
- [ ] Commit with message `feat(fact-checker): consolidate semantic claim clusters`.
- [ ] Dispatch a fresh Luna Max fast reviewer. Require adversarial checks for number/date/negation collisions, stable cluster IDs, provenance union, and accidental deletion of distinct claims. Fix and re-review every finding.
- [ ] Push the reviewed commit sequence to the branch remote.

---

### Task 6: Rebuild Fact Checker verification around a claim-specific union evidence pool

**Files:**

- Modify: `src/deep_research/agents/evidence.py`
- Modify: `src/deep_research/agents/fact_checker.py`
- Modify: `src/deep_research/agents/prompts.py`
- Modify: `src/deep_research/agents/events.py`
- Modify: `src/deep_research/utils/types.py`
- Test: `tests/test_agents/test_fact_checker.py`
- Test: `tests/test_agents/test_evidence_quality_seam.py`
- Test: `tests/test_agents/test_events.py`
- Test: `tests/test_agents/test_native_react_boundary.py`

**Contract:** The adjudicator selects evidence IDs from the exact pool it was shown. Local code, not the model, determines whether selected supports satisfy the two-publisher/two-work rule.

```python
class ClaimVerdictDraft(ContractModel):
    verdict: Literal[
        "verified", "contradicted", "unverified", "insufficient_evidence"
    ]
    confidence: float
    supporting_evidence_ids: list[str]
    contradicting_evidence_ids: list[str]
    rationale: str
```

- [ ] **RED:** Add the decisive upstream A+B test: two Researcher evidence units on different eligible publishers and works reach the adjudication prompt; no retrieval tool runs; the provider selects both IDs; the final claim is verified.
- [ ] **RED:** Add the latent-bug test: A+B exist in the read registry but B is absent from the claim-linked pool. Require an `evidence_handoff` audit classification and a non-verified claim, proving this cannot disappear invisibly.
- [ ] **RED:** Add cases for same work/two URLs (`same_work_only`), distinct works from one publisher (`same_publisher_only`), unknown work (`ambiguous_identity`), one read plus many search results (`no_independent_read`), two prompted units but only one supports (`no_valid_supporting_passage`), model disagreement (`model_declined_verification`), contradiction precedence, loop failure, and provider failure. Assert the fixed reason-precedence order for evidence that triggers more than one predicate.
- [ ] **RED:** Add Fact Checker acquisition-policy cases: skip the loop when upstream candidates qualify; otherwise allow at most two searches before a read; read a candidate from a different publisher/work; stop once a candidate independent pair exists; never retry a denied URL; and never treat search results as evidence.
- [ ] Replace `known_source_urls`, global `_upstream_read_urls`, URL-only `valid_verification_passages`, and the “new independent domain required” early return with `claim_evidence_pool(state, cluster)`.
- [ ] Build upstream pool membership from the cluster's consumed finding/evidence IDs, not every URL in state. Overlay Source Evaluator identity and role onto the Researcher units; keep unknowns visible but ineligible.
- [ ] If the upstream pool contains a candidate independent pair, call structured adjudication immediately with a numbered evidence packet. Otherwise run one policy-controlled acquisition loop targeted only at the missing evidence dimension, convert successful reads to Fact Checker `EvidenceUnit`s, union and deduplicate, then adjudicate.
- [ ] Implement `VerificationAcquisitionPolicy` on the shared Task 3 policy hook. It uses the current claim cluster and pool identities, not a generic “find more sources” prompt, and records why acquisition stopped: `candidate_pair_ready`, `no_candidate`, `access_denied`, or `budget_exhausted`.
- [ ] Change adjudication prompts to contain the exact excerpt, locator, publisher, work, role, and origin for each local evidence ID. The reply may reference IDs only; reject invented IDs and duplicate IDs.
- [ ] Resolve the verdict locally: contradictions first; verified only with an eligible selected pair; unverified only when the adjudicator considered valid evidence but declined settlement; insufficient when acquisition/handoff/identity/support failed.
- [ ] Populate one reason for every `insufficient_evidence` claim and no reason for every other verdict. Validate this invariant in `Claim` or its builder.
- [ ] Emit one bounded `claim_evidence_audit` event with counts and IDs safe for logs: target registry units, explicitly discarded units and reasons, finding units, cluster units, prompt units, selected support units, distinct publishers, distinct works, acquisition attempts, first failing boundary, and failure class. Do not emit excerpts in events.
- [ ] Update trace outputs so F1's alternatives are mechanically distinguishable: fewer than two target registry reads is upstream acquisition; an unexplained count/ID drop at read→finding, finding→cluster, or cluster→prompt is handoff; an explicit irrelevant/duplicate discard is not handoff; prompt A+B but selected fewer is genuine support failure.
- [ ] Run `python -m pytest tests/test_agents/test_fact_checker.py tests/test_agents/test_evidence_quality_seam.py tests/test_agents/test_events.py tests/test_agents/test_native_react_boundary.py -q`.
- [ ] Run `python -m ruff check src tests` and `git diff --check`.
- [ ] Commit with message `fix(fact-checker): adjudicate the complete evidence pool`.
- [ ] Dispatch a fresh Luna Max fast reviewer. Require line-by-line verification of zero-retrieval A+B, ID allow-listing, local eligibility, reason totality, contradiction precedence, and safe audit events. Fix and re-review every finding.
- [ ] Push the reviewed commit sequence to the branch remote.

---

### Task 7: Make Synthesizer output concise, mechanism-first, evidence-complete reports

**Files:**

- Modify: `src/deep_research/agents/synthesizer.py`
- Modify: `src/deep_research/agents/report.py`
- Modify: `src/deep_research/agents/prompts.py`
- Modify: `src/deep_research/utils/types.py`
- Test: `tests/test_agents/test_synthesizer.py`
- Test: `tests/test_agents/test_report.py`
- Test: `tests/test_agents/test_synthesis_seam.py`
- Test: `tests/test_agents/test_tool_free_prompts.py`

**Contract:** Reader points cite the selected supports that justify them; ranked constraints explain a deployment mechanism; unresolved technical detail stays in the ledger.

- [ ] **RED:** Add a report fixture in which a verified claim has one origin URL plus two selected support evidence units. Require all selected support URLs in the reader reference set and the evidence IDs in the ledger.
- [ ] **RED:** Reject a ranked constraint whose mechanism is blank, `not stated`, or merely repeats the claim. Reject reader-facing `C001`-style internal IDs. Reject two report points from the same semantic cluster.
- [ ] **RED:** Reject or locally strip a drafted report point that introduces a number, unit, year, named organization, geography, or causal mechanism absent from its canonical cluster and selected evidence. Require `mechanism_evidence_ids` to support every ranked mechanism.
- [ ] **RED:** Add recency selection: when two eligible supports are equivalent in authority and scope, the current source is preferred; historical evidence remains available in the ledger.
- [ ] Change `ReportPoint` to carry `claim_cluster_ids`, `evidence_ids`, and `mechanism_evidence_ids`; derive factual text, mechanism support, and citation URLs locally from those IDs. Provider-supplied reference URLs remain disallowed, and a local atom guard rejects new numeric/date/entity facts in drafted connective prose.
- [ ] Give the Synthesizer a compact canonical packet: plan scope/as-of, per-topic status, one row per claim cluster, selected support/contradiction passages, source scores/roles, and deterministic quality warnings. Do not send repeated raw finding rows.
- [ ] In the constraint ranking, include only `verified` load-bearing clusters with an explicit mechanism and geography/scope supported by selected evidence. Put contradicted clusters in the conflict section and unverified/insufficient clusters in open questions; neither may appear as a ranked constraint.
- [ ] Render uncertainty by topic and failure class without claim IDs. The reader report summarizes unresolved areas once; the evidence ledger retains every claim, reason, identity, passage, error, and audit boundary count.
- [ ] Preserve existing limits: reader report at most `8,000` words unless the user explicitly requests longer, and references/backmatter below `35%` of reader-report characters. Add deterministic counters for both.
- [ ] Ensure the methodology states independent publisher/work counts and explains that mirrors/derivatives do not count as corroboration.
- [ ] Run `python -m pytest tests/test_agents/test_synthesizer.py tests/test_agents/test_report.py tests/test_agents/test_synthesis_seam.py tests/test_agents/test_tool_free_prompts.py -q`.
- [ ] Run `python -m ruff check src tests` and `git diff --check`.
- [ ] Commit with message `feat(synthesizer): cite canonical evidence and mechanisms`.
- [ ] Dispatch a fresh Luna Max fast reviewer. Require review of citation completeness, reader/ledger separation, contradiction wording, internal-ID leakage, duplicate clusters, and size gates. Fix and re-review every finding.
- [ ] Push the reviewed commit sequence to the branch remote.

---

### Task 8: Make Critic tool-free, targetable, and calibrated

**Files:**

- Modify: `src/deep_research/agents/critic.py`
- Modify: `src/deep_research/agents/prompts.py`
- Modify: `src/deep_research/utils/types.py`
- Modify: `src/deep_research/utils/config.py`
- Modify: `src/deep_research/runtime/assembly.py`
- Modify: `config.yaml`
- Modify: `src/deep_research/evaluation/cases/critic.py`
- Test: `tests/test_agents/test_critic.py`
- Test: `tests/test_agents/test_tool_free_prompts.py`
- Test: `tests/test_config.py`
- Test: `tests/test_runtime/test_assembly.py`
- Test: `tests/test_evaluation/test_cases_critic.py`
- Test: `tests/test_evaluation/test_evaluators_agents.py`

**Contract:** The normal Critic judges the published packet and deterministic quality snapshot; it does not discover new evidence and never treats search snippets as verification.

```python
GapKind = Literal[
    "coverage", "missing_second_work", "access", "contradiction",
    "semantic_duplicate", "source_quality", "mechanism", "freshness",
]
MissingDimension = Literal[
    "topic", "publisher", "work", "passage", "identity",
    "mechanism_evidence", "presentation", "freshness", "conflict_resolution",
]


class CritiqueGap(ContractModel):
    coverage_id: str | None
    claim_cluster_id: str | None
    kind: GapKind
    missing_dimension: MissingDimension
    problem: str
    recommended_queries: list[str]
```

- [ ] **RED:** Add a test proving the Critic has an empty toolset, makes no `run_react_loop` call, and still performs one structured review over the canonical packet. Search payloads must be absent from the packet.
- [ ] **RED:** Add typed-gap tests requiring a coverage ID for topic gaps, a cluster ID for claim gaps, a valid failure kind, and queries only when retrieval can remedy the gap. A formatting/mechanism gap must not fabricate a web query.
- [ ] **RED:** Put a load-bearing contradiction, limitations entry, and citation near the end of a report beyond 6,000 characters. Require all three in the Critic packet and resulting review; prefix truncation must never hide a report section.
- [ ] **RED:** Add monotonic calibration fixtures: a report with all deterministic gates and complete evidence scores at least `7`; removing one narrow non-load-bearing detail cannot reduce it below `7`; removing a second independent work lowers the score; missing multiple topics and supports scores below the accepted fixture. Keep score bands broad enough to test ordering without scripting one exact prose response.
- [ ] Delete or bypass Critic discovery/spot-check ReAct behavior. Set `agents.tool_budget_overrides.critic: 0` and assert runtime assembly passes no tools.
- [ ] Build `CriticPacket` from the exact reader report, evidence-ledger digest, `ReportQualitySnapshot`, per-topic states, claim-cluster evidence counts, and error summary. The critic may explain hard failures but cannot override them.
- [ ] Update rubric wording so `7` means every load-bearing settled point has an eligible evidence pair and only narrow explicitly disclosed gaps remain. Preserve the user-required threshold.
- [ ] Keep optional independent live auditing out of the production graph. If later needed, it becomes a separately invoked read-capable evaluator with its own budget and results; do not recreate it as search-only Critic calls.
- [ ] Re-pin critic controlled-case fingerprints only after inspecting the semantic diff and recording why the prompt/schema changed.
- [ ] Run `python -m pytest tests/test_agents/test_critic.py tests/test_agents/test_tool_free_prompts.py tests/test_config.py tests/test_runtime/test_assembly.py tests/test_evaluation/test_cases_critic.py tests/test_evaluation/test_evaluators_agents.py -q`.
- [ ] Run `python -m ruff check src tests` and `git diff --check`.
- [ ] Commit with message `refactor(critic): judge canonical evidence without tools`.
- [ ] Dispatch a fresh Luna Max fast reviewer. Require checks that the critic cannot make external calls, hard gates remain authoritative, gap routing is valid, and calibration does not encode a passing answer. Fix and re-review every finding.
- [ ] Push the reviewed commit sequence to the branch remote.

---

### Task 9: Target refinement and stop deterministic no-progress loops

**Files:**

- Modify: `src/deep_research/utils/types.py`
- Modify: `src/deep_research/graph/state.py`
- Modify: `src/deep_research/graph/nodes.py`
- Modify: `src/deep_research/graph/orchestrator.py`
- Modify: `src/deep_research/agents/researcher.py`
- Modify: `src/deep_research/agents/source_evaluator.py`
- Modify: `src/deep_research/agents/fact_checker.py`
- Test: `tests/test_state.py`
- Test: `tests/test_graph/test_state.py`
- Test: `tests/test_graph/test_nodes.py`
- Test: `tests/test_graph/test_orchestrator.py`
- Test: `tests/test_agents/test_researcher.py`
- Test: `tests/test_agents/test_fact_checker.py`

**Contract:** A refinement pass touches only named topics/claim clusters and ends after one pass with no deterministic improvement.

```python
class RefinementTarget(ContractModel):
    coverage_id: str
    claim_cluster_id: str | None = None
    gap_kind: GapKind
    missing_dimension: MissingDimension
    queries: list[str]


class ResearchProgress(ContractModel):
    covered_topic_ids: list[str]
    verified_cluster_ids: list[str]
    unresolved_claim_reasons: dict[str, str]
    semantic_duplicate_pairs: list[str]
```

- [ ] **RED:** Add graph tests showing a missing-second-work gap for one cluster researches only that cluster/topic; a mechanism-only gap returns to synthesis without web research; an unresolved global coverage gap targets only uncovered topics.
- [ ] **RED:** Add a no-progress test: two consecutive quality checkpoints with identical covered topics, verified clusters, insufficiency reasons, and semantic-duplicate pairs finalize `partial` with reason `no_progress` before spending another refinement pass.
- [ ] Derive `RefinementTarget`s locally from typed Critique gaps. Never parse topic titles or free-text problem descriptions to route work. `missing_dimension="presentation"` routes directly to synthesis; `topic`, `publisher`, `work`, `passage`, `identity`, `mechanism_evidence`, and `freshness` route to targeted acquisition; `conflict_resolution` routes to Fact Checker adjudication and acquires only when the gap also names missing evidence.
- [ ] Store a bounded `progress_history` in state. Compute its fingerprint from sorted deterministic fields; critic wording and score alone do not count as progress.
- [ ] Update Researcher selection to include only targeted evidence targets. Update Source Evaluator to score only new/changed sources while returning the cumulative canonical snapshot. Update Fact Checker to re-adjudicate targeted clusters plus any cluster whose evidence pool changed.
- [ ] Route presentation-only gaps directly to synthesis. Route mechanism or freshness gaps to synthesis only when the canonical packet already contains the requested evidence; otherwise use their typed acquisition target. Route contradictions to Fact Checker and acquire only when `missing_dimension` names absent evidence.
- [ ] Emit one `refinement_started`, `refinement_progress`, or `refinement_stopped_no_progress` event per macro pass with stable IDs and counts.
- [ ] Preserve `max_iterations` as the final iteration ceiling; no-progress termination can stop earlier but never extend it.
- [ ] Run `python -m pytest tests/test_state.py tests/test_graph/test_state.py tests/test_graph/test_nodes.py tests/test_graph/test_orchestrator.py tests/test_agents/test_researcher.py tests/test_agents/test_fact_checker.py -q`.
- [ ] Run `python -m ruff check src tests` and `git diff --check`.
- [ ] Commit with message `feat(graph): target refinement and stop no progress`.
- [ ] Dispatch a fresh Luna Max fast reviewer. Require checks of every route, snapshot replacement versus append semantics, progress fingerprint stability, and partial finalization. Fix and re-review every finding.
- [ ] Push the reviewed commit sequence to the branch remote.

---

### Task 10: Enforce production quality gates and make CLI diagnostics useful

**Files:**

- Modify: `src/deep_research/agents/quality.py`
- Modify: `src/deep_research/agents/report.py`
- Modify: `src/deep_research/runtime/outcome.py`
- Modify: `src/deep_research/cli.py`
- Modify: `src/deep_research/api/models.py`
- Modify: `src/deep_research/api/sessions.py`
- Modify: `README.md`
- Test: `tests/test_agents/test_quality.py`
- Test: `tests/test_agents/test_report.py`
- Test: `tests/test_runtime/test_outcome.py`
- Test: `tests/test_cli/test_arguments.py`
- Test: `tests/test_cli/test_render.py`
- Test: `tests/test_cli/test_report_quality_acceptance.py`
- Test: `tests/test_api/test_sessions.py`
- Test: `tests/test_api/test_stream_and_artifacts.py`

**Contract:** Acceptance is deterministic hard gates plus critic `>=7` plus deterministic whole-reader quality `>=0.80`; CLI normal mode reports decisions, not every internal event. Debug mode retains raw detail.

- [ ] **RED:** Extend quality tests with separate `exact_duplicate_claims` and `semantic_duplicate_claims`, qualifying publisher/work counts, missing insufficiency reasons, per-topic status, reader word count, backmatter ratio, and settled points lacking an eligible evidence pair.
- [ ] **RED:** Add a shared seven-dimension reader-quality score for completeness, prioritization, evidence quality, attribution, uncertainty, readability, and actionability. Require the production snapshot and controlled whole-report evaluator to produce the same score from the same typed composition; live acceptance requires `>=0.80`.
- [ ] **RED:** Require these hard failures: coverage below `0.80`; any semantic duplicate pair; any uncited settled point; any verified/load-bearing point without an eligible two-publisher/two-work pair; any insufficient claim without a reason; reader over `8,000` words without explicit user request; backmatter over `0.35`; report/ledger publication mismatch.
- [ ] **RED:** Add CLI rendering tests for one compact phase line per agent, per-topic `verified/partial/unresolved` status, verified cluster count, independent publisher/work count, aggregated errors by agent+type+reason, elapsed time, request/token totals, ceiling utilization, and exact versus semantic duplicate labels.
- [ ] Add `TopicQualityStatus`, per-dimension reader scores, `reader_quality_score`, and the new integrity metrics to `ReportQualitySnapshot`. Compute evidence relationships from canonical composition; only bounded word/backmatter/render checks may inspect rendered Markdown.
- [ ] Move or expose the existing whole-report seven-dimension calculation as one shared pure helper used by production quality and `e2e_evaluation`; do not maintain two formulas or call a provider for this score.
- [ ] Add `duration_seconds`, provider request-attempt counts, and ceiling utilization to `ResearchOutcome`; derive time from run start/end timestamps, not wall-clock calls during rendering.
- [ ] Add CLI flag `--debug-events`. Default mode suppresses per-request budget rows and repeated tool errors; `--verbose` shows phase summaries and aggregate tool counts; `--debug-events` prints the full bounded event stream. Preserve backward behavior for all other flags.
- [ ] Render budget progress only at start, 80%, 90%, and terminal use in normal/verbose output. Aggregate recoverable failures while preserving complete rows in the ledger.
- [ ] Keep exit meanings: `0` finished without strict quality requirement, `1` configuration, `2` usage, `3` graph failure, `4` `--require-quality` non-acceptance, `130` interrupt. Add regression tests proving the richer quality gates still return `4` and preserve both artifact paths.
- [ ] Expose the same quality/topic/SLO data through API models without exposing excerpts, secrets, or raw provider payloads.
- [ ] Update README CLI examples, quality semantics, evidence-independence rule, and debug-event behavior.
- [ ] Run `python -m pytest tests/test_agents/test_quality.py tests/test_agents/test_report.py tests/test_runtime/test_outcome.py tests/test_cli tests/test_api/test_sessions.py tests/test_api/test_stream_and_artifacts.py -q`.
- [ ] Run `python -m ruff check src tests` and `git diff --check`. Do not run Ruff over the unrelated untracked `tools/` directory.
- [ ] Commit with message `feat(cli): surface production evidence quality`.
- [ ] Dispatch a fresh Luna Max fast reviewer. Require checks of every hard gate, seven-dimension parity with whole-report evaluation, exit-code precedence, aggregation accuracy, normal/verbose/debug behavior, and API compatibility. Fix and re-review every finding.
- [ ] Push the reviewed commit sequence to the branch remote.

---

### Task 11: Build and pass the network-zero production matrix

**Files:**

- Modify: `src/deep_research/e2e_evaluation/cases.py`
- Modify: `src/deep_research/e2e_evaluation/evaluators.py`
- Modify: `src/deep_research/e2e_evaluation/models.py`
- Modify: `src/deep_research/e2e_evaluation/runner.py`
- Modify: `src/deep_research/evaluation/cases/planner.py`
- Modify: `src/deep_research/evaluation/cases/researcher.py`
- Modify: `src/deep_research/evaluation/cases/source_evaluator.py`
- Modify: `src/deep_research/evaluation/cases/fact_checker.py`
- Modify: `src/deep_research/evaluation/cases/synthesizer.py`
- Modify: `src/deep_research/evaluation/cases/critic.py`
- Modify: affected files under `tests/test_e2e_evaluation/` and `tests/test_evaluation/`
- Create: `docs/superpowers/validation/2026-09-16-evidence-integrity-controlled-validation.md`
- Update: `docs/superpowers/validation/2026-09-15-release-status.md`

**Contract:** Controlled tests exercise the real cross-agent seams and failure modes; they do not script a green final report around production helpers.

- [ ] **RED:** First update registry/case-contract tests to require upgraded `broad-constraints`, `comparative-conflict`, and `refinement-evidence-recovery` plus new `blocked-html-pdf-fallback`, `same-work-mirror`, `semantic-duplicate-claims`, and `stalled-refinement`; capture the missing-case/version failures. Then implement the seven fixed, inspectable, network-zero cases while preserving historical IDs.
- [ ] Give every case deterministic expectations for topic coverage, read/search progression, publisher/work identity, selected evidence IDs, claim clusters, reasons, report citations, critic routing, final status, and exit policy.
- [ ] Replace the whole-report runner's hard-coded “exactly three cases” invariant with an assertion over the exact seven registered IDs above while preserving exactly three repetitions per case and fail-on-any-repetition behavior.
- [ ] Add these exact per-agent controlled cases targeted to the repaired contract: Planner `scoped-evidence-targets`; Researcher `read-bearing-acquisition`; Source Evaluator `work-role-independence`; Fact Checker `upstream-independent-pair`; Synthesizer `canonical-evidence-report`; Critic `typed-gap-calibration`.
- [ ] Version-bump each agent's single registered live case and make its gates exercise the same repaired contract: Planner target structure, Researcher read/disposition chain, Source Evaluator role/work identity, Fact Checker upstream pool and selected evidence IDs, Synthesizer citation/mechanism integrity, and Critic tool-free typed gaps. Preserve earlier live artifacts under their original case versions.
- [ ] Add evaluator metrics for eligible verified clusters, evidence handoff completeness, semantic duplicates, reason totality, reader/ledger alignment, and targeted-refinement efficiency. A judge-provider failure still makes an evaluation `FAILED` even when deterministic gates pass.
- [ ] Run the fake-driven per-agent contracts first: `python -m pytest tests/test_evaluation -q`. This command must make zero external requests; an attempted provider or LangSmith call is a test failure.
- [ ] Run `python -m deep_research.e2e_evaluation suite --tier controlled --repetitions 3`. Require every repetition to satisfy every deterministic hard gate; do not average away a failed repetition.
- [ ] Run `python -m pytest -q`. Expected: zero failures, one intentional live deselection unless the test inventory explicitly changes it.
- [ ] Run `python -m ruff check src tests`, `git diff --check`, and a secret-pattern scan over the new validation document.
- [ ] Write the controlled validation record with candidate SHA, commands, exact pass/fail counts, case-level metrics, prompt fingerprint changes and rationale, known limitations, and confirmation that no external requests occurred.
- [ ] Update release status to `CONTROLLED READY / LIVE NOT YET VALIDATED` only if every deterministic matrix gate passes. Otherwise list the exact failing agent/case and keep status `NOT READY`.
- [ ] Commit with message `test(evaluation): cover evidence integrity production matrix`.
- [ ] Dispatch a fresh Luna Max fast reviewer. Require inspection for fake leakage, fixture-to-production helper parity, hard-gate coverage, judge-failure honesty, and validation-record accuracy. Fix and re-review every finding.
- [ ] Push the reviewed commit sequence to the branch remote.

---

### Task 12: Run staged blind live validation and publish the release packet

**Files:**

- Create: `docs/superpowers/validation/2026-09-16-production-canary-01-agent-controlled-predeclaration.md`
- Create: `docs/superpowers/validation/2026-09-16-production-canary-01-agent-controlled-results.md`
- Create: `docs/superpowers/validation/2026-09-16-production-canary-02-agent-live-predeclaration.md`
- Create: `docs/superpowers/validation/2026-09-16-production-canary-02-agent-live-results.md`
- Create: `docs/superpowers/validation/2026-09-16-production-canary-03-document-smoke-predeclaration.md`
- Create: `docs/superpowers/validation/2026-09-16-production-canary-03-document-smoke-results.md`
- Create: `docs/superpowers/validation/2026-09-16-production-canary-04-battery-broad-predeclaration.md`
- Create: `docs/superpowers/validation/2026-09-16-production-canary-04-battery-broad-results.md`
- Create: `docs/superpowers/validation/2026-09-16-production-canary-05-comparative-conflict-predeclaration.md`
- Create: `docs/superpowers/validation/2026-09-16-production-canary-05-comparative-conflict-results.md`
- Create: `docs/superpowers/validation/2026-09-16-production-canary-06-current-regulation-predeclaration.md`
- Create: `docs/superpowers/validation/2026-09-16-production-canary-06-current-regulation-results.md`
- Create: `docs/superpowers/validation/2026-09-16-production-canary-07-document-fallback-predeclaration.md`
- Create: `docs/superpowers/validation/2026-09-16-production-canary-07-document-fallback-results.md`
- Create: `docs/superpowers/validation/2026-09-16-agent-production-readiness-final.md`
- Update: `docs/superpowers/validation/2026-09-15-release-status.md`
- Update: `docs/superpowers/2026-09-08-cross-agent-planner-fix-parity-fix-log.md`

**Contract:** Readiness is demonstrated on controlled and blind live evidence. A failed or partial run remains an immutable result, never rewritten into a pass.

- [ ] Treat all evaluation CLI and research CLI calls in this task as paid/networked. The aggregate ceiling for Task 12 is `US$100` and at most ten whole-report runs; every predeclaration allocates a smaller provider-request, token, wall-time, and currency sub-ceiling. Commit and push each predeclaration, then obtain explicit user confirmation immediately before its stage. Never infer confirmation from an earlier stage.
- [ ] In canary 01, run one controlled high-risk case per agent, then the controlled suite only if all six pass. Use these exact commands and capture LangSmith experiment IDs/direct UI links:

  ```powershell
  python -m deep_research.evaluation agent planner --case scoped-evidence-targets --tier controlled --verbose
  python -m deep_research.evaluation agent researcher --case read-bearing-acquisition --tier controlled --verbose
  python -m deep_research.evaluation agent source-evaluator --case work-role-independence --tier controlled --verbose
  python -m deep_research.evaluation agent fact-checker --case upstream-independent-pair --tier controlled --verbose
  python -m deep_research.evaluation agent synthesizer --case canonical-evidence-report --tier controlled --verbose
  python -m deep_research.evaluation agent critic --case typed-gap-calibration --tier controlled --verbose
  python -m deep_research.evaluation suite --tier controlled --verbose
  ```

  Any `judge_provider_failure`, missing experiment ID, or deterministic gate failure makes canary 01 fail.

- [ ] In canary 02, run each agent's registered live case once with these exact commands. Require trace evidence that Researcher and Fact Checker use reads, Source Evaluator and Synthesizer stay tool-free, and Critic performs no discovery calls.

  ```powershell
  python -m deep_research.evaluation agent planner --tier live --verbose
  python -m deep_research.evaluation agent researcher --tier live --verbose
  python -m deep_research.evaluation agent source-evaluator --tier live --verbose
  python -m deep_research.evaluation agent fact-checker --tier live --verbose
  python -m deep_research.evaluation agent synthesizer --tier live --verbose
  python -m deep_research.evaluation agent critic --tier live --verbose
  ```
- [ ] In canary 03, run the narrow document-rich smoke question `What constraints do recent official U.S. reports identify for utility-scale battery storage interconnection?` Its purpose is transport and evidence-spine validation, not release acceptance. Require read-bearing evidence IDs to survive through report and ledger, zero handoff failures, and successful document fallback.
- [ ] In canary 04, run the blind broad question `What are the current constraints on grid-scale battery storage deployment?` Do not seed LBNL, IEA, NREL, EIA, CAISO, or any other holdout URL in the plan or prompt.
- [ ] In canary 05, run the comparative/conflict question `What does the current evidence say about whether return-to-office mandates reduce commercial office vacancy in major U.S. cities?`
- [ ] In canary 06, run the time-sensitive question `What are the current regulatory and grid-connection constraints on data-center expansion in the United States?`
- [ ] In canary 07, run the document-fallback question `What constraints do recent official U.S. reports identify for heat-pump deployment?` This case must exercise legitimate document/API/alternate-source acquisition after denied HTML without bypassing access controls.
- [ ] For each whole-report run, invoke the CLI in strict mode with its literal predeclared question. Canary 04 uses:

  ```powershell
  python -m deep_research "What are the current constraints on grid-scale battery storage deployment?" --config config.yaml --verbose --require-quality
  ```

  Canaries 03, 05, 06, and 07 use the same flags with the literal questions above.

- [ ] Record critic score, coverage, verified cluster count, selected independent publisher/work pairs, semantic duplicates, reason completeness, report/ledger hashes, request attempts by provider, tokens, elapsed time, ceiling use, exit code, and every unexplained error class.
- [ ] Apply all release gates per broad live run: critic `>=7`; deterministic reader quality `>=0.80`; coverage `>=0.80`; no semantic duplicates or conflicting verdicts for one cluster; every settled load-bearing point has two eligible publishers and works; every insufficient claim has a reason; no evidence handoff failures; reader/ledger alignment; run remains inside its predeclared cost/latency/request SLO.
- [ ] After each whole-report run completes, dispatch a fresh Luna Max fast **output-quality reviewer** that did not implement the task and was not given holdout URLs before the run. It selects three to five authoritative machine-readable sources, checks every settled reader claim against them, identifies major omitted constraints, verifies source/work independence, and scores factual correctness, completeness, prioritization, uncertainty, and readability on `[0,1]`. Record its sources and claim-by-claim disposition in the results document. Any false settled claim, fabricated citation, missed direct contradiction, or overall score below `0.80` fails the run regardless of the in-graph Critic.
- [ ] Stop immediately on credential/provider failure, ceiling exhaustion, a handoff-integrity failure, or two consecutive runs failing the same unchanged gate. Diagnose before proposing another run; do not compensate by raising budgets or weakening criteria.
- [ ] If acquisition fails but a separately authorized seeded diagnostic succeeds, classify the production failure as acquisition and keep the blind run failed. The seeded run never satisfies release readiness.
- [ ] After each results record is complete, commit it with the exact stage message `docs(validation): record production canary 01` through `docs(validation): record production canary 07`, dispatch a fresh Luna Max fast evidence reviewer, fix only documentation inaccuracies, and push after approval.
- [ ] Run the final offline gate again at the exact release candidate SHA: `python -m pytest -q`, `python -m ruff check src tests`, and `git diff --check`.
- [ ] Write the final readiness record with base/head SHAs, commit list, task review dispositions, controlled matrix, every live run, artifacts/hashes, SLOs, unresolved limitations, and one honest verdict: `READY`, `CONDITIONALLY READY`, or `NOT READY`. `READY` requires every gate above; `CONDITIONALLY READY` cannot be used to bypass strict CLI acceptance.
- [ ] Commit the final record with message `docs(validation): record agent production readiness`.
- [ ] Dispatch one final Luna Max fast documentation/evidence review, push only after it is clean, then stop for the user-owned whole-branch review. Do not merge the branch.

---

## Final Acceptance Checklist

- [ ] Planner emits bounded scope/as-of and atomic evidence targets with one memory call at most.
- [ ] Researcher cannot spend an entire loop searching without reading or explicitly terminating with no candidate.
- [ ] Every new finding is tied to a successful read, exact excerpt, locator, publisher, and work identity candidate.
- [ ] Source Evaluator distinguishes source quality from source independence and classifies mirrors/derivatives/self-interest.
- [ ] Fact Checker receives every claim-linked upstream passage, can verify upstream A+B without retrieving C, and enforces two publishers plus two works locally.
- [ ] Same-work mirrors never count as corroboration; unknown identity fails closed.
- [ ] Semantic claim duplicates are merged or block acceptance; exact and semantic duplicate metrics are labeled separately.
- [ ] Every insufficient claim has one mechanical reason and one auditable boundary classification.
- [ ] Synthesizer ranks only mechanism-bearing constraints, cites selected verification sources, and keeps internal IDs out of the reader report.
- [ ] Critic is tool-free in the production graph, calibrated monotonically, and returns targetable typed gaps.
- [ ] Refinement touches only affected targets and stops after deterministic no progress.
- [ ] CLI default output is concise, strict mode retains exit `4`, debug events remain available, and report+ledger stay aligned.
- [ ] Controlled cases pass three repetitions without averaging away a failure.
- [ ] Blind live matrix clears every evidence, coverage, critic, integrity, and SLO gate without seeded holdouts.
- [ ] Every task and live record has a clean Luna Max fast review and is pushed only after review completion.

## Handoff

Recommended execution mode: **Subagent-Driven Development** in this task, sequentially from Task 0. Dispatch one Luna Max fast implementer, then a fresh Luna Max fast reviewer, resolve findings, and push before starting the next task. Use **Inline Execution** only if subagent dispatch is unavailable; preserve the same RED/GREEN/review/push gates.
