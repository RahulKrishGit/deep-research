# CLI Report Quality and Agent Output Integrity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make an end-to-end CLI run produce a concise, comprehensive, auditable research report whose quality status is visible to both people and automation.

**Architecture:** Keep the six-agent graph and the provider-native structured transport, but stop treating every refinement as an append-only new research run. Research findings remain an evidence ledger; source assessments and checked claims become canonical snapshots keyed by stable identity. Search results discover sources, while only read content can support findings or verification. The Synthesizer writes a concise, claim-linked reader report plus a separate evidence ledger. A deterministic quality assessment gates the Critic and drives exact refinement work; the CLI streams progress and reports quality, coverage, provenance, and artifact paths.

**Tech Stack:** Python 3.11+, Pydantic v2, LangGraph 1.2+, DeepSeek/OpenAI providers, Tavily, Beautiful Soup, pdfplumber, `tldextract`, pytest/pytest-asyncio, Ruff, Markdown.

**Spec:** `docs/superpowers/specs/2026-07-25-agentic-deep-research-design.md`, `docs/superpowers/specs/2026-07-25-15-end-to-end-verification-and-hardening-design.md`, and the follow-up whole-report evaluation called for by `docs/superpowers/specs/2026-08-16-individual-agent-evaluation-design.md`.

**Evidence artifact:** `docs/reports/cli-run-2026-09-13-grid-scale-battery-storage-854cddd3.md` at branch head `c4eff363b5c0f56793bf09a13a59df58866f1f79`.

## Review result this plan must correct

The branch materially improves structured-output and native-tool-call transport, but transport readiness is not report readiness. The committed CLI report is incomplete, bloated, and not sufficiently auditable:

- The branch differs from its merge base in 136 files, with 35,616 insertions and 1,035 deletions. Most of that work concerns provider boundaries, native ReAct shape, individual-agent evaluation, and readiness documentation—not whole-report quality.
- The final report is 127,872 bytes and 16,120 words. Its source appendix alone is 96,032 characters, or 75.3% of the report.
- The appendix has 257 rows for 101 canonical URLs: 156 duplicate URL rows. Two hundred twenty-one rows are shown as score `0.20`, confidence `low`; 209 of those merely fell past the scoring cap and 12 reflect a scoring-provider failure. Operational non-evaluation is therefore presented as a source-quality judgment.
- The report lists 101 citations but uses only 12 citation numbers before the Citations section. Eighty-nine listed sources do not support any visible report sentence or checked claim.
- Refinement repeatedly appends the same source assessments and claims. The verified-claim section contains repeated facts with different confidence values, even where superficial string normalization hides some semantic duplicates.
- The Critic receives only the first 6,000 report characters. In this artifact that excludes the entire Verified claims, Uncertainty, Limitations, Citations, and Source appendix sections, so it cannot review the evidence and disclosure surfaces its rubric asks it to judge.
- Six major constraint families are explicitly left unsupported: trade/tariffs, siting and safety permitting, market rules, non-U.S. grid regimes, equipment lead times, and financing. The answer is therefore a partial U.S.-heavy report rather than a complete answer to the unqualified question.
- The report discloses generic process failure but does not identify which agent/stage failed, which sub-topic was affected, how many attempts failed, or what evidence was lost.
- The branch contains no captured CLI stdout, structured run summary, or trace export for the committed session. The final Markdown alone is not enough to attribute every runtime failure after the fact.
- `git diff --check` fails on committed trailing whitespace in several review/specification documents.
- The current checkout compiles with `python -m compileall -q src tests`. The current environment does not have `pytest`, so the head was not freshly test-run here. A committed readiness artifact records `2511 passed, 1 deselected` at an earlier reviewed head; that evidence must not be relabeled as a test of `c4eff363`.

## Agent assessment and priority

| Priority | Agent / layer | What is lacking | Why it appears in the report |
| --- | --- | --- | --- |
| P0 | Source Evaluator | Re-scores cumulative state, appends duplicate snapshots, caps the whole run at 12, fabricates `0.20` for unscored sources, and calls topic overlap “corroboration.” | 257 appendix rows, 221 false-low rows, repeated URLs, authoritative sources mislabeled because they were never judged. |
| P0 | Fact Checker | Re-extracts from the oldest 40 findings, checks at most five each pass, has no claim identity, treats search hits as retrieved evidence, counts hostnames rather than publishers, and stores prose evidence without URLs. | Duplicate facts with different confidence, invisible verification sources, and “verified” claims citing only the page that made the claim. |
| P0 | Researcher | Attempts only three planned sub-topics per pass, accepts search snippets as evidence, does not require source reading or a primary-source hierarchy, and can spend research budget writing unchecked memory. | Broad source noise, shallow evidence, silent low-priority omissions, and six unanswered constraint families despite URLs being discovered. |
| P1 | Synthesizer / renderer | Consumes first-in cumulative slices, can narrate unchecked findings, cites only at section granularity, renders every historical source assessment, and publishes/saves memory before Critic acceptance. | Dense paragraphs with ambiguous citations, an enormous duplicate appendix, unused citations, and interim/stale material persisted as if final. |
| P1 | Critic / refinement | Reviews a 6,000-character prefix, receives duplicate source context and only an error count, emits free-text gaps that are matched to sub-topics by substring, and must stop at the iteration cap regardless of hard defects. | It notices gaps but cannot inspect the full report or reliably route work to close them; the final artifact remains unaccepted at budget exhaustion. |
| P1 | Planner | Uses an overly broad decomposition and forbids new capitalized terms/four-digit years in queries. It does not make scope, as-of date, geography, source class, or measurable success explicit. | Search cannot deliberately target FERC, NFPA, UL 9540A, FEOC, jurisdictions, or current-year primary material unless those tokens appeared in the question. |
| P2 | CLI / outcome | Does not pass the existing live event handler, prints progress only after completion, exposes no quality/coverage metrics, groups no errors by agent, and returns success for a report the Critic did not accept. | The user sees a path and generic status but cannot tell whether the report is complete, why it is partial, or which subsystem needs attention. |
| P2 | Evaluation | Evaluates six agents independently and explicitly forbids a full-graph evaluation in the current package. | Cross-agent duplication, provenance loss, refinement failure, and renderer bloat passed individual gates and were caught only by a manual CLI run. |

## Global constraints

- Work from the repository root on `codex/cross-agent-planner-fix-parity`. At the start of each task record `git status --short --branch`, `git rev-parse HEAD`, and `git rev-list --left-right --count origin/main...HEAD`.
- Rebase/merge the 71 commits currently missing from the branch only in a separately reviewed integration step. Do not mix conflict resolution with report-quality behavior changes.
- Preserve the current fail-closed provider boundary, native structured output, multiple native tool calls per turn, one bounded structured repair, redaction rules, and content-free failure telemetry.
- Correct `NATIVE_REACT_RESPONSE_CONTRACT` to describe the already-implemented multi-call behavior, but do not reintroduce prompt-encoded JSON/DSML tool decisions.
- Use TDD for every behavior change: write the focused failing test, run it and confirm the intended failure, implement the minimum change, rerun the focused test, then run the task gate.
- Keep search results as discovery records. Only content returned by `web_scraper`, `document_reader`, or a provenance-bearing memory record may support a finding or fact-check verdict.
- Never manufacture numeric source-quality scores for sources that were not evaluated. “Unscored” is a status, not a low score.
- Corroboration is a claim-level relation between independently owned sources. Two sources discussing the same broad sub-topic do not corroborate one another.
- Every load-bearing narrative point must identify the checked claim(s) and exact source URL(s) that support it. Section-level source piles are insufficient.
- The reader report must contain only unique cited sources. Full research/audit detail belongs in the companion evidence ledger.
- Do not persist claims to long-term memory until the graph has reached a terminal route and deterministic quality gates permit publication.
- Keep the existing individual-agent evaluation package independent of the graph. Add the full-graph campaign in a new `deep_research.e2e_evaluation` package so the old scope contract remains true.
- All live DeepSeek, OpenAI, Tavily, LangSmith, or other paid/network campaigns require a predeclared request ceiling and explicit human authorization. Tasks 1–9 are offline.
- End every implementation task with focused tests, Ruff on touched Python, `git diff --check`, and one independently reviewable commit. Stage exact paths only.

---

### Task 1: Correct shared ReAct wording and add canonical evidence identities

**Files:**
- Modify: `src/deep_research/agents/prompts.py`
- Modify: `src/deep_research/utils/types.py`
- Create: `src/deep_research/agents/identity.py`
- Test: `tests/test_agents/test_prompts.py`
- Test: `tests/test_types.py`
- Create: `tests/test_agents/test_identity.py`

**Interfaces:**
- Adds: `finding_fingerprint(finding: Finding) -> str`
- Adds: `claim_fingerprint(text: str) -> str`
- Adds: `deduplicate_findings(findings: Sequence[Finding]) -> list[Finding]`
- Adds: `merge_source_snapshot(previous, current) -> list[ScoredSource]`
- Adds: `merge_claim_snapshot(previous, current) -> list[Claim]`
- Changes: `evaluated_sources` and `verified_claims` state updates replace canonical snapshots; they no longer append historical copies.

- [ ] **Step 1: Pin the multi-call response contract.** Replace the obsolete sentence “Call at most one tool” with:

```python
NATIVE_REACT_RESPONSE_CONTRACT = (
    "Call one or more tools supplied with this request when independent "
    "lookups or actions are needed. Use provider-native tool calling; never "
    "write or imitate a tool call in text, JSON, XML, DSML, or a Markdown "
    "fence. When no tool is needed, return the final answer directly."
)
```

Add a test that the contract contains `one or more tools`, does not contain `at most one`, and still forbids textual tool protocols.

- [ ] **Step 2: Write RED identity tests.** Use punctuation/case/whitespace variants that must collapse, and materially different years/numbers that must remain distinct:

```python
def test_claim_fingerprint_collapses_formatting_but_preserves_facts() -> None:
    assert claim_fingerprint("Queue capacity fell in 2024.") == claim_fingerprint(
        "  queue capacity FELL in 2024  "
    )
    assert claim_fingerprint("Queue capacity fell in 2024.") != claim_fingerprint(
        "Queue capacity fell in 2025."
    )
```

`claim_fingerprint` must normalize Unicode, casefold, collapse whitespace, remove non-semantic punctuation, and hash the normalized text with SHA-256. It must not remove digits, units, negation, geography, or dates.

- [ ] **Step 3: Add source and claim snapshot merge tests.** The latest assessment for a canonical URL wins without changing first-seen order. The latest claim with the same fingerprint wins; a contradicted verdict wins over a previous verified verdict so stale positive judgments cannot survive.

```python
merged = merge_source_snapshot([source(url="https://EXAMPLE.test/a/")], [
    source(url="https://example.test/a", evaluation_status="scored", overall=0.91)
])
assert len(merged) == 1
assert merged[0].overall_score == 0.91
```

- [ ] **Step 4: Change state merge semantics.** Remove `evaluated_sources` and `verified_claims` from `_APPEND_STATE_FIELDS`. Require Source Evaluator and Fact Checker updates to carry their complete canonical snapshots. Keep `events`, `errors`, `raw_findings`, and the initial Planner `sub_topics` behavior append-only for now.

- [ ] **Step 5: Add finding de-duplication.** A finding key is `(canonical URL, normalized related_sub_topic, normalized content)`. When duplicates exist, keep the higher confidence; on a tie keep the earlier record. Do not use semantic embeddings or fuzzy thresholds in this task.

- [ ] **Step 6: Run the focused gate.**

```bash
python -m pytest tests/test_agents/test_prompts.py tests/test_types.py tests/test_agents/test_identity.py -q
ruff check src/deep_research/agents/prompts.py src/deep_research/utils/types.py src/deep_research/agents/identity.py tests/test_agents/test_prompts.py tests/test_types.py tests/test_agents/test_identity.py
git diff --check
```

Expected: all pass. Commit:

```bash
git add -- src/deep_research/agents/prompts.py src/deep_research/utils/types.py src/deep_research/agents/identity.py tests/test_agents/test_prompts.py tests/test_types.py tests/test_agents/test_identity.py
git commit -m "fix(agents): canonicalize cumulative evidence state"
```

---

### Task 2: Make Planner output a scoped coverage contract

**Files:**
- Modify: `src/deep_research/utils/types.py`
- Modify: `src/deep_research/agents/planner.py`
- Modify: `src/deep_research/agents/prompts.py`
- Test: `tests/test_agents/test_planner.py`
- Test: `tests/test_agents/test_planner_researcher_seam.py`
- Test: `tests/test_agents/test_tool_free_prompts.py`

**Interfaces:**
- Adds to `SubTopic`: `coverage_id: str`
- Adds to plan output: a stable locally assigned `coverage_id` such as `topic-01`.
- Preserves: 3–7 sub-topics and provider-facing `ResearchPlanDraft` compatibility.

- [ ] **Step 1: Write a RED prompt test for real search terms.** The production plan instruction must allow identifiers, institutions, acronyms, jurisdictions, and years that are necessary to find primary/current evidence even when absent from the user’s wording.

```python
assert "Do not introduce any capitalized word" not in PLAN_INSTRUCTION
for phrase in ("primary sources", "as-of date", "geographic scope", "measurable"):
    assert phrase in PLAN_INSTRUCTION
```

- [ ] **Step 2: Replace the harmful lexical restriction.** Use this contract:

```text
Search queries may introduce organizations, standards, laws, acronyms,
jurisdictions, and years needed to find authoritative current evidence.
Do not assert those terms as facts in the plan; use them only as search targets.
For an unqualified broad question, state the assumed scope and create distinct
sub-topics for materially different mechanisms rather than bundling them.
Each success criterion must name the evidence type, geography, and measurement
or decision needed to consider the sub-topic answered.
```

- [ ] **Step 3: Assign stable coverage IDs locally.** Do not ask the model to invent identifiers. After plan validation and priority sorting, stamp `topic-01`, `topic-02`, … in order. Add a test that a repair pass produces the same IDs for the same ordered titles.

- [ ] **Step 4: Strengthen the broad-question seam fixture.** Replace the all-placeholder Alpha/Beta fixture with five distinct constraint mechanisms. Assert all planned topics carry IDs, explicit source-oriented success criteria, and remain individually visible to the Researcher.

- [ ] **Step 5: Add an output-quality characterization case.** For the battery-storage question, a scripted valid plan should separate at least grid connection, supply chain/trade, siting/safety, market rules, and project economics. This is a contract fixture, not a domain-specific hard-coded production taxonomy.

- [ ] **Step 6: Run the focused gate.**

```bash
python -m pytest tests/test_agents/test_planner.py tests/test_agents/test_planner_researcher_seam.py tests/test_agents/test_tool_free_prompts.py -q
ruff check src/deep_research/utils/types.py src/deep_research/agents/planner.py src/deep_research/agents/prompts.py tests/test_agents/test_planner.py tests/test_agents/test_planner_researcher_seam.py tests/test_agents/test_tool_free_prompts.py
git diff --check
```

Commit:

```bash
git add -- src/deep_research/utils/types.py src/deep_research/agents/planner.py src/deep_research/agents/prompts.py tests/test_agents/test_planner.py tests/test_agents/test_planner_researcher_seam.py tests/test_agents/test_tool_free_prompts.py
git commit -m "feat(planner): make scope and coverage explicit"
```

---

### Task 3: Require the Researcher to read evidence and cover every planned topic

**Files:**
- Modify: `src/deep_research/agents/researcher.py`
- Modify: `src/deep_research/agents/toolset.py`
- Modify: `src/deep_research/runtime/assembly.py`
- Modify: `src/deep_research/utils/config.py`
- Modify: `config.yaml`
- Test: `tests/test_agents/test_researcher.py`
- Test: `tests/test_agents/test_planner_researcher_seam.py`
- Test: `tests/test_runtime/test_assembly.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Changes default: initial pass attempts all planned sub-topics, up to Planner’s maximum of seven.
- Adds: `MAX_FINDINGS_PER_SUB_TOPIC = 6`
- Adds: `MAX_UNIQUE_SOURCES_PER_SUB_TOPIC = 4`
- Removes `save_to_memory` from `ResearcherAgent.allowed_tools`.
- Changes `retrieved_finding_urls`: search-result URLs are discovery-only.

- [ ] **Step 1: Write the search-only RED regression.** A loop containing a successful `web_search` with five results but no page/document read must not call structured extraction and must produce no finding.

```python
run = ReActRun(agent_name="researcher", steps=[successful_search_step()])
assert retrieved_finding_urls(run) == ()
assert not any_finding_evidence(run)
```

- [ ] **Step 2: Define read-bearing evidence.** Count:

  - `web_scraper` only when it has non-blank `text` and a URL;
  - `document_reader` only when it has non-empty chunks and a source URL;
  - `query_memory` only when a match has non-blank content and `source_url`;
  - never `web_search` or `save_to_memory`.

Keep search payloads in the ReAct transcript for discovery/debugging, but omit them from the extraction evidence block except for candidate title/URL metadata.

- [ ] **Step 3: Require source hierarchy in the Researcher prompt.** Add these instructions without hard-coding a domain:

```text
Prefer primary sources: laws and regulator orders, standards bodies, official
datasets, original research, and issuer filings. Use secondary analysis to find
or interpret primary material, not as the default support for load-bearing
numbers. Read a source before reporting a finding from it. Record publication
date and geographic applicability when the source provides them.
```

- [ ] **Step 4: Attempt the complete plan.** Set the production default cap to seven, matching `MAX_SUB_TOPICS`. On refinements, order exact Critic gap targets first; then attempt planned topics whose success criteria remain unsatisfied. Every unattempted topic, regardless of priority, gets `researcher_sub_topic_skipped` with `coverage_id` and reason.

- [ ] **Step 5: Bound useful evidence, not planned coverage.** After extraction, canonicalize and de-duplicate. Retain no more than six distinct findings and four distinct source URLs per sub-topic, selecting higher-confidence findings first while preserving source diversity. Emit `findings_dropped_duplicate`, `findings_dropped_cap`, and `sources_retained` counts in the completion event.

- [ ] **Step 6: Remove premature memory writes.** Remove `save_to_memory` from the injected Researcher toolset and its system prompt. Add an assembly test proving Researcher receives only the four read/discovery tools.

- [ ] **Step 7: Update the seam test.** A five-topic plan must show five attempted topics, zero cap skips, and a finding or explicit no-findings error for every coverage ID. Add a separate provider-failure case that names every topic not attempted after the failure.

- [ ] **Step 8: Run the focused gate.**

```bash
python -m pytest tests/test_agents/test_researcher.py tests/test_agents/test_planner_researcher_seam.py tests/test_runtime/test_assembly.py tests/test_config.py -q
ruff check src/deep_research/agents/researcher.py src/deep_research/agents/toolset.py src/deep_research/runtime/assembly.py src/deep_research/utils/config.py tests/test_agents/test_researcher.py tests/test_agents/test_planner_researcher_seam.py tests/test_runtime/test_assembly.py tests/test_config.py
git diff --check
```

Commit:

```bash
git add -- config.yaml src/deep_research/agents/researcher.py src/deep_research/agents/toolset.py src/deep_research/runtime/assembly.py src/deep_research/utils/config.py tests/test_agents/test_researcher.py tests/test_agents/test_planner_researcher_seam.py tests/test_runtime/test_assembly.py tests/test_config.py
git commit -m "fix(researcher): require read evidence and complete coverage"
```

---

### Task 4: Separate source quality from evaluation status

**Files:**
- Modify: `src/deep_research/utils/types.py`
- Modify: `src/deep_research/agents/source_evaluator.py`
- Modify: `src/deep_research/agents/sources.py`
- Modify: `src/deep_research/agents/prompts.py`
- Modify: `src/deep_research/utils/config.py`
- Modify: `config.yaml`
- Test: `tests/test_agents/test_source_evaluator.py`
- Test: `tests/test_agents/test_sources.py`
- Test: `tests/test_types.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Adds: `SourceEvaluationStatus = Literal["scored", "unscored_cap", "unscored_provider", "unscored_missing"]`
- Changes `ScoredSource`: model-derived score fields and `overall_score` become `UnitScore | None`; adds `evaluation_status`.
- Removes: topic-overlap `corroboration_score` from `ScoredSource` and from source-quality weighting.
- Adds config: `source_evaluator.batch_size=12`, `source_evaluator.max_total_sources=36`.

- [ ] **Step 1: Write RED status tests.** An unscored source must validate with all numeric scores `None`, render as `not scored`, and never contribute to `average_score` or `low_confidence_count`.

```python
source = unscored_source(status="unscored_cap")
assert source.overall_score is None
assert average_score([source]) is None
```

- [ ] **Step 2: Remove false corroboration.** Delete `corroboration_score`’s use in source scoring and its claim of “independent domains.” Reweight only true source properties:

```python
AUTHORITY_WEIGHT = 0.45
RECENCY_WEIGHT = 0.15
RELEVANCE_WEIGHT = 0.40
```

Keep corroboration exclusively in the Fact Checker’s evidence relation. Add a test proving that two unrelated findings under the same sub-topic do not increase either source’s quality score.

- [ ] **Step 3: Batch unique sources.** Canonicalize groups once. Score them in deterministic batches of 12 until `max_total_sources`. Merge each URL once. A provider failure marks only the affected and remaining unscored URLs `unscored_provider`; it must not erase successful earlier batches.

- [ ] **Step 4: Preserve an explicit cap without fake numbers.** Sources above `max_total_sources` receive `evaluation_status="unscored_cap"`, no numeric score, and a generated explanation. Emit separate `scored_count`, `unscored_cap_count`, `unscored_provider_count`, and `unique_source_count` fields.

- [ ] **Step 5: Return the complete canonical snapshot.** Merge newly assessed sources with previous assessments by URL. A new scored record replaces an older unscored record. A provider failure does not replace a prior valid scored record with an unscored one.

- [ ] **Step 6: Update renderers used in prompts.** `render_source_quality` must emit one line per canonical URL and use `status=unscored_cap` instead of numeric placeholders. It must cap prompt material by relevance/usage, not blindly print historical copies.

- [ ] **Step 7: Run the focused gate.**

```bash
python -m pytest tests/test_agents/test_source_evaluator.py tests/test_agents/test_sources.py tests/test_types.py tests/test_config.py -q
ruff check src/deep_research/utils/types.py src/deep_research/agents/source_evaluator.py src/deep_research/agents/sources.py src/deep_research/agents/prompts.py src/deep_research/utils/config.py tests/test_agents/test_source_evaluator.py tests/test_agents/test_sources.py tests/test_types.py tests/test_config.py
git diff --check
```

Commit:

```bash
git add -- config.yaml src/deep_research/utils/types.py src/deep_research/agents/source_evaluator.py src/deep_research/agents/sources.py src/deep_research/agents/prompts.py src/deep_research/utils/config.py tests/test_agents/test_source_evaluator.py tests/test_agents/test_sources.py tests/test_types.py tests/test_config.py
git commit -m "fix(source-evaluator): distinguish unscored from low quality"
```

---

### Task 5: Make Fact Checker verdicts provenance-complete and idempotent

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/deep_research/utils/types.py`
- Modify: `src/deep_research/agents/sources.py`
- Modify: `src/deep_research/agents/fact_checker.py`
- Modify: `src/deep_research/agents/prompts.py`
- Test: `tests/test_agents/test_sources.py`
- Test: `tests/test_agents/test_fact_checker.py`
- Test: `tests/test_types.py`

**Interfaces:**
- Adds: `publisher_identity(url: str) -> str` using offline public-suffix data.
- Adds model: `EvidencePassage(source_url, source_title, locator, excerpt, stance)`.
- Adds to `Claim`: `claim_id`, `verification_evidence: list[EvidencePassage]`.
- Retains `Claim.source_urls` as the origin sources that made the claim for compatibility.
- Changes: verified/contradicted verdicts must be backed by provenance-bearing independent passages.

- [ ] **Step 1: Add `tldextract` without network refresh.** Add `tldextract>=5,<6` and create one module-level extractor:

```python
_EXTRACT = tldextract.TLDExtract(suffix_list_urls=())

def publisher_identity(url: str) -> str:
    host = urlsplit(normalize_source_url(url)).hostname
    if host is None:
        return normalize_source_url(url).casefold()
    parts = _EXTRACT(host)
    return ".".join(part for part in (parts.domain, parts.suffix) if part)
```

Add tests that `news.example.co.uk` and `docs.example.co.uk` are the same publisher while `example.co.uk` and `independent.org` are not. Replace the current incorrect comment that subdomain splitting can only under-count corroboration.

- [ ] **Step 2: Write RED search-only verification tests.** A search result from an independent publisher is a candidate, not evidence. The Fact Checker must scrape/read it before the verdict call. `retrieved_source_urls` must exclude plain search results.

- [ ] **Step 3: Make the provider return structured passages.** Change `ClaimVerdictDraft` to return:

```python
class EvidencePassageDraft(ContractModel):
    source_url: str
    source_title: str
    locator: str
    excerpt: str
    stance: Literal["supports", "contradicts"]

class ClaimVerdictDraft(ContractModel):
    verdict: str
    confidence: float
    passages: list[EvidencePassageDraft]
```

Only accept a passage URL present in read-bearing tool output. Clamp excerpts/locators to safe bounds and drop copied example URLs.

- [ ] **Step 4: Tighten local verdict resolution.** Apply in order:

  1. any valid independent contradicting passage → `contradicted`;
  2. one or more valid independent supporting passages and no contradictions → model may return `verified`;
  3. no valid independent passage → `insufficient_evidence`, confidence `0.0`;
  4. `unverified` is allowed only when evidence exists but does not settle the claim.

The presence of an independent hostname alone must never satisfy the gate.

- [ ] **Step 5: Stop rechecking the same old claims.** Build extraction input from all unique curated findings, grouped by coverage ID and ordered: uncovered topics, newly added findings, low-confidence/high-impact claims, then earlier material. Exclude claim fingerprints already in the canonical claim snapshot unless new evidence arrived or the Critic explicitly requested re-verification.

- [ ] **Step 6: Merge a canonical claim snapshot.** Stamp `claim_id=claim_fingerprint(text)`. Merge checked claims with the prior snapshot and return the complete list. Add a four-pass regression asserting one repeated fact produces one claim and a later contradiction replaces the earlier verified verdict.

- [ ] **Step 7: Expose verification provenance.** Events record support/contradiction passage counts and unique publisher counts. Never put full excerpts into event metadata or logs.

- [ ] **Step 8: Run the focused gate.**

```bash
python -m pytest tests/test_agents/test_sources.py tests/test_agents/test_fact_checker.py tests/test_types.py -q
ruff check src/deep_research/utils/types.py src/deep_research/agents/sources.py src/deep_research/agents/fact_checker.py src/deep_research/agents/prompts.py tests/test_agents/test_sources.py tests/test_agents/test_fact_checker.py tests/test_types.py
git diff --check
```

Commit:

```bash
git add -- pyproject.toml src/deep_research/utils/types.py src/deep_research/agents/sources.py src/deep_research/agents/fact_checker.py src/deep_research/agents/prompts.py tests/test_agents/test_sources.py tests/test_agents/test_fact_checker.py tests/test_types.py
git commit -m "fix(fact-checker): retain independent evidence provenance"
```

---

### Task 6: Produce a concise claim-linked report and separate evidence ledger

**Files:**
- Modify: `src/deep_research/agents/report.py`
- Modify: `src/deep_research/agents/synthesizer.py`
- Modify: `src/deep_research/agents/prompts.py`
- Modify: `src/deep_research/utils/types.py`
- Test: `tests/test_agents/test_report.py`
- Test: `tests/test_agents/test_synthesizer.py`

**Interfaces:**
- Replaces section-level prose with `ReportPoint(text, claim_ids, source_urls)`.
- Adds: `render_reader_report(...) -> str`
- Adds: `render_evidence_ledger(...) -> str`
- Adds to `SynthesizedReport`: `evidence_markdown`, `evidence_path`, unique counts.
- Reader report includes: as-of/scope, executive summary, constraint ranking, detailed findings, gaps/uncertainty, methodology, and references.

- [ ] **Step 1: Write the pathological RED fixture.** Construct 101 canonical sources represented by 257 source records and repeated claims. Assert the new renderer emits each canonical source and claim once, cites only used sources in the reader report, and puts reviewed-but-unused sources only in the evidence ledger.

```python
reader, ledger = render_reports(pathological_state())
assert reader.count("https://example.test/source-001") == 1
assert "Reviewed but not cited" not in reader
assert "Reviewed but not cited" in ledger
assert duplicate_claim_text(reader) is False
```

- [ ] **Step 2: Add claim-linked provider output.** Use a schema such as:

```python
class ReportPointDraft(ContractModel):
    text: str
    claim_ids: list[str]
    source_urls: list[str]

class ReportSectionDraft(ContractModel):
    title: str
    points: list[ReportPointDraft]

class ReportDraft(ContractModel):
    executive_summary: list[ReportPointDraft]
    ranked_constraints: list[ReportPointDraft]
    sections: list[ReportSectionDraft]
    uncertainty_notes: list[str]
```

Reject a settled point with no known checked claim or with a URL not present on those claims. Allow source-free text only in explicitly labeled gap/methodology fields.

- [ ] **Step 3: Build the synthesis evidence packet from canonical checked claims.** Do not send the first 40 raw findings. Include all unique checked claims up to a token/character budget by coverage, verdict, and impact. Raw findings may inform open questions but may not support settled narrative statements.

- [ ] **Step 4: Render inline citations.** Each bullet/paragraph ends with its own markers, derived from the point’s validated source URLs. Remove `Sources: [8][10]...` section-end piles. A sentence with three factual assertions must either share the exact same claim/source set or be split into separate points.

- [ ] **Step 5: Make the main report decision-friendly.** Render, in order:

  1. title plus `As of`, assumed geography/scope, and terminal quality status;
  2. a short executive summary;
  3. a constraint ranking table with constraint, deployment mechanism, geography, evidence strength, and confidence;
  4. detailed findings;
  5. evidence gaps and conflicting claims;
  6. a compact methodology/run summary;
  7. references containing only sources actually cited in the reader report.

Move the verbose verified-claim registry, complete source assessment table, verification passages, rejected evidence, and run-error inventory to `report-<session>-<iteration>-evidence.md`.

- [ ] **Step 6: Render status honestly.** Numeric fields are printed only when `evaluation_status="scored"`. Unscored sources show a status and reason. Low confidence means a real score below threshold, not provider/cap failure.

- [ ] **Step 7: Enforce concise structural bounds.** Add deterministic tests:

  - no duplicate canonical URL rows;
  - no duplicate claim IDs;
  - every reader citation resolves;
  - every settled point has at least one checked claim and cited source;
  - references equal the set of URLs used by reader points;
  - no empty “Sources: none cited” finding section;
  - the compact fixture’s appendix/reference material is below 35% of reader-report characters.

- [ ] **Step 8: Stop writing and saving memory inside synthesis.** This task composes both Markdown strings in state but does not publish artifacts or write long-term memory. Task 7 adds terminal publication.

- [ ] **Step 9: Run the focused gate.**

```bash
python -m pytest tests/test_agents/test_report.py tests/test_agents/test_synthesizer.py -q
ruff check src/deep_research/agents/report.py src/deep_research/agents/synthesizer.py src/deep_research/agents/prompts.py src/deep_research/utils/types.py tests/test_agents/test_report.py tests/test_agents/test_synthesizer.py
git diff --check
```

Commit:

```bash
git add -- src/deep_research/agents/report.py src/deep_research/agents/synthesizer.py src/deep_research/agents/prompts.py src/deep_research/utils/types.py tests/test_agents/test_report.py tests/test_agents/test_synthesizer.py
git commit -m "feat(reports): separate concise report from evidence ledger"
```

---

### Task 7: Add deterministic quality gates, targeted refinement, and terminal publication

**Files:**
- Create: `src/deep_research/agents/quality.py`
- Modify: `src/deep_research/utils/types.py`
- Modify: `src/deep_research/agents/critic.py`
- Modify: `src/deep_research/agents/researcher.py`
- Modify: `src/deep_research/agents/synthesizer.py`
- Modify: `src/deep_research/graph/state.py`
- Modify: `src/deep_research/graph/nodes.py`
- Modify: `src/deep_research/graph/orchestrator.py`
- Test: `tests/test_agents/test_quality.py`
- Test: `tests/test_agents/test_critic.py`
- Test: `tests/test_agents/test_researcher.py`
- Test: `tests/test_graph/test_state.py`
- Test: `tests/test_graph/test_nodes.py`
- Test: `tests/test_graph/test_orchestrator.py`

**Interfaces:**
- Adds: `ReportQualitySnapshot`
- Adds: `CritiqueGap(coverage_id, problem, recommended_queries)`
- Adds graph node: `finalize_report`
- Changes graph: `critic -> {refine -> researcher | finalize_report -> END}`.

- [ ] **Step 1: Define deterministic quality metrics.** Implement a pure function over `ResearchState` and synthesized structured content:

```python
class ReportQualitySnapshot(ContractModel):
    coverage_ratio: UnitScore
    planned_topics: int
    covered_topics: int
    unresolved_topic_ids: list[str]
    unique_findings: int
    unique_sources: int
    cited_sources: int
    scored_cited_source_ratio: UnitScore
    verified_claims: int
    contradicted_claims: int
    duplicate_claims: int
    duplicate_source_rows: int
    uncited_settled_points: int
    hard_failures: list[str]
```

For this phase, `covered` means a topic has at least one reader-report point backed by a checked claim. A topic may be intentionally unresolved only when the report names it in the gap section.

- [ ] **Step 2: Define hard gates.** The model score cannot override:

  - duplicate claims or source rows > 0;
  - unresolved citation markers;
  - uncited settled points > 0;
  - any cited source with non-`scored` evaluation status;
  - any contradicted claim presented as settled;
  - coverage ratio below `0.80` for a broad plan;
  - a missing scope/as-of declaration;
  - a missing reader report or evidence ledger.

Hard failures force refinement while budget remains. At budget exhaustion they set terminal status `partial`, not `accepted`.

- [ ] **Step 3: Replace free-text gaps with targetable gaps.** The provider-facing `CritiqueDraft` returns objects with optional `coverage_id`. Validate IDs against the plan. Unknown IDs become global gaps; known IDs route exactly to the Researcher. Remove title-substring matching.

```python
class CritiqueGapDraft(ContractModel):
    coverage_id: str
    problem: str
    recommended_queries: list[str]
```

- [ ] **Step 4: Give the Critic a complete, balanced view.** Pass the structured quality snapshot, all reader-report sections, canonical checked claims, cited-source assessments, and typed errors grouped by agent/stage. If character limits are needed, clamp each section separately so Summary, Findings, Uncertainty, Methodology, and References are always represented. Do not take a prefix of the combined Markdown.

- [ ] **Step 5: Make refinements delta-oriented.** Researcher consumes exact gap objects and first runs their recommended queries, then new queries needed to satisfy that topic’s success criteria. Source Evaluator scores only new/unscored URLs and returns a complete snapshot. Fact Checker checks only new/reopened claims and returns a complete snapshot. Synthesizer regenerates from canonical state.

- [ ] **Step 6: Add a terminal finalizer.** The finalizer writes reader and evidence Markdown with `write_document`. It saves unique high-confidence verified claims to memory only when terminal status is `accepted`; for `partial`, it writes artifacts but saves no claims unless a separate future policy explicitly allows it.

- [ ] **Step 7: Preserve terminal artifacts on write failure.** Markdown remains authoritative in state. Record separate reader/evidence write errors and paths. Never point the CLI at an earlier refinement artifact if the final write failed.

- [ ] **Step 8: Add the exact regression for the observed report.** Create a compact multi-iteration fixture where each pass rediscovers the same two claims and sources. After three refinements assert:

```python
assert len(final.evaluated_sources) == 2
assert len(final.verified_claims) == 2
assert final.quality.duplicate_source_rows == 0
assert final.quality.duplicate_claims == 0
assert publisher.memory_writes == 1
assert publisher.report_writes == 2  # reader + evidence, terminal only
```

- [ ] **Step 9: Run the focused graph gate.**

```bash
python -m pytest tests/test_agents/test_quality.py tests/test_agents/test_critic.py tests/test_agents/test_researcher.py tests/test_graph/test_state.py tests/test_graph/test_nodes.py tests/test_graph/test_orchestrator.py -q
ruff check src/deep_research/agents/quality.py src/deep_research/utils/types.py src/deep_research/agents/critic.py src/deep_research/agents/researcher.py src/deep_research/agents/synthesizer.py src/deep_research/graph/state.py src/deep_research/graph/nodes.py src/deep_research/graph/orchestrator.py tests/test_agents/test_quality.py tests/test_agents/test_critic.py tests/test_agents/test_researcher.py tests/test_graph/test_state.py tests/test_graph/test_nodes.py tests/test_graph/test_orchestrator.py
git diff --check
```

Commit:

```bash
git add -- src/deep_research/agents/quality.py src/deep_research/utils/types.py src/deep_research/agents/critic.py src/deep_research/agents/researcher.py src/deep_research/agents/synthesizer.py src/deep_research/graph/state.py src/deep_research/graph/nodes.py src/deep_research/graph/orchestrator.py tests/test_agents/test_quality.py tests/test_agents/test_critic.py tests/test_agents/test_researcher.py tests/test_graph/test_state.py tests/test_graph/test_nodes.py tests/test_graph/test_orchestrator.py
git commit -m "feat(graph): gate and publish final report quality"
```

---

### Task 8: Make CLI progress and quality actionable

**Files:**
- Modify: `src/deep_research/runtime/outcome.py`
- Modify: `src/deep_research/cli.py`
- Test: `tests/test_runtime/test_outcome.py`
- Test: `tests/test_cli/test_render.py`
- Test: `tests/test_cli/test_entrypoint.py`

**Interfaces:**
- Adds to `ResearchOutcome`: `quality`, `evidence_path`, `accepted`.
- Adds CLI flag: `--require-quality`.
- Adds exit code: `EXIT_QUALITY_UNACCEPTED = 4` when the opt-in flag is set.
- Uses the existing `event_handler` path for live progress.

- [ ] **Step 1: Add outcome derivation tests.** Build quality and both artifact paths from typed terminal state/events without parsing report prose. A failed final evidence write must yield `evidence_path=None`, not an earlier pass’s path.

- [ ] **Step 2: Stream progress during execution.** Pass a handler to `run_research_sync` that immediately renders allowed progress events. Do not reprint the same events after completion. In verbose mode, stream agent completion events and concise typed error events; never stream report bodies, provider text, or evidence excerpts.

- [ ] **Step 3: Render the quality summary first.** After session identity/status, print:

```text
Quality: partial (critic 6/10; 3/7 topics covered, 43%)
Evidence: 12 cited sources; 10 scored; 14 verified, 1 contradicted
Integrity: 0 duplicate claims; 0 duplicate source rows; 0 uncited settled points
Open coverage: topic-03, topic-05, topic-06, topic-07
Report: output/report-...md
Evidence ledger: output/report-...-evidence.md
```

Use actual fields; omit the critic fragment if no model review exists.

- [ ] **Step 4: Group failures by agent.** Plain mode prints one line per affected agent with counts and affected coverage IDs. `--verbose` prints safe typed messages. Remove the current generic-only disclosure as the sole diagnostic.

- [ ] **Step 5: Keep backward-compatible exit behavior by default.** A partial report still returns 0 unless the graph failed. With `--require-quality`, return 4 unless terminal quality is accepted. Document that 1=config, 2=usage, 3=graph failure, 4=quality not accepted, 130=interrupt.

- [ ] **Step 6: Test real-time ordering with a fake runner.** The fake calls the supplied handler before returning. Assert progress text precedes the final summary and each event appears once.

- [ ] **Step 7: Run the focused CLI gate.**

```bash
python -m pytest tests/test_runtime/test_outcome.py tests/test_cli/test_render.py tests/test_cli/test_entrypoint.py -q
ruff check src/deep_research/runtime/outcome.py src/deep_research/cli.py tests/test_runtime/test_outcome.py tests/test_cli/test_render.py tests/test_cli/test_entrypoint.py
git diff --check
```

Commit:

```bash
git add -- src/deep_research/runtime/outcome.py src/deep_research/cli.py tests/test_runtime/test_outcome.py tests/test_cli/test_render.py tests/test_cli/test_entrypoint.py
git commit -m "feat(cli): surface live progress and report quality"
```

---

### Task 9: Add the deferred full-graph report-quality evaluation

**Files:**
- Create: `src/deep_research/e2e_evaluation/__init__.py`
- Create: `src/deep_research/e2e_evaluation/__main__.py`
- Create: `src/deep_research/e2e_evaluation/cases.py`
- Create: `src/deep_research/e2e_evaluation/evaluators.py`
- Create: `src/deep_research/e2e_evaluation/models.py`
- Create: `src/deep_research/e2e_evaluation/runner.py`
- Create: `tests/test_e2e_evaluation/test_cases.py`
- Create: `tests/test_e2e_evaluation/test_evaluators.py`
- Create: `tests/test_e2e_evaluation/test_runner.py`
- Modify: `tests/test_evaluation/test_scope.py`
- Modify: `README.md`

**Interfaces:**
- Adds command: `python -m deep_research.e2e_evaluation list|case|suite`.
- Reuses artifact/fingerprint/judge foundations from `deep_research.evaluation` without importing the graph into that existing package.
- Adds controlled multi-pass cases and separately authorized live cases.

- [ ] **Step 1: Preserve the existing evaluation scope.** Keep `src/deep_research/evaluation` graph-free. Amend its scope test only to assert that the new graph-owning package exists separately; do not weaken the original import guard.

- [ ] **Step 2: Define three controlled whole-report cases.** Include:

  - a broad constraints question that requires five distinct coverage topics;
  - a comparative question with conflicting sources and one contradicted claim;
  - a refinement case that initially lacks evidence for two topics and receives it in pass two.

All tools/providers are scripted and network-zero. At least one case must reproduce repeated source/claim output from multiple passes so the canonical snapshot gate is non-vacuous.

- [ ] **Step 3: Add deterministic evaluators.** Score/gate:

  - planned-topic accounting and coverage;
  - source-read provenance;
  - scored cited-source ratio;
  - checked-claim provenance;
  - citation resolution and claim linkage;
  - duplicate claim/source counts;
  - contradiction disclosure;
  - reader-report concision and evidence-ledger separation;
  - Critic refinement effectiveness;
  - terminal publication and memory timing;
  - CLI summary agreement with state.

Any integrity failure is a hard fail, regardless of an LLM judge score.

- [ ] **Step 4: Add a whole-report judge rubric.** The judge sees the research question, scoped coverage plan, reader report, deterministic metrics, and a bounded evidence-ledger summary. It scores completeness, prioritization, evidence quality, attribution, uncertainty, readability, and actionability. It never sees secrets, raw provider output, or hidden reasoning.

- [ ] **Step 5: Version and fingerprint the campaign.** Record graph revision, all six agent prompt fingerprints, report schema version, quality-gate version, case version, target/judge model settings, and request counts in the local artifact and LangSmith metadata.

- [ ] **Step 6: Set controlled acceptance criteria.** Three repetitions per controlled case must meet:

```text
all deterministic integrity gates pass
mean coverage >= 0.90; no repetition below 0.80
all cited sources scored
zero duplicate claims and source rows
zero uncited settled points
mean whole-report judge score >= 0.80; no repetition below 0.70
```

- [ ] **Step 7: Run only the offline campaign.**

```bash
python -m pytest tests/test_e2e_evaluation tests/test_evaluation/test_scope.py -q
python -m deep_research.e2e_evaluation suite --tier controlled --repetitions 3
ruff check src/deep_research/e2e_evaluation tests/test_e2e_evaluation tests/test_evaluation/test_scope.py
git diff --check
```

No real provider/search credentials may be loaded by this gate.

- [ ] **Step 8: Document, then commit.** README must explain individual-agent vs whole-graph gates, controlled vs live tiers, `--require-quality`, and the two report artifacts.

```bash
git add -- src/deep_research/e2e_evaluation tests/test_e2e_evaluation tests/test_evaluation/test_scope.py README.md
git commit -m "feat(evaluation): add whole-report quality campaign"
```

---

### Task 10: Clean the branch, verify the current head, and run an authorized canary

**Files:**
- Modify: only files reported by `git diff --check` for whitespace cleanup.
- Create after authorized live run: `docs/superpowers/YYYY-MM-DD-cli-report-quality-live-validation.md`
- Update: `docs/superpowers/2026-09-08-cross-agent-planner-fix-parity-fix-log.md`

- [ ] **Step 1: Fix existing diff hygiene separately.** Remove the trailing whitespace currently reported in the September 10–12 review/specification Markdown. Do not rewrite prose. Run `git diff --check` and commit only those paths:

```bash
git commit -m "docs: clean branch diff whitespace"
```

- [ ] **Step 2: Install the repository development environment and run the complete offline gate at the final candidate head.**

```bash
python -m pip install -e '.[dev]'
python -m pytest -q
ruff check . --exclude tools,.deepseek-runs
python -m compileall -q src tests
git diff --check
git status --short --branch
```

Record exact pass/fail/deselection counts. Do not cite the earlier 2,511-test artifact as evidence for this head.

- [ ] **Step 3: Run a mocked CLI acceptance.** Capture stdout and both Markdown artifacts for the battery-storage-shaped controlled fixture. Assert stdout metrics equal state and artifact metrics. Compare against the observed pathology:

```text
duplicate URL rows: 156 -> 0
false numeric scores for unscored sources: 221 -> 0
unused sources in reader references: 89 -> 0
critic-visible required sections: 2/7 -> 7/7
duplicate claim IDs: >0 -> 0
```

- [ ] **Step 4: Predeclare the live canary.** Before any network call, write the candidate SHA, exact question set, repetitions, DeepSeek request ceiling, Tavily request ceiling, Judge request ceiling, expected cost, credential sources, stop conditions, and artifact locations. Suggested representative questions:

  - current constraints on grid-scale battery deployment;
  - compare two interventions where benefits, harms, costs, and regional applicability matter;
  - a policy question with a current as-of date and contradictory evidence.

- [ ] **Step 5: Stop for explicit authorization.** Do not infer authorization from the implementation plan. Present the inventory and wait.

- [ ] **Step 6: After authorization, run once and do not rerun a failed gate.** Compare against the last committed CLI artifact using structural metrics and an independent whole-report judge. A failure produces diagnosis and a new amendment; it does not trigger ad hoc prompt edits or an unplanned second live batch.

- [ ] **Step 7: Apply live release criteria.** Every canary must have:

  - terminal `accepted` quality;
  - coverage ≥ 0.80 and every planned topic accounted for;
  - zero duplicate claims/source rows and zero unresolved citations;
  - every cited source genuinely scored and every settled point claim-linked;
  - all verification passages provenance-bearing and independently published;
  - reader report ≤ 8,000 words unless the question explicitly requests longer;
  - backmatter ≤ 35% of reader-report characters;
  - no unexplained agent error and no generic-only limitation;
  - whole-report judge score ≥ 0.80;
  - CLI summary exactly matching artifacts and state.

- [ ] **Step 8: Write the validation record and request review.** Include exact commands, candidate SHA, request counts, failures, output metrics, artifact hashes/paths, and comparison table. Then use `superpowers:requesting-code-review`; address findings with `superpowers:receiving-code-review` and rerun the complete offline gate before any merge decision.

---

## Configuration experiments after structural correctness

Do not use token ceilings or prompt wording as substitutes for Tasks 1–9. Once the controlled whole-report suite passes, test these one at a time with fixed seeds/fixtures and predeclared live budgets:

| Experiment | Current | Candidate | Accept only if |
| --- | ---: | ---: | --- |
| Tavily search depth | `basic` | `advanced` | primary-source retrieval and coverage improve enough to justify latency/cost. |
| Tavily results | `5` | `8` | source quality/coverage improves without increasing unused-source noise. |
| Research observation summary | `200` chars | `600` chars | tool-loop completion and source-read selection improve without prompt overflow. |
| Factual structured-call temperature | global `0.7` | operation-specific `0.0–0.2` | repetitions become more consistent with no quality regression. |
| Graph refinement budget | `3` | unchanged first | increase only if targeted refinements close gaps and cost per accepted report remains acceptable. |

The global `32768` output ceilings solved transport/truncation failures; they are not a reason to allow oversized evidence lists or reports. Keep them until the existing live shape gates are deliberately revalidated.

## Final acceptance checklist

- [ ] The current branch is reconciled with `main` in a dedicated integration change and has no unresolved conflict or accidental regression.
- [ ] Full offline tests, Ruff, compilation, `git diff --check`, and clean-status checks pass at the exact candidate SHA.
- [ ] Source assessments and claims are canonical snapshots across refinements.
- [ ] Search snippets cannot become findings or verification evidence.
- [ ] Unscored sources have no numeric quality score.
- [ ] Claim verification retains independent source URL, publisher, locator, excerpt, and stance.
- [ ] Every planned coverage ID is answered or explicitly unresolved.
- [ ] Reader-report points are claim-linked and cited inline.
- [ ] Reader references contain only used, unique sources.
- [ ] Evidence ledger contains unique source/claim records and typed agent errors.
- [ ] Critic sees all required report surfaces plus deterministic quality metrics.
- [ ] Hard integrity gates cannot be overridden by the model score or iteration exhaustion.
- [ ] Only terminal publication writes report artifacts and accepted memory.
- [ ] CLI streams progress and reports quality, coverage, integrity, per-agent errors, and both paths.
- [ ] `--require-quality` gives automation a non-zero exit for partial reports.
- [ ] Controlled whole-graph evaluation passes all three repetitions per case.
- [ ] Any live canary was separately authorized, bounded, recorded once, and met the release criteria.

## Execution recommendation

Tasks 1–5 change foundational contracts and must be executed in order. Task 6 depends on canonical source/claim provenance. Task 7 depends on the new report model. Task 8 depends on the terminal quality/outcome contract. Task 9 evaluates the completed pipeline. Task 10 is the only live-release stage.

Do not parallelize tasks that edit `utils/types.py`, `agents/prompts.py`, or graph state. After Task 5, independent reviewers may audit the source/evidence contracts while implementation proceeds, but their findings must be integrated before Task 6 begins.
