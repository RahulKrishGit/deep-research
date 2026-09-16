# Evidence Integrity and Agent Production Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Plan date / amendment:** 2026-09-16. Astra-authored review of dd1b93f, cross-run amendment e2cbb8f, and the last run's 1,079 LangSmith spans. Rebased onto merged main b2fa96b at the user's explicit request. This is the single execution plan, amended in place, not another parallel proposal.

**Goal:** Make all six agents cooperate to produce accurate, substantive, current, well-supported, readable CLI reports, and demonstrate that quality through real-agent tests and independently reviewed end-to-end outputs.

**Architecture:** Preserve the six research agents. Introduce a shared persisted read-to-claim evidence contract, target-aware acquisition and verification, evidence-bound editorial composition, and targeted refinement. Separate the Critic's iterative editing role from a tool-free terminal report-review service. Both inspect the actual report; deterministic code owns provenance checks, status, routing, and publication. Structural proxy scores do not establish semantic quality.

**Tech Stack:** Existing Python 3.11+, Pydantic v2, asyncio, LangGraph, configured DeepSeek/OpenAI providers, HTTPX, BeautifulSoup, pdfplumber, pytest/pytest-asyncio, Ruff, LangSmith, Markdown. Reuse dependencies; no new search vendor, browser automation dependency, identity service, or vector database is required.

## Global constraints and execution decisions

- Work in .worktrees/agent-cli-quality-trace-plan on codex/agent-cli-quality-trace-plan, created from freshly fetched origin/main at b2fa96bd1121bafd3775374e032cf5a817c0814d. The prior branch is merged. Historical run application SHA remains 2bc6665de59d0cec3d76f0bd0cdc5bd92ba3b180 and trace/docs SHA 935cc9e09dc4f5659d0c3acd44f40236e76bab79; those identify evidence, not the new execution base. Check branch/current diff before every task.
- Preserve the root checkout's unrelated files and all old worktrees, ignored outputs, and memories. Historical output artifacts remain in .worktrees/cross-agent-planner-fix-parity; locate/hash them explicitly, do not fabricate missing copies in the new worktree. Stage exact task paths only.
- Preserve merged-main behavior, including removal of Streamlit and reduced public trace payloads. Review the existing latest_claims/latest_scored_sources helpers before adding canonical projections; define one effective projection per contract. Do not reintroduce UI code, duplicate conflicting projection rules, or full thought/prompt logging.
- This amendment is planning only. Execution starts at Task 0 when requested. Do not implement application code while reviewing this document.
- Quality is the scope. Do not add unrelated security, deployment, authentication, monitoring, or operational-hardening projects. Preserve existing secret handling, access controls, request ceilings, and interrupts without expanding them.
- Preserve critic acceptance >=7/10, substantive broad coverage >=0.80, whole-report semantic review >=0.80, zero false settled claims, zero fabricated/unresolved citations, reader length <=8,000 words unless explicitly requested otherwise, and backmatter <=35%. Structural cleanliness is necessary, not sufficient.
- Astra is the sole plan author and plan reviewer, as explicitly requested; no Sol/High browser relay or delegated planning. During implementation retain the user's Luna Max code dispatches and available fast/priority execution, with fresh task reviews. Record effective dispatch settings; do not claim a fast-mode toggle was enabled if the interface cannot confirm it.
- Execute sequentially because contracts/state are shared. Require fresh task reviews for specification compliance and code quality. Resolve important/critical findings, rerun gates, then push reviewed commits. Do not merge; final whole-branch review remains user-owned.
- Use RED → GREEN → REFACTOR. Test excerpts specify minimum regressions, not the complete inventory. Add each named adversarial case separately. Split multi-action implementation steps into small test/change/test commits.
- Through Task 12, tests are offline with scripted provider responses and fake external clients. Replace external boundaries, not production agents, handoffs, renderers, or gates.
- Task 13 is paid/live. Historical US$100 / approximately ten-run authorization is not a fresh allowance. Reconcile remaining authority and obtain required stage confirmation before spending. No paid calls are needed for this plan amendment.
- Tokens, searches, transport attempts, tool calls, dropped requests, and event rows are distinct. Remove the previous plan's unvalidated 250k/500k-token and 15/30-minute quality gates: measure efficiency and obey authorized limits, but do not sacrifice output to invented release SLOs.
- Historical results remain immutable. A source denial, failed judge, missing trace, partial answer, or seeded diagnostic is never relabeled as a successful unseeded production run.
- This plan supersedes unfinished Tasks 16–21 of docs/superpowers/plans/2026-09-15-cli-live-canary-amendment.md and explicitly amends output-quality interpretations in docs/superpowers/specs/2026-09-15-production-ready-reports-design.md below. Earlier completed fixes remain.
- No plan guarantees perfect answers to arbitrary questions. Completion means the declared quality matrix passes at the exact candidate, observed important output defects are closed, and evidence limitations are correctly handled. Do not replace evidence with an “absolute production-ready” assertion.

## 1. Evidence critique

### What the repository establishes

Latest local evidence: output/report-417fa9338e10450784b459f89af98b1c-3.md, its -3-evidence.md ledger, and output/cli-canary-20260915-223507-evidencepooling.log. Outcome: partial, critic 5/10, coverage 4/6, 16 checked claims, 5 provisionally verified, approximately 815,664 tokens. The report repeats queue/PJM/report-cutoff facts; every ranked deployment mechanism says “not stated”; internal C001/C011 IDs leak; current constraints rely largely on old statistics and a future cost projection; wholesale-market and technical-performance questions are unanswered.

The last-run trace was freshly queried in this amendment: 157 Researcher searches versus 20 HTML/PDF read calls, 121 Fact Checker searches versus 58 read calls, and 29 Critic searches without page-read capability. These counts exclude 64 memory queries. The code wrongly admits ordinary memory as read-bearing evidence, so historical independent_sources and admitted-URL counts cannot be treated as validated fresh-publisher counts. Document reads succeeded 44/45 and HTML reads 15/33. Access denial is real but is not the whole failure.

The bounded, source-anchored review is [Last-run agent trace review](../validation/2026-09-16-last-run-agent-trace-review.md). It records exact trace IDs, artifact hashes, per-agent measurements, code-confirmed defects, and unavailable payloads. Full historical LLM prompts/drafts and document bodies were not present; this plan does not invent them.

| Inspected evidence / code | Defensible conclusion | Unsupported inference to avoid |
| --- | --- | --- |
| Last-run trace, initial state; agents/steps.py:_read_payload_urls; runtime/memory_bridge.py; tests/test_agents/test_steps.py | Ordinary recalled text plus a URL is admitted as a source read, extraction evidence, and a candidate independent publisher. An offline reproduction confirms all three; previous-session duplicate facts were present in this run. | That all "read-backed" claims were based on freshly opened sources, or that historical memory counts establish independent corroboration. |
| agents/react.py:_tool_observation; agents/base.py:_record_step; config.yaml | Next-action feedback is a 200-character payload prefix; extraction separately uses 4,000-character prefixes. Decision context and extraction selection both require repair. | That increasing final extraction size alone fixes candidate selection or repeated reads. |
| Last-run Researcher spans; researcher.py:_refinement_satisfied_sub_topics | Eleven of twenty passes emitted no findings; wholesale produced zero across four passes; any prior finding plus no Critic gap can suppress further topic research. | That a raw finding or an omitted Critic gap proves the topic answered. |
| docs/superpowers/validation/2026-09-15-verification-boundary-diagnosis.md; agents/fact_checker.py:valid_verification_passages, claim_verification_messages, verify_claim | Upstream URLs are admitted, but upstream excerpts are absent from the adjudication prompt; a new-independent-retrieval precondition remains. | That this explains every failure. One URL per claim cannot distinguish missing acquisition from extraction loss. |
| agents/researcher.py:render_evidence, build_findings, bound_sub_topic_findings | Extraction relies on bounded payload prefixes and URL admission, not exact excerpt validation. Retention does not reserve each target's support. | That prompt instructions or more aggregate reads establish corroboration. |
| agents/fact_checker.py:DEFAULT_MAX_CLAIMS and the accepted-claims slice | A default five-claim prefix can starve later topics. | That a six-topic plan fits a fixed five-claim extraction prefix. |
| agents/quality.py:compute_report_quality | A linked claim can credit a topic without substantively answering it. | That 100% ID coverage means complete research. |
| e2e_evaluation/cases.py:scripted_research_agents; runner.py:run_repetition | Controlled end-to-end tests replace graph agents. They test graph behavior, not cooperation of the six real agents. | That the old three-case green matrix proves agent quality. |
| e2e_evaluation/evaluators.py:judge_whole_report | “Readability” is a length band; “actionability” is a keyword; several terms repeat integrity gates. Judge input clips the report at 16,000 characters. | That a structural 0.8 establishes correctness, prioritization, useful analysis, or good writing. |
| output/evaluations/task21-fact-checker-readiness-30d9921/ | Average 0.6205 / FAILED despite green hard gates. Judge notes abort, unused upstream evidence, and vacuous metrics. | That conservative non-answering is a useful completed fact check. |
| output/evaluations/production-readiness-v2-planner-r1/ | 0.877 / REVIEW REQUIRED; weak recency framing and omissions remain. | That a high average closes important agent-specific defects. |
| output/evaluations/live-synthesizer-2dfa099/ | 0.81 / REVIEW REQUIRED; invented “truncated finding” limitation and omitted available content. | That accurate citation URLs make every narrative/limitation sentence true. |
| output/evaluations/live-critic-d2/; docs/superpowers/2026-09-12-critic-calibration-recommendation-request.md | Calibration, schema behavior, and judge disagreement require separate checks; d2 r3 FAILED at 0.639. | That forcing the critic upward or ignoring a judge/provider error improves reports. |
| config.yaml; utils/config.py:LLMConfig.resolve_for; runtime/assembly.py:build_agent | Evaluation and production can resolve different reasoning overrides. | That max-effort evaluation validates high-effort production merely because construction is shared. |

Paths beginning agents/, tools/, runtime/, utils/, graph/, evaluation/, or e2e_evaluation/ in this evidence table are under src/deep_research/. Output histories are historical snapshots, not current agent scores.

Do not repeat the ruled-out User-Agent experiment, assume an entire publisher is blocked, or add paid acquisition vendors before testing the repaired path. This planning review does not independently establish the external truth of grid-storage facts.

### Cross-run findings and expected fix effects

A local sweep during this amendment inventoried all 13 top-level non-evaluation reader artifacts (10 sessions), inspected cross-run gap text, and reconciled the four latest canary logs. This is broader than the latest report alone, but is not an exhaustive reread of every raw remote LangSmith trace.

| Run / local artifact | Measured result | Expected repair and falsification test |
| --- | --- | --- |
| resource: report-d8894023f1de48b7b3b3182634a15cb0-3.md | Critic 4/10, reported 6/6 coverage, yet the reader explicitly says technical/system-integration constraints are absent. | Tasks 2/5/10: coverage must fail for the unanswered required target even if a claim carries its topic ID. |
| publisher retention: report-97921fe49c89402ab13c5562dbd97b3a-3.md | Critic 4/10, 5/6 coverage; current project cost/revenue/financing remain without checked claims. | Tasks 3/5/6: target-fair evidence/claim scheduling must preserve economic evidence and record exact unmet obligations. More retained domains alone is not success. |
| corroboration criterion: report-480fd56130e349208b56307f98f89fab-3.md | Critic 4/10, reported 6/6 coverage; no checked safety/performance risk claim and no supported supply-chain-to-deployment mechanism. | Tasks 5/7/10: critical-target completion and mechanism support must expose these gaps; another prompt sentence is not the acceptance test. |
| evidence pooling: report-417fa9338e10450784b459f89af98b1c-3-evidence.md | CAISO market-revenue and EIA efficiency sources are scored but uncited; corresponding findings remain unchecked while the reader declares those areas unaddressed. | Tasks 3/5/6/7: replay available-but-unused evidence through extraction, scheduling, adjudication, and composition. A positive fixture must carry the relevant claim to the reader, with appropriate freshness qualification. |
| same latest ledger: mineral-processing claim | A recorded supporting passage uses different mineral sets and thresholds from the claim; a news passage cites the IEA report itself. | Tasks 4/6: test full-atom entailment and shared-origin dependence separately. This ledger does not establish two complete independent supports, even though the current verdict says verified. |
| same latest ledger: queue/evacuation claims | Compound totals/wait-time or evacuation/duration assertions rely on multiple partial passages; some support rows are metadata rather than the complete proposition. | Tasks 5/6: split atomic assertions without losing qualifiers; do not declare the entire compound statement corroborated from one supported component. |
| agent-fixes: report-b600945da4d54379943f6f81b31d84da-3.md and 2026-09-15-agent-fixes-run.md | Reads improved at the same ten-call loop bound, critic stayed 3/10; the scored PNNL zoning source was not converted to a checked claim. | Tasks 3/5/6: discriminate acquisition from loss/selection, and validate useful downstream evidence yield rather than merely read count. |
| legacy report-854cddd33db749b3a193fe1f8a125e19, iterations 0–3 | Reader length grows approximately 3,261 → 6,291 → 10,377 → 15,567 words. | Tasks 7/9/11: refinement replaces/improves canonical content instead of appending inventories; reader-length/backmatter gates must apply to the final CLI output. |

Report paths in this table are under output/. The agent-fixes result document is under docs/superpowers/validation/. Word counts use whitespace splitting and are structural diagnostics, not semantic scores. “Available but unused” identifies a boundary to test; it does not imply every old finding is current, relevant, or already independently verified.

The latest trace counts above are freshly reconciled; older trace claims remain historical. Missing original provider/read payloads must be recorded as not_diagnosable in Task 0. Each fix has both a positive control (use valid evidence to answer) and a negative control (reject or qualify inadequate evidence); neither universal abstention nor blanket verification can pass.

### Last-run trace additions that change implementation priorities

| Trace finding | Specific implementation requirement | Owner tasks |
| --- | --- | --- |
| TR-01: generated memory masquerades as read evidence; old duplicate queue facts already present | Close the ordinary-memory admission path before trusting acquisition/independence metrics; preserve validated document-cache reuse separately | 1, 2, 3, 6, 12, 13 |
| TR-02: 200-character next-decision feedback and separate 4,000-character extraction prefixes | Give actual decision requests a target-aware candidate/read/passage manifest; do not merely enlarge logs | 3, 6 |
| TR-03: LBNL 2025 URL read 21 times overall; USGS/CAISO successful reads sometimes produce no findings | Reuse content/versioned reads; select late substantive passages and measure yield; keep unknown historical passage loss explicitly unresolved | 3, 5, 9 |
| TR-04: stale date anchors, invented numerical tolerances, compound success criteria | Runtime as-of, atomic obligations, source-appropriate support policy; no invented agreement tolerance | 2 |
| TR-05/06: source warnings do not constrain settlement; exact five-claim batches and duplicate atoms | Enforce typed fitness/dependence; fair claim continuation; exact support union and stable semantic IDs | 4, 5, 6 |
| TR-07: interim_satisfaction is inferred from one raw finding and Critic silence | Outstanding required targets drive repairs independently of Critic gap completeness | 9, 10, 11 |
| TR-08: unchecked numbers can appear inside caveats; metadata asserts stronger linkage than actual prose | Review every reader statement, including exclusions, scope, and generated methodology, not only Findings bullets | 7, 10, 11 |
| TR-09: two recovered schema failures with normal stop reason; Critic still search-only | Preserve schema repair context; record categorical field errors without inventing historical causes; make Critic tool-free | 6, 8, 12 |

The source evaluator successfully reused assessments and the synthesizer correctly distinguished future cost projections from present costs. Preserve those strengths. The Critic's low score is consistent with missing answers; do not treat raising its score as the root repair.


### Agent-specific output requirements

| Agent | Main deficiency | Targeted improvement | Required proof |
| --- | --- | --- | --- |
| Planner | Prose criteria; missing temporal/scope constraints; infeasible obligations | Question-shaped answer contract, critical targets, source needs, balanced queries, capacity feasibility | Scope/recency/adversarial-plan fixtures; unseen-question review; downstream target completion |
| Researcher | Discovery dominates usable evidence; failed leads recur; late passages/support B can disappear | Candidate queue, read progression, exact excerpts, official-document fallback, target-aware retention/dispositions | Read → selected-passage → finding audit; late-document and failed-read tests; evidence yield per target |
| Source Evaluator | Serving domains and high authority masquerade as independent evidence | Separate issuer/work/host, passage-level dependence, relevance, freshness, conflicts of interest | Mirror, shared-dataset, unsupported-metadata, mixed-role article, weak-source controls |
| Fact Checker | Repeat checks, five-claim cap, URL-only pooling, incomplete semantic checking | Stable atomic clusters, fair scheduling, exact union packet, evidence-ID adjudication, qualified truth labels | A+B without C; B-loss test; date/unit/scope contradictions; non-vacuous positive cases |
| Synthesizer | Fact dump; weak mechanisms/prioritization; duplicates; invented limitations | Answer-first structure, supported reasoning, statement-level mapping, meaningful uncertainty, correct references | Factuality/completeness/usefulness review; unsupported-mechanism and invented-limitation negatives |
| Critic | Search-only spot checks; anchoring; vague gaps | Tool-free evidence review, defect-specific gaps, calibrated score bands, attribution/corroboration distinction | False-pass/false-fail calibration; full-tail packet test; actionable refinement |
| Graph / CLI | Generic repeats, hidden loss, confusing metrics/status | Targeted repairs, meaningful progress, consistent terminal report/ledger/review, readable summary | Real-agent CLI matrix; artifact/state parity; unchanged-pass and presentation-only repair tests |

### Approach selected

Prompt-only tuning already underperformed and cannot restore lost evidence. Replacing all agents would discard working contracts without evidence of need. Preserve the graph/providers, repair evidence and editorial seams, then tune only failures demonstrated by agent and report tests.

## 2. Binding quality semantics

These definitions replace conflicting wording in the previous plan revision. Apply them consistently in schemas, prompts, metrics, tests, and CLI wording.

### 2.1 Read provenance, attribution, corroboration, and truth

- Search results, snippets, memory recalls, URLs, and source scores are not read-bearing evidence.
- query_memory is always discovery/procedural guidance, even when its entry has a URL, high confidence, a previous "verified" label, or a claimed read ID. The current steps.py rule that treats recalled content as a read must change, along with tests that codify it. No synthetic current retrieved_at or publisher vote may be created from remembered prose.
- Evidence is an exact excerpt from a successful same-run read, or a provenance-validated cache entry whose version is checked for time-sensitive claims. It has a stable read ID, content version, locator, and target association.
- Cache validation resolves an actual stored ReadRecord and original extracted content independently of the model/memory match, validates the content hash and temporal/version eligibility, then admits that artifact to the run registry. A memory record's self-declared metadata is not validation. A bare legacy memory becomes a lead needing retrieval; no destructive memory migration is required.
- **Verified** retains the strict independent-corroboration badge: two selected passages each support the complete atomic claim; their known canonical publishers and works differ; their claim-specific evidence origins are independent; no unresolved material contradiction defeats settlement. Qualifying upstream A+B needs no new retrieval. URL counts never auto-verify.
- **Source-supported** is not verified. A primary report can support “Report X estimates Y for population P in year T” without another publisher reproducing X's measurement. Preserve the legacy insufficient_evidence verdict when corroboration is absent, add evidence_status=source_supported, and explain “primary-source attribution; independent corroboration not established,” not “probably false.”
- This deliberately corrects the previous blanket requirement that every reader fact have two independent works. It does not allow an unqualified settled assertion without the strict pair. Adding “according to” cannot earn verified: check the exact narrowed proposition and its attribution.
- **Derived** analysis cites supported premises, shows reproducible arithmetic where applicable, and labels inference. A plausible mechanism is not an observed causal fact. **Contested** statements show both sides and scope/method differences. Unsupported assertions stay out of reader conclusions.
- A checked company statement establishes what the company said, not the independent truth of its assertion. Low-authority/unscored material cannot carry a conclusion merely by attribution.
- Assign target support policy before verdicts: general comparative/causal conclusions require independent evidence; official measurements/definitions may require precise primary attribution; calculations require supported premises and checked derivation. No post-hoc downgrading to pass coverage.
- No blanket-abstention reward. Positive cases must deliver answerable claims and critical targets. Honest insufficiency is correct for negative cases, not a substitute for useful research in answerable cases.

### 2.2 Canonical identity and independence

Maintain serving URL/host, publisher/issuer, intellectual work, and claim-specific evidence origin separately.

1. Collect aliases: normalized DOI; issuer-namespaced report number; normalized nonempty complete-text hash; conservative title + year + issuer; evidenced document/version relationships.
2. Resolve across records, not “first key per URL.” A DOI-bearing original and DOI-missing identical mirror must join. Equal hashes can join; unequal hashes cannot prove independent works. Empty/error/truncated boilerplate hashes never establish identity.
3. Conflicting strong IDs or different editions/data periods stay distinct or unresolved; generic titles never force a merge. Preserve old IDs through aliases when stronger metadata arrives.
4. A mirror is a transport relation, not unusable evidence. An OSTI copy of an LBNL report inherits issuer/work, can be the first primary support, and contributes no additional corroboration. CDN/archive hosts are not new publishers.
5. Stories repeating one report, press release, dataset, or analysis do not independently corroborate that result. Independence is claim-specific: an article can contain derivative statistics and original interviews.
6. Independent reanalysis of shared data can support a distinct interpretation if its separate method is evidenced; it is not another independent measurement of the same statistic. Preserve derives_from_work_ids and dependence rationale.
7. Unknown identity cannot establish independence. It can support conservative attribution where issuer/authority and exact content suffice; never invent metadata to complete a pair.
8. Validate metadata against document text/fields/links and evidenced organization aliases. A quoted organization's name does not make it the article's publisher.

### 2.3 Substantive coverage and editorial relevance

Freeze original question, scope/as-of, answer form, and initial critical-target inventory. A target is answered only when its reader statement satisfies required dimensions and support policy. Mentioning a topic ID, incident date, or report date cannot satisfy a mechanism/economics target. A subsequently discovered original-question omission may add a reviewed target through Planner refinement, but cannot remove or weaken an existing obligation. Record the initial and expanded inventories; expanded coverage uses their union, never a smaller denominator.

Report target completion and topic coverage separately. A topic is covered only when all required targets are answered. Broad acceptance needs >=80% of topics, every critical target answered, every remaining target accounted for, and no major original-question omission in semantic review. Never shrink the denominator, delete difficult topics, or mark them optional after retrieval.

Publication date, data period, forecast horizon, effective policy date, retrieval date, and generation date differ. “Current” means latest relevant evidence checked as of the declared date, not recently fetched. Older latest-available data may be used with its lag disclosed; a 2035 projection cannot become today's cost.

Metadata is context unless the question asks for metadata. Do not exempt a load-bearing assertion by labeling it context. A comparison, factual explanation, or historical question must not be forced into a deployment-ranking template.

### 2.4 Mechanical audits without false causal certainty

Persist boundary IDs: read registry → selected prompt passages → findings/dispositions → atomic clusters → adjudication packet → selected support/contradiction → reader statement. Explicit omissions need reasons; counts alone are insufficient.

| Audit class | Reproducible predicate |
| --- | --- |
| evidence_admission | A proposed read/support URL has no matching validated same-run or cache ReadRecord and exact excerpt. Includes generated-memory-only, forged/absent cache provenance, and empty/boilerplate payload. Reject before computing publisher counts. |
| acquisition | A required obligation has no acquired readable candidate after the declared attempts; record denied/not-found/malformed/no-candidate/bound outcomes. Means “not acquired in this run,” not “does not exist.” |
| evidence_handoff | An accepted ID disappears at a required later boundary without disposition, or a selected successful read has neither extraction result nor disposition. Record first missing set difference/boundary. |
| publisher_identity | Candidate support reached adjudication but identity/dependence collapses it, or ambiguity prevents the required pair. Subreasons: same_work, same_publisher, shared_origin, identity_unknown. |
| genuinely_missing_corroboration | No unexplained boundary loss; candidates were assessed for identity and semantic support; fewer than required independent complete supports remain. Scope/date/unit mismatch is support failure, not transport failure. |
| not_diagnosable | Historical payload/IDs are absent, model/judge did not return, or packet was not fully assessed. Do not force a causal class. |

Flags may coexist; a display reason is not causal proof. An explicit no_relevant_passage disposition is the extractor's judgment, not established truth: known-support replay tests must catch wrongly discarded evidence.

For F1, first audit evidence admission: a "read" may actually be generated memory. Then inspect raw successful reads and extraction input/output for the exact atom (value/unit/period/population). If A+B contain it but B never reaches the claim, identify the first loss. If only A is present, acquisition did not supply B. If payloads were not saved, mark the historical case unresolvable and use replay; do not infer from URL lengths or source/publisher counts.

### 2.5 Retention and effort

Four publishers is the existing per-packet diversity cap, not four URLs or a global stored-evidence cap. sources_retained stays a deprecated URL-count alias; add publishers_retained, source_urls_retained, findings_retained, works_retained.

Keep valid units in the registry. Packet selection reserves critical/uncovered targets, required independent support, and contradictions; then within-publisher target diversity; then relevance/authority/freshness, with confidence only a tie-breaker. More than four required publishers or a full packet creates a continuation, not deletion. Deferred IDs must resume.

The ten-tool-call loop cannot guarantee four multi-source targets fit. Schedule feasible batches, reserve reads before discovery spends the allowance, and persist unfinished work. Do not silently slice claims, mark unreviewed findings consumed, or spend everything on the first topic.

### 2.6 Decision context, memory modes, and diagnosis

Public progress summaries and model decision packets have different purposes. Keep the former compact. The latter must contain the active target, exact queued candidate URLs/IDs, reasons to read them, attempted/denied/read status, selected evidence IDs/locators, pending work, remaining tool/model-turn capacity, and the next required support type. No prefix-only serialization of a whole search/PDF response is an acceptable decision packet.

Record boundary manifests in the evidence/quality artifact: operation/job ID, target/claim IDs, input/selected/output/accepted/deferred IDs, dispositions, packet fingerprint, configuration/schema version, and execution status. Count an entire native batch, not only the last observation stored on its iteration span. Normal telemetry does not need raw prompts, complete pages, credentials, or chain-of-thought.

Release repetitions use independently empty, isolated long-term and procedural research memory plus explicit source-cache state; a new session ID alone is insufficient. Test realistic warm memory separately: repeated incorrect claims must not outweigh a freshly supported correction, and valid cached original evidence must remain reusable. Neither memory deletion nor disabling useful evidence reuse is the product fix.

## 3. Shared contracts and ownership

All additions load historical state conservatively. New producers use strict builders; old snapshots cannot receive new strict acceptance without missing checks. Add quality_contract_version and old-ID aliases. Never synthesize historical read provenance.

| Owner | Responsibility |
| --- | --- |
| src/deep_research/utils/types.py | Additive shared read/evidence/target/claim/support contracts; later typed gaps, statements, progress, review. |
| src/deep_research/agents/evidence.py (new) | Pure read validation, canonical identity/aliases, exact excerpt matching, eligible_independent_pair, stable IDs. |
| src/deep_research/tools/passage_selection.py (new) | Complete-document passage selection, locators, explicit omissions. |
| src/deep_research/agents/acquisition.py (new) | Shared candidate queue/policy; Researcher and Fact Checker supply target needs. |
| src/deep_research/agents/claim_clusters.py (new) | Atom validation, semantic grouping, stable aliases, fair scheduling. |
| src/deep_research/agents/report_review.py (new) | Shared terminal semantic review packet, bounded complete review, local score/status checks. Production never imports evaluation internals. |
| src/deep_research/runtime/assembly.py | Real-agent construction, effective production settings, injected external clients and terminal reviewer. |
| src/deep_research/graph/state.py and utils/types.py | Persist registries, dispositions, aliases, progress, reviewed fingerprints; explicit reducers. |
| agents/report.py, runtime/outcome.py, CLI/API | Render/export one reviewed composition and consistent evidence/quality record. |

New-field contracts below extend rather than replace existing models:

~~~python
class WorkIdentity(ContractModel):
    key: str | None
    aliases: list[str]
    basis: str
    issuer_id: str | None
    derives_from_work_ids: list[str]
    identity_status: Literal["known", "unknown", "conflicting"]

class ReadRecord(ContractModel):
    read_id: str
    requested_url: str
    resolved_url: str
    title: str
    reader: Literal["web_scraper", "document_reader"]
    retrieved_at: str
    content_sha256: str
    extraction_complete: bool
    passages: dict[str, str]  # locator -> canonical extracted text
    target_ids: list[str]

class EvidenceUnit(ContractModel):
    evidence_id: str
    read_id: str
    source_url: str
    source_title: str
    locator: str
    excerpt: str
    target_ids: list[str]
    origin: Literal["researcher", "fact_checker"]

class EvidenceTarget(ContractModel):
    target_id: str
    coverage_id: str
    question: str
    required_dimensions: list[str]
    required: bool
    critical: bool
    support_policy: Literal["independent_pair", "primary_attribution", "derivation"]

class AtomicProposition(ContractModel):
    text: str
    subject: str
    predicate: str
    value: str | None = None
    unit: str | None = None
    population: str | None = None
    geography: str | None = None
    observation_period: str | None = None
    forecast_horizon: str | None = None
    negated: bool = False
    attribution: str | None = None

class SupportAssessment(ContractModel):
    evidence_id: str
    relation: Literal["supports", "contradicts", "partial", "context", "unrelated"]
    complete_support: bool
    origin_group_id: str | None
    dependence: Literal["primary", "independent_analysis", "derivative", "unknown"]
    rationale: str
~~~

Use Pydantic Field bounds and ID/reference validators in new producer builders. Required dimensions use fact/mechanism/scope/time/magnitude/comparison/tradeoff; choose only question-relevant dimensions.

Additional fields:

- Finding: evidence_ids and evidence_target_ids; retain old URL/title/fingerprint fields. Every new finding requires valid evidence.
- EvidenceDisposition: item_id, stage, reason, target_ids, retained_equivalent_id (nullable). Stages: read-selection, extraction, retention, clustering, adjudication-packet, composition. Reasons: irrelevant, out_of_scope, stale_for_target, duplicate_content, malformed, deferred_capacity, unsupported_excerpt. Duplicates point to retained evidence; deferral never means consumed.
- Claim: cluster_id, member_claim_ids, proposition (nullable only for legacy), evidence_ids, target_ids, evidence_status (corroborated/source_supported/derived/contested/unsupported/unassessed), support_assessments, assessment_fingerprint. Preserve legacy verdict enum.
- ScoredSource: publisher/work/serving-host, transport_relation (original/mirror/syndication/unknown), evidenced metadata, assessment revision. Passage-specific dependence belongs in SupportAssessment, not blanket document exclusion.
- ResearchState/ResearchStateUpdate: read_records keyed by read_id, evidence_units keyed by evidence_id, evidence_dispositions, cluster_aliases, quality_contract_version. ID conflicts are errors, not last-write-wins. Claims/sources remain canonical snapshots; events/errors append. Tasks 9–10 add progress/review.
- ReadRecord adds acquisition_kind (network/cache, default network), origin_session_id, and version_validated_at (nullable for ordinary same-run reads). Cache import fills these locally only after validation. Ordinary query_memory never creates ReadRecord.
- BoundaryAudit (types.py): audit_id, job_id, agent_name, operation, target_ids, claim_cluster_ids, input_ids, selected_ids, returned_ids, accepted_ids, deferred_ids, disposition_ids, packet_fingerprint, schema_version, configuration_fingerprint, status (completed/deferred/provider_failed/schema_failed). All ID lists default empty; required scalar IDs/fingerprints are nonempty. Persist boundary_audits by audit_id with conflict-detecting merges; export them in quality JSON, not public progress messages.

Evidence IDs exclude mutable identity/quality scores. Full extracted text stays in the bounded session read registry/cache for locators/replay, not normal logs/prompts. Cache eviction requires a disposition or preserved replay artifact; never silently lose unprocessed text. Audit exports contain selected excerpts, hashes, all accepted/deferred IDs, and dispositions.

---


### Task 0: Freeze and reconcile the evidence baseline

**Files:** Create docs/superpowers/validation/2026-09-16-output-quality-baseline.md. Update docs/superpowers/validation/2026-09-15-release-status.md. Read, do not rewrite, earlier result/predeclaration documents and output artifacts.

**Interface:** Produce an immutable artifact/commit inventory and a defect-to-task matrix. Documentation task; no invented RED test or live rerun.

- [ ] Record branch/application SHA/docs SHA, all latest canary sessions, literal questions, CLI exits, report/ledger/log paths, byte sizes, and SHA-256 hashes. Use Get-FileHash and Get-Item; do not infer recency from OneDrive mtimes.
- [ ] Reconcile the latest 5/10, 4/6, 815,664-token result and prior resource/publisher/corroboration runs. Separate local measurements, trace-derived historical counts, missing fields, and interpretations.
- [ ] Inventory per-agent results.json by case version, target configuration, status, gate failures, judge status, and substantive reviewer findings. REVIEW REQUIRED is not release approval; infrastructure failures are not agent-quality successes.
- [ ] For three repeated live atoms (queue totals, report cutoff, PJM cycle), perform the cheaper F1 payload comparison where raw data exists. Record exact first missing boundary or not_diagnosable. Do not invent B from two similar URLs.
- [ ] Incorporate the last-run trace review TR-01–TR-09, including memory admission, 21 reads of the same LBNL URL, cold/warm state, and the absence of full request/read payloads. Store reference IDs and distinguish verified code reproduction from historical causal inference. Do not overwrite the prior baseline with newer source content.
- [ ] Record deficiencies named in Section 1 and current source/prose weaknesses without claiming the external facts have been independently rechecked.
- [ ] Update the release-status “current run” pointer while retaining historical entries; NOT READY remains.
- [ ] Verify every reported number against its cited artifact, check hashes and absence of secrets, inspect the documentation diff, then commit, independently review, and push.

~~~powershell
git branch --show-current
git rev-parse HEAD
Get-FileHash -Algorithm SHA256 -LiteralPath output/report-417fa9338e10450784b459f89af98b1c-3.md
Get-FileHash -Algorithm SHA256 -LiteralPath output/report-417fa9338e10450784b459f89af98b1c-3-evidence.md
git diff --check
~~~

**Commit:** docs(validation): reconcile agent and CLI quality baseline

### Task 1: Persist an exact, versioned evidence contract and stable identities

**Files:**

- Create src/deep_research/agents/evidence.py and tests/test_agents/test_evidence.py.
- Modify src/deep_research/utils/types.py, src/deep_research/agents/sources.py, src/deep_research/agents/steps.py, src/deep_research/tools/document_reader.py, src/deep_research/tools/web_scraper.py, src/deep_research/graph/state.py.
- Tests: tests/test_types.py, tests/test_state.py, tests/test_graph/test_state.py, tests/test_agents/test_steps.py, tests/test_agents/test_sources.py, tests/test_agents/test_researcher.py, tests/test_agents/test_fact_checker.py, tests/test_tools/test_document_reader.py, tests/test_tools/test_web_scraper.py.

**Interfaces:** Section 3 contracts. evidence.py produces normalized_content_sha256(text: str) -> str; excerpt_matches(text: str, excerpt: str) -> bool; resolve_work_identities(metadata: Sequence[Mapping[str, object]]) -> dict[str, WorkIdentity] (input rows keyed by source_id); canonical_publisher_id(metadata: Mapping[str, object]) -> str | None. eligible_independent_pair is added in Task 6. Source metadata fields are DOI, report_number, issuer, title, year, complete_content_sha256, edition, identity_links; every field has read-derived provenance.

Cache admission is owned by evidence.py, not memory_tools.py: validate_cached_read(record: ReadRecord, canonical_text: str, *, expected_content_sha256: str, version_eligible: bool, validated_at: str) -> ReadRecord | None. Its caller resolves the stored original artifact and determines version eligibility locally; these arguments never come directly from model/memory metadata. Require nonempty complete canonical text, matching stored/expected/recomputed hashes, valid locators/excerpts, and version eligibility. Return a locally stamped cache ReadRecord or None plus a caller-recorded disposition; query_memory itself still yields no read URLs. Task 3 owns cache lookup/revalidation scheduling.

- [ ] **RED:** Test DOI/report normalization, original-with-DOI plus identical mirror-without-DOI, unknown identity, conflicting identifiers, different editions, generic-title collision, empty/error/partial extraction hashes, and issuer versus CDN host.
- [ ] **RED:** Test round-trip persisted registries, stable IDs after stronger identity arrives, conflicting content for one ID, and legacy state loading without fabricated provenance.
- [ ] **RED:** A query_memory result containing text, source_url (direct or nested metadata), high confidence, and a previous verified label contributes zero read_evidence_urls. Test this through both Researcher extraction and Fact Checker independent-source eligibility; current tests expecting a memory URL to count as a read must be deliberately corrected, not preserved as the target behavior.
- [ ] **RED:** Reject a forged/missing cache read ID, incorrect content hash, stale time-sensitive cached content, and a previous generated summary substituted for original source text. Positive control: a validated immutable original read is admitted without another body download and retains its publisher/work/original observation period.
- [ ] Remove ordinary-memory admission from steps.py. Keep query_memory usable for leads/procedural guidance; valid cached source text enters only via the local read-registry validation path. Do not erase historical memory or automatically recertify legacy claims.
- [ ] Persist Section 2.6 boundary manifests at read admission/selection and extend them as later tasks add handoffs. Historical missing IDs remain unknown. A missing manifest must fail a new-contract replay assertion, never silently look like zero loss.
- [ ] **RED:** Require requested/resolved URL, extraction completeness, content hash, and locator text on successful reads. A redirect must not silently leave only the requested URL. A 200 challenge/empty document must not become evidence.
- [ ] Implement deterministic canonical text normalization and exact excerpt membership. Normalize whitespace/Unicode consistently while preserving numbers, minus signs, units, and negation. Preserve the raw locator text as well as normalized match text; no fuzzy excerpt acceptance.
- [ ] Implement alias-based work grouping with strong-ID conflict checks. A normalized hash is only an equality edge when extraction is complete and nonempty. Different hashes are never independence proof. Preserve ambiguity explicitly.
- [ ] Implement registry reducers/strict producer builders. Keep module imports acyclic; shared models do not import provider/agent implementations. Persist evidence IDs independently of mutable assessments.
- [ ] **GREEN:** Run focused tests, then all state/read-tool regressions. Review backward compatibility and no network calls.
- [ ] Commit, fresh review, resolve findings, rerun, push.

Minimum RED and implementation kernel:

~~~python
def test_doi_missing_mirror_joins_original():
    rows = [
        {"source_id": "original", "doi": "https://doi.org/10.1234/ABC",
         "issuer": "Example Lab", "title": "Queue Study", "year": 2025,
         "complete_content_sha256": "a" * 64},
        {"source_id": "mirror", "issuer": "Example Lab",
         "title": "Queue Study", "year": 2025,
         "complete_content_sha256": "a" * 64},
    ]
    resolved = resolve_work_identities(rows)
    assert resolved["original"].key == resolved["mirror"].key
    assert resolved["original"].issuer_id == resolved["mirror"].issuer_id

def excerpt_matches(text: str, excerpt: str) -> bool:
    def normalized(value: str) -> str:
        return " ".join(unicodedata.normalize("NFC", value).split())
    candidate = normalized(excerpt)
    return bool(candidate) and candidate in normalized(text)
~~~

Run: python -m pytest tests/test_agents/test_evidence.py tests/test_types.py tests/test_state.py tests/test_graph/test_state.py tests/test_agents/test_sources.py tests/test_tools/test_document_reader.py tests/test_tools/test_web_scraper.py -q

Also run the shared read-classifier and affected agent regressions:

~~~powershell
python -m pytest tests/test_agents/test_steps.py tests/test_agents/test_researcher.py tests/test_agents/test_fact_checker.py -q
~~~

Memory-admission minimum RED (use the production classes, not a fake classifier):

~~~python
def test_recalled_fact_is_not_a_source_read():
    from deep_research.agents.steps import ReActStep, read_evidence_urls
    from deep_research.tools.base import ToolResult

    step = ReActStep(
        iteration=1, thought="Test fixture.", action="use_tool",
        tool_name="query_memory",
        tool_result=ToolResult(
            tool_name="query_memory", success=True, latency_ms=0,
            data={"matches": [{
                "content": "A previous generated answer says capacity is 10 GW.",
                "source_url": "https://remembered.example/report",
                "confidence": 0.99,
            }]}),
    )
    assert read_evidence_urls(step) == ()
~~~

**Commit:** feat(evidence): persist read provenance and canonical identities

### Task 2: Make Planner targets answer-shaped, scoped, feasible, and production-configured

**Files:**

- Modify src/deep_research/agents/planner.py, src/deep_research/agents/prompts.py, src/deep_research/agents/base.py, src/deep_research/utils/types.py, src/deep_research/utils/config.py, src/deep_research/runtime/assembly.py, config.yaml.
- Modify src/deep_research/evaluation/config.py and src/deep_research/evaluation/dependencies.py for explicit production-parity resolution.
- Modify src/deep_research/runtime/recall.py and src/deep_research/main.py for the initial planning-only recall path; retain full legacy memory storage and bounded Researcher lead lookup.
- Tests: tests/test_agents/test_planner.py, tests/test_agents/test_base.py, tests/test_types.py, tests/test_config.py, tests/test_runtime/test_assembly.py, tests/test_evaluation/test_config.py, tests/test_evaluation/test_dependencies_controlled.py.
- Tests for startup recall: tests/test_runtime/test_recall.py, tests/test_runtime/test_memory_bridge.py, tests/test_cli/test_entrypoint.py.

**Interfaces:** Add AnswerContract to types.py: question, scope_statement, geographic_scope, as_of_date, evidence_period_requirement, assumptions, answer_kind (constraints/comparison/explanation/factual/historical), requested_word_limit. Persist ResearchState.answer_contract and ResearchStateUpdate.answer_contract, plus the immutable initial_target_ids. SubTopic.evidence_targets carries 1–4 new targets; legacy empty lists load but require replanning. The reserved target reference question denotes an original-question omission and is not a counted evidence target. AgentRuntimeConfig.tool_budget_for(agent_name: str) -> int uses validated tool_budget_overrides. Production-parity evaluation resolves from LLMConfig.resolve_for instead of silently using evaluation-only overrides.

- [ ] **RED:** Unqualified broad, jurisdiction-specific, latest-policy, comparison, historical, and narrow-factual plans must preserve the question and produce matching answer forms. No invented dates/URLs/settled premises.
- [ ] **RED:** Include a seemingly diverse plan that omits a critical question dimension, a compound evidence target, biased queries assuming an answer, scope expansion, and an infeasible target batch. Structural validation catches IDs/fields; a structured plan-review call checks semantic atomicity and original-question coverage. Do not pretend regex can prove these properties.
- [ ] Add the answer contract and locally stamped topic/target IDs. Request unknowns as questions, not asserted facts. Explicitly mark assumptions for unspecified geography; a regional sample cannot support a global conclusion.
- [ ] Stamp as_of_date from the injected run clock, not from model knowledge or memory. With a 2026 clock, regression fixtures proposing "2024 is current" must become a latest-available evidence obligation, not a hard-coded old-year limit. Preserve requested historical dates when the user explicitly supplies them.
- [ ] Reject invented cross-publisher matching tolerances (the last plan used 10%, 5 percentage points, 15%, and 3 percentage points) and split criteria combining multiple measures/rule dates/jurisdictions. Tolerances require an explicit question or documented measurement/method basis; numerical proximity alone never establishes the same atomic fact. Official rule dates use primary-attribution policy, not a requirement for another organization's independent model of that date.
- [ ] Add one bounded tool-free plan-review/repair call for semantic defects, with named missing dimensions and an unchanged original question. If it cannot produce a sound plan, report the exact planning failure; no quiet denominator shrink.
- [ ] Support extend_plan refinement for a later original-question omission: return only additional targets/topics with new locally assigned IDs, keep existing IDs/scope/as-of/critical obligations, and preserve both inventories. Existing targets cannot be made optional. A capacity conflict is explicit, not permission to delete a difficult topic. Test this with an initially missing siting/permitting dimension.
- [ ] Planner uses at most one procedural-memory query; cached facts never become evidence. Set tool overrides planner=1, researcher=10, fact_checker=10, source_evaluator=0, synthesizer=0; Task 8 sets critic=0.
- [ ] Include automatic initial memory recall in this limit: avoid a hidden bridge recall plus one tool lookup. Provide only deduplicated procedural guidance to planning; recalled findings may supply optional acquisition leads, never settled premises or target priority. Test duplicate/high-confidence/obsolete memory and inspect the actual planning request.
- [ ] Add purpose: Literal["research","planning"] = "research" to recall_memory_context for backward-compatible callers. Startup in main.py passes purpose="planning": use available procedural guidance without querying long-term finding/reputation records or populating similar_findings. Keep any optional Planner tool lookup within the one-call limit and constrained to procedural material. The Researcher may discover prior source leads separately under its own recorded budget; it must re-admit their evidence.
- [ ] Wire resolved budgets through both BaseAgent and direct run_react_loop call sites. Implement per-call configuration fingerprints including model, thinking/effort, output/context limits, schema and prompt version.
- [ ] Use the already-supported llm.model_overrides to align quality-sensitive production calls with the measured configuration: planner/fact_checker/synthesizer/critic max; researcher/source_evaluator high as the initial candidate. This is a testable candidate, not proof max is always better. Validate provider support locally; incompatible settings fail preflight, not silently fall back.
- [ ] Add evaluation production-parity resolution (the CLI flag is exposed in Task 12) and label any experiment-only override as non-release evidence. Review tests prove the target settings match CLI settings.
- [ ] **GREEN:** Run focused and assembly/config/evaluation regressions. Commit, review, fix, push.

~~~python
def test_agent_budgets_resolve_without_changing_global_default():
    config = AgentRuntimeConfig(
        tool_budget=10, tool_budget_overrides={"planner": 1, "critic": 0}
    )
    assert config.tool_budget_for("planner") == 1
    assert config.tool_budget_for("researcher") == 10
    assert config.tool_budget_for("critic") == 0
~~~

~~~yaml
llm:
  model_overrides:
    planner: {reasoning_effort: max}
    fact_checker: {reasoning_effort: max}
    synthesizer: {reasoning_effort: max}
    critic: {reasoning_effort: max}
    researcher: {reasoning_effort: high}
    source_evaluator: {reasoning_effort: high}
~~~

This snippet amends the existing llm mapping, not replaces other fields. Resolve configuration for report_judge separately in Task 10.

Run: python -m pytest tests/test_agents/test_planner.py tests/test_agents/test_base.py tests/test_types.py tests/test_config.py tests/test_runtime/test_assembly.py tests/test_evaluation/test_config.py tests/test_evaluation/test_dependencies_controlled.py -q

Also run python -m pytest tests/test_runtime/test_recall.py tests/test_runtime/test_memory_bridge.py tests/test_cli/test_entrypoint.py -q for startup memory isolation and compatibility.

**Commit:** feat(planner): define scoped answer obligations and configuration parity

### Task 3: Make acquisition produce useful read evidence instead of search churn

**Files:**

- Create src/deep_research/agents/acquisition.py, src/deep_research/tools/passage_selection.py, tests/test_agents/test_acquisition.py, tests/test_tools/test_passage_selection.py.
- Modify src/deep_research/agents/react.py, src/deep_research/agents/steps.py, src/deep_research/agents/base.py, src/deep_research/agents/researcher.py, src/deep_research/agents/prompts.py, src/deep_research/agents/events.py, src/deep_research/tools/document_reader.py, src/deep_research/tools/web_scraper.py, src/deep_research/utils/config.py, src/deep_research/runtime/assembly.py, config.yaml.
- Modify src/deep_research/utils/types.py and src/deep_research/graph/state.py for persisted acquisition state and pending IDs.
- Tests: tests/test_agents/test_base.py, tests/test_agents/test_react.py, tests/test_agents/test_steps.py, tests/test_agents/test_researcher.py, tests/test_agents/test_native_react_boundary.py, tests/test_agents/test_planner_researcher_seam.py, tests/test_tools/test_document_reader.py, tests/test_tools/test_web_scraper.py, tests/test_config.py, tests/test_runtime/test_assembly.py.

**Interfaces:** AcquisitionState is defined in utils/types.py and has candidate_urls, attempted_urls, read_urls, denied_urls, pending_passage_ids, pending_extraction_ids (list[str], empty defaults), target_id (str | None, default None), remaining_calls (int >=0), consecutive_searches (int >=0, default 0), and empty_searches (int >=0, default 0). Persist acquisition_state_by_target in ResearchState/ResearchStateUpdate with ID-aware merges. All queued URLs are canonicalized. next_acquisition_action(state) -> Literal["search","read","extract","finish"] is pure. Optional tool_policy in run_react_loop validates each requested action immediately before execution, including each call in a native multi-call batch. select_relevant_passages(passages: Mapping[str,str], query: str, limit: int) -> list[str] returns locator IDs.

Add CandidateRecord in types.py: candidate_id, url, title, target_ids, selection_reason, discovered_via (search/memory/document_link), status (queued/read/denied/unusable/deferred), and read_id (nullable). AcquisitionState.candidate_records maps canonical URL to CandidateRecord, default empty; remaining_model_turns is an integer >=0 stamped from effective loop settings. The queue and record states are updated together, and the decision packet reads this manifest, not undisclosed global mutable state.

Add build_acquisition_context(state: AcquisitionState, reads: Mapping[str,ReadRecord], evidence: Mapping[str,EvidenceUnit], *, limit: int) -> str in acquisition.py. It renders complete candidate/read/selected-passage records plus explicit pending IDs, never a sliced serialized response. Add decision_context: str = "" to render_react_messages. BaseAgent and Researcher callbacks rebuild it from their current job state before each provider call; Task 6 wires the same hook for Fact Checker. Acquisition policy updates state after every native call. Preserve the public 200-character summary separately.

- [ ] **RED:** After at most two discovery calls, a queued readable candidate must be read. A failed read must allow another candidate or a bounded targeted fallback search, not deadlock waiting for a successful read. Two empty searches may end no_candidate. Repeated identical queries/denied URLs never consume the whole loop.
- [ ] **RED:** Test a native batch [search, search, search, read], last-slot read reservation, policy rejections not charging external tools, and maximum model-turn bounds. Every proposal gets its corresponding result/rejection observation. NativeToolCall intentionally has no vendor call ID: assign local proposal_id from job_id/turn_index/batch_index in run_react_loop and persist it additively on ReActStep. Do not invent provider IDs or replace the native provider boundary.
- [ ] **RED:** The needed source is the third search result after a long first URL; after the actual search, inspect the next complete_react request and require the exact candidate URL plus read/denied states. A relevant PDF section beyond character 4,000 must appear in the selection/extraction packet. Keep observation_summary_chars=200 in this test so accidentally changing log size cannot make it pass.
- [ ] **RED:** The same URL is proposed seven times across Researcher and Fact Checker and several targets. At unchanged content/version, reuse one acquired body and select new target-relevant passages locally; cache hits do not increase acquired-work counts or imply new corroboration. A changed version invalidates only affected evidence/claims.
- [ ] Task 3 proves this cache contract through two independent acquisition consumers and the Researcher; Task 6 adds the real FactCheckerAgent cross-agent assertion. Do not mark that later integration proved by a mocked agent.
- [ ] **RED:** Test denied HTML → discovered official PDF; same-host working PDF remains allowed. Reject guessed PDF URLs and retries of the exact denied page. No publisher-wide HTML circuit: current evidence does not justify blanket host blocking.
- [ ] **RED:** Relevant material on a late PDF page, late HTML paragraph, CSV row, and table with headers/units/footnotes must reach extraction. Test paraphrased query terms, not only exact numeric matches. Scanned/empty/unparseable documents produce an explicit extraction limitation, not fabricated text.
- [ ] Implement the candidate queue, shared successful-read cache, and deterministic loop policy. Prefer sources needed by an uncovered target or missing independent origin; use snippets only to select candidates. Keep document/API/official repository discovery generic, not hard-coded to battery sites.
- [ ] Replace prefix-only next-decision feedback with build_acquisition_context. Retain complete records within the packet budget, use explicit continuation IDs for overflow, and prioritize unread relevant candidates/new independent origins over repeated query variants. Do not append every memory/search result into extraction prompts.
- [ ] Treat every ordinary memory hit as an optional deduplicated lead requiring Task 1 read admission. Returning memory findings without an actual original read/cache artifact cannot satisfy extraction or target completion.
- [ ] Route explicit PDFs/documents to document_reader; HTML to web_scraper. Honor MIME/redirect outcomes, preserve title/issuer metadata, and avoid retrying a blocked landing page as a document. Reuse legitimate already-readable content.
- [ ] Add query-aware selection over all extracted chunks, retaining adjoining context, table labels, negative qualifiers, and locators. Start with deterministic lexical selection plus target query variants, tested against late/paraphrased material; selection misses create a second bounded passage batch, not a claim of no evidence.
- [ ] Default selected_passages_per_read=4 and evidence_packet_chars=24000; fields validated in config and assembly. These are per-packet bounds. If all required passages do not fit, create continuation packets with explicit omitted IDs; never insist every read fits in one packet.
- [ ] Extract findings by read_id/locator/excerpt/target ID, with exact membership checks against the registry. The provider does not invent source URL/title/hash. Reject altered numbers, missing negation, and excerpts absent from the specified locator.
- [ ] Every read and selected passage receives accepted findings, an explicit relevance/discard reason, or deferred_capacity. Deferred items remain pending; a mistaken discard is detectable by known-support replay.
- [ ] Distinguish transport success, usable extracted content, and substantive target yield. A 200-response short shell/consent/error page does not establish usable content; retain its categorical disposition and try a legitimate alternative. Never classify a short authoritative factual page as unusable by length alone. The historical Federal Register 1,122-character response remains unclassified because its body is absent.
- [ ] Use USGS/CAISO-shaped offline documents with cover/contents followed by relevant tables; positive cases must extract actual findings, while a contents-only version must not invent findings. Zero-yield retry uses another passage batch or a new candidate for the same obligation, not another download of the same cover.
- [ ] Apply Section 2.5 retention: reserve target support/contradictions; four-publisher packet diversity; no registry deletion; distinct publisher/URL/work/finding counters. Emit successful reads and useful evidence yield separately.
- [ ] **GREEN:** Run acquisition, real native-loop, extraction, and tool regressions. Commit, review, fix, push.

~~~python
def test_two_searches_require_read_but_failed_reads_do_not_deadlock():
    state = AcquisitionState(
        candidate_urls=["https://primary.example/report.pdf"],
        consecutive_searches=2, remaining_calls=3
    )
    assert next_acquisition_action(state) == "read"
    state = state.model_copy(update={
        "candidate_urls": [], "denied_urls": ["https://primary.example/report.pdf"],
        "remaining_calls": 2, "consecutive_searches": 0
    })
    assert next_acquisition_action(state) == "search"

def test_late_passage_is_selected():
    passages = {"p1": "Table of contents", "p80": "Queue delay prevents project commissioning."}
    assert select_relevant_passages(passages, "queue delay commissioning", 1) == ["p80"]
~~~

AcquisitionState's remaining fields have empty/zero defaults. Implementation decision order:

~~~python
if state.pending_passage_ids:
    return "extract"
if state.remaining_calls <= 0:
    return "finish"
if state.candidate_urls and (state.consecutive_searches >= 2 or state.remaining_calls == 1):
    return "read"
if not state.candidate_urls and state.empty_searches >= 2:
    return "finish"
return "read" if state.candidate_urls else "search"
~~~

Run: python -m pytest tests/test_agents/test_acquisition.py tests/test_agents/test_react.py tests/test_agents/test_steps.py tests/test_agents/test_researcher.py tests/test_agents/test_native_react_boundary.py tests/test_agents/test_planner_researcher_seam.py tests/test_tools/test_passage_selection.py tests/test_tools/test_document_reader.py tests/test_tools/test_web_scraper.py tests/test_config.py tests/test_runtime/test_assembly.py -q

Also run python -m pytest tests/test_agents/test_base.py -q to verify the actual next-decision request hook.

**Commit:** feat(researcher): acquire and preserve target-bearing passages

### Task 4: Assess source fitness without confusing mirrors, freshness, and independence

**Files:**

- Modify src/deep_research/agents/source_evaluator.py, src/deep_research/agents/sources.py, src/deep_research/agents/evidence.py, src/deep_research/agents/prompts.py, src/deep_research/utils/types.py.
- Tests: tests/test_agents/test_source_evaluator.py, tests/test_agents/test_sources.py, tests/test_agents/test_evidence.py, tests/test_agents/test_evidence_quality_seam.py, tests/test_agents/test_tool_free_prompts.py.

**Interfaces:** assess_new_sources(provider, reads: Sequence[ReadRecord], existing: Sequence[ScoredSource]) -> list[ScoredSource] is an async shared tool-free service used by SourceEvaluatorAgent and Task 6 for newly read verifier sources. It returns a cumulative canonical URL snapshot with assessment_revision. Source fitness includes authority, target relevance, data/effective date, methods, and potential self-interest; absence of metadata is not a made-up score.

- [ ] **RED:** Original report, official mirror, derivative news statistic, independently researched article, company statement, mixed-role article, and unknown issuer dossiers. A high-quality mirror remains usable primary evidence but adds no origin.
- [ ] **RED:** Publication date versus observation period versus forecast horizon; an old still-effective official rule; a newly published article repeating obsolete data; a current high-authority but irrelevant source.
- [ ] **RED:** Unsupported model-proposed issuer/DOI/year is rejected. A real alias evidenced in document metadata resolves; the title's mention of another organization does not transfer ownership.
- [ ] Implement source fitness and transport/identity fields with rationale linked to read metadata. Keep source quality scores separate from claim-specific independence; a high authority score cannot override unsupported content.
- [ ] Overlay assessments onto evidence projections without mutating raw reads or stable evidence IDs. Reassess only new/changed content/metadata; preserve the cumulative snapshot.
- [ ] Preserve the last run's working assessment reuse (7→9→10→10 sources, no final extra model call). Test that a reuse hit depends on content/metadata/temporal fingerprint, not just URL. Propagate derivative role, self-interest, observation date, and target relevance as typed constraints; a high overall score must not wash them away.
- [ ] Extract the shared assessment service so Fact Checker retrievals are assessed before use. No verification-source bypass and no circular graph trip just to score a new document.
- [ ] On provider/schema failure, retain deterministic identity metadata and explicit unscored status. Do not accept unscored sources behind reader conclusions; target the missing assessment in refinement.
- [ ] **GREEN:** Run source/evidence/tool-free tests. Commit, review, fix, push.

~~~python
def test_mirror_is_not_a_new_publisher():
    original = {"issuer": "Example Lab", "serving_host": "lab.example",
                "transport_relation": "original"}
    mirror = {"issuer": "Example Lab", "serving_host": "repository.example",
              "transport_relation": "mirror"}
    assert canonical_publisher_id(original) == canonical_publisher_id(mirror)
~~~

Service implementation sequence: validate read-backed dossiers → reuse same-content assessments → request only missing assessments → validate metadata anchors → resolve alias groups → return merge_source_snapshot(existing, new). Role/transport labels must not directly change overall_score.

Run: python -m pytest tests/test_agents/test_source_evaluator.py tests/test_agents/test_sources.py tests/test_agents/test_evidence.py tests/test_agents/test_evidence_quality_seam.py tests/test_agents/test_tool_free_prompts.py -q

**Commit:** feat(source-evaluator): separate source fitness from evidence independence

---


### Task 5: Atomize claims, keep stable semantic identities, and remove topic starvation

**Files:**

- Create src/deep_research/agents/claim_clusters.py and tests/test_agents/test_claim_clusters.py.
- Modify src/deep_research/agents/fact_checker.py, src/deep_research/agents/identity.py, src/deep_research/agents/prompts.py, src/deep_research/utils/types.py, src/deep_research/utils/config.py, src/deep_research/runtime/assembly.py, config.yaml.
- Tests: tests/test_agents/test_fact_checker.py, tests/test_agents/test_identity.py, tests/test_agents/test_evidence_quality_seam.py, tests/test_config.py, tests/test_runtime/test_assembly.py.

**Interfaces:** atomic_compatible(a: AtomicProposition, b: AtomicProposition) -> bool is a necessary, not sufficient, merge check. consolidate_claims(provider, drafts, existing, evidence) returns canonical Claim snapshots and aliases. select_claim_batch(claims: Sequence[Claim], target_order: Sequence[str], limit: int) -> list[Claim] selects one outstanding obligation per target before filling extra slots. consumed means actually processed, not merely visible in a prompt.

- [ ] **RED:** Merge live queue/PJM/report-cutoff paraphrases with evidence unions. Do not merge different year, period, geography, population, capacity unit, percentage denominator, attribution, forecast status, or negation.
- [ ] **RED:** A+B findings become one proposition with both evidence IDs; two claims from one source remain distinct; a later refinement adding C preserves cluster_id and old citations.
- [ ] **RED:** Six topics/at least twelve claims with a five-item batch do not permanently drop topic six. Every unselected claim is pending; no unprocessed finding gets marked consumed. Add rare-but-critical target before extra low-value claims.
- [ ] **RED:** Metadata relevance depends on the question. A report's publication date cannot satisfy a deployment mechanism, but must be answered if the user asks when it was published.
- [ ] Implement strict atom extraction preserving all qualifiers and evidence IDs. Split compound observations and causal assertions; maintain parent/member IDs.
- [ ] Generate conservative candidate pairs using entities, scope, date/value/unit and semantic similarity from a bounded structured call. Exact-number matching alone must not exclude textual duplicates. Validate proposed equivalence locally for incompatible atoms.
- [ ] Assign a cluster ID once from its first canonical anchor, then persist it. On cluster merging, retain the oldest stable ID and alias the other; never rehash sorted members each refinement.
- [ ] Uncertain duplicate candidates are diagnostics, not automatically hard failures. Do not falsely merge. If both would be published as separate supporting facts, resolve or select one conservative representative with its union provenance before publication. Known duplicate facts cannot inflate counts.
- [ ] Turn the hidden max_claims prefix into an explicit claim_batch_size (initial 5), with a bounded continuation queue. Add claim_batches_per_pass (initial 6); retain and report pending claims when the per-pass or existing run bound is reached. Plan feasibility and critical-target progress must reflect these settings.
- [ ] Reverification cache key includes proposition, evidence content, source assessment/identity revision, temporal scope, and verification prompt/schema version. New evidence or identity corrections invalidate it; wording-only critique does not.
- [ ] **GREEN:** Run extraction/identity/coverage-seam/config tests. Commit, review, fix, push.

~~~python
def test_atomic_numbers_and_periods_do_not_collapse():
    a = AtomicProposition(text="The 2024 queue was 10 GW.",
                          subject="queue", predicate="capacity",
                          value="10", unit="GW", observation_period="2024")
    b = a.model_copy(update={"value": "10000", "unit": "MW"})
    c = a.model_copy(update={"observation_period": "2025"})
    assert not atomic_compatible(a, c)
    # Unit equivalence requires an explicit checked normalization, not text matching.
    assert not atomic_compatible(a, b)
~~~

Implementation invariant for merges:

~~~python
canonical_id = existing_cluster.cluster_id
merged = existing_cluster.model_copy(update={
    "evidence_ids": sorted(set(existing_cluster.evidence_ids) | set(incoming.evidence_ids)),
    "member_claim_ids": sorted(set(existing_cluster.member_claim_ids) | set(incoming.member_claim_ids)),
    "target_ids": sorted(set(existing_cluster.target_ids) | set(incoming.target_ids)),
})
assert merged.cluster_id == canonical_id
~~~

Run: python -m pytest tests/test_agents/test_claim_clusters.py tests/test_agents/test_fact_checker.py tests/test_agents/test_identity.py tests/test_agents/test_evidence_quality_seam.py tests/test_config.py tests/test_runtime/test_assembly.py -q

**Commit:** feat(claims): preserve semantic identity and fairly schedule evidence obligations

### Task 6: Verify the claim-specific evidence union and make failures diagnosable

**Files:**

- Modify src/deep_research/agents/fact_checker.py, src/deep_research/agents/evidence.py, src/deep_research/agents/acquisition.py, src/deep_research/agents/source_evaluator.py, src/deep_research/agents/prompts.py, src/deep_research/agents/events.py, src/deep_research/utils/types.py.
- Modify src/deep_research/providers/deepseek_provider.py and src/deep_research/providers/contracts.py only for additive bounded successful-repair diagnostics; reuse existing retry behavior.
- Tests: tests/test_agents/test_fact_checker.py, tests/test_agents/test_evidence.py, tests/test_agents/test_evidence_quality_seam.py, tests/test_agents/test_events.py, tests/test_agents/test_native_react_boundary.py.
- Test provider repair context and categorical diagnostics in tests/test_deepseek_provider.py.

**Interfaces:** claim_evidence_pool(state: ResearchState, claim: Claim) -> list[EvidenceUnit] resolves only claim-linked IDs and explicit target candidate links; validate_adjudication(draft, packet, assessments) -> Claim validates evidence selections and derives status. ClaimVerdictDraft contains a verdict proposal, per-ID SupportAssessment rows, selected support/contradiction IDs, and rationale. No free-form citation URLs or newly invented excerpts.

Add EvidenceEligibility to evidence.py with publisher_id/work_id/origin_group_id (nullable strings), complete_support/read_valid/corroboration_eligible (booleans). eligible_independent_pair(a: EvidenceEligibility, b: EvidenceEligibility) -> bool checks the strict pair after grounded semantic assessment.

- [ ] **RED:** Real FactCheckerAgent receives upstream A+B, both exact passages appear in its adjudication request, zero retrieval tools run, selected IDs produce verified. Same result whether the claim's source_urls lists A, B, or both.
- [ ] **RED:** A+B are only remembered generated findings: no adjudication packet may present them as read evidence or increment eligible publisher counts. If a current original read corrects a repeated high-confidence memory claim, the verified/source-supported reader follows the read, not repetition. All independent_sources diagnostics must derive from validated read support, with memory candidates counted separately.
- [ ] **RED:** A+B were read, but B is missing from extraction/cluster/prompt: each boundary yields the exact missing ID and cannot falsely pass. Conversely two readable unrelated documents are not a proven handoff loss.
- [ ] **RED:** Same work/mirror, same publisher/different works, different publishers/shared statistic, unknown origin, and a search-only second URL cannot verify. A primary mirror remains usable first support.
- [ ] **RED:** Two passages with mismatched period/unit/scope, a compound claim only partially supported, wrong causal direction, quoted speculation, and stale/future/current confusion must fail full entailment.
- [ ] **RED:** Faithful scoped primary attribution is source_supported, not verified; company claim remains attributed; derived arithmetic is not independent observation. Contradictory evidence is retained. Different-period facts are not automatically contradictions.
- [ ] **RED:** If an upstream candidate pair fails semantic support, allow bounded targeted retrieval and re-adjudication; do not stop just because two identities exist. A sufficient pool bypasses a failed or unnecessary retrieval loop.
- [ ] **RED:** A malformed ClaimVerdictDraft followed by a valid response preserves the exact claim/evidence packet and counts both requests; exhausting repair leaves pending adjudication, not consumed evidence or a fabricated verdict. Record bounded schema field paths/error categories and the packet fingerprint, not raw rejected text. The historical trace's normal finish reason does not identify the malformed field.
- [ ] Reuse the provider's existing bounded StructuredValidationDiagnostic/one-repair behavior; do not add another nested retry loop. Add tests/test_deepseek_provider.py to the focused regression run. If the successful-repair return does not expose diagnostic categories, record them through an additive bounded diagnostic hook in src/deep_research/providers/deepseek_provider.py and its contract in src/deep_research/providers/contracts.py; never infer a field path from the schema name.
- [ ] Replace global upstream-URL admission and the new-independent-domain early return with the actual claim-specific union. Resolve aliases, include relevant counterevidence, and select packets preserving both members of candidate pairs and contradictions.
- [ ] Require the provider to assess only IDs it was shown, complete support, scope/time compatibility, and dependence. Validate ID coverage/uniqueness and exact read membership locally. Semantic decisions remain fallible and are tested/reviewed; local URL checks do not prove entailment.
- [ ] Assess verifier-acquired sources through Task 4's shared service before they may carry a report statement. Persist their reads/evidence/assessments for synthesis and later refinement.
- [ ] Wire Task 3's decision-context hook and shared read cache into real claim acquisition. Prove a Researcher-acquired unchanged PDF is reused for a new Fact Checker target without another body download; an added independent passage or changed content/version invalidates the relevant adjudication fingerprint.
- [ ] Add ConflictAssessment rows (claim_cluster_id, evidence_ids, same_scope: bool, material: bool, resolution: resolved/unresolved/not_comparable, rationale) for conflicting candidates. Missing conflict assessment is unresolved, not an implicit dismissal. Preserve rejected counterevidence with an explicit reason.
- [ ] Apply Section 2 status semantics. Material unresolved contradictions preclude settled verified wording. A disputed claim gets a reasoned conflict analysis, not a forced “false” verdict merely because one weak source disagrees.
- [ ] Populate every insufficient claim with a nonempty local reason: evidence_not_admitted, handoff_loss, packet_incomplete, acquisition_failed, no_candidate, capacity_deferred, identity_unknown, same_work, same_publisher, shared_origin, no_complete_support, single_primary_only, provider_unavailable, schema_failed, or model_disagreement. Keep multiple audit flags. Provider/schema failure is not an evidence verdict.
- [ ] Record registry/selected/findings/cluster/prompt/assessed/support/contradiction ID sets, explicit dispositions, identity collapse, and failure class. Full details go to the ledger/quality JSON; events contain bounded IDs/counts, not entire excerpts.
- [ ] **GREEN:** Run union/seam/native-boundary tests; prove no duplicate adjudication at the same fingerprint. Commit, review, fix, push.

~~~python
def test_same_origin_cannot_corroborate_across_publishers():
    a = EvidenceEligibility(
        publisher_id="lab-a", work_id="report-a", origin_group_id="dataset-a",
        complete_support=True, read_valid=True, corroboration_eligible=True)
    b = a.model_copy(update={"publisher_id": "news-b", "work_id": "article-b"})
    assert not eligible_independent_pair(a, b)
    independent = b.model_copy(update={"origin_group_id": "study-b"})
    assert eligible_independent_pair(a, independent)

def eligible_independent_pair(a, b):
    return (
        a.read_valid and b.read_valid
        and a.complete_support and b.complete_support
        and a.corroboration_eligible and b.corroboration_eligible
        and all((a.publisher_id, b.publisher_id, a.work_id, b.work_id,
                 a.origin_group_id, b.origin_group_id))
        and a.publisher_id != b.publisher_id
        and a.work_id != b.work_id
        and a.origin_group_id != b.origin_group_id
    )
~~~

The upstream-A+B test must inspect the production request messages and tool-call recorder; a pure eligible_independent_pair test is not enough.

Run: python -m pytest tests/test_agents/test_fact_checker.py tests/test_agents/test_evidence.py tests/test_agents/test_evidence_quality_seam.py tests/test_agents/test_events.py tests/test_agents/test_native_react_boundary.py -q

**Commit:** fix(fact-checker): adjudicate grounded upstream and retrieved evidence together

### Task 7: Produce an answer, not a claim inventory

**Files:**

- Modify src/deep_research/agents/synthesizer.py, src/deep_research/agents/report.py, src/deep_research/agents/prompts.py, src/deep_research/utils/types.py.
- Tests: tests/test_agents/test_synthesizer.py, tests/test_agents/test_report.py, tests/test_agents/test_synthesis_seam.py, tests/test_agents/test_tool_free_prompts.py.

**Interfaces:** Add ReportStatement: statement_id, text, mode (settled/attributed/inference/contested/context), claim_cluster_ids, evidence_ids, target_ids, answered_dimensions, basis (nullable derivation/explanation). Extend ReportPoint/ReportConstraint compatibly to these fields. Reader sections, summary, tables, captions, uncertainty, and limitations must use statement records; factual prose outside that mapping is invalid. Citation URLs are derived locally from selected evidence.

- [ ] **RED:** Correctly cited but unsupported mechanism, made-up recommendation, wrong geographic extrapolation, invented truncation limitation, and uncited factual table cell must be rejected or repaired.
- [ ] **RED:** An uncertainty sentence says an unchecked number is "not reported" but includes the number (the last reader did this with 890 GW). Reject that leak or route it to support checking; express the missing topic without the unsupported figure. Also verify scope/methodology's assertions about complete claim linkage, duplicate handling, and limitations against the actual rendered content.
- [ ] **RED:** Same fact repeated as multiple independent findings cannot inflate report length or quality; a brief summary restatement plus detailed evidence discussion is allowed but counted once. Do not ban every cluster appearing in both summary and body.
- [ ] **RED:** A verified cluster uses its actual selected verification sources in references, not only origin URLs. Attributed/contested points cite the appropriate source/contradiction. One work with two mirrors produces one reader reference with accessible canonical copy.
- [ ] **RED:** Constraint, comparison, explanatory, factual, and historical questions generate appropriate structures. No mandatory empty constraint table for every question.
- [ ] Compose a compact canonical packet, balanced across critical targets, including exact support/counterevidence, source assessment, dates, open obligations, and measured failures. Carry explicit omission IDs and continuation batches rather than a highest-confidence prefix.
- [ ] Use an answer-first summary: what the evidence establishes, which distinctions change the answer, and the important unresolved limitation. Findings explain mechanisms, scale/time/scope, trade-offs, and implications when the question requires them.
- [ ] Ranking requires an evidenced comparison basis; evidence abundance or model confidence is not importance. When ranking is unjustified, group constraints by type/region and say no defensible universal order was established. Do not fabricate rank, causal mechanism, or geography to fill a table.
- [ ] Primary-attributed statistics may appear with precise source/year/scope, but do not become unqualified settled conclusions. Derived statements show supported premises and uncertainty. Generic recommendations unsupported by the research are excluded.
- [ ] Add deterministic atom/reference checks plus a structured statement-support review of all substantive prose. New factual assertions return to Fact Checker; unsupported connective claims cannot pass because their numbers happen to match. Permit explicitly checked unit conversion/arithmetic through recorded derivations.
- [ ] Keep uncertainty short, topic-specific, and consequential. Separate “not acquired,” “uncertain/conflicting,” and “outside scope.” Never assert unavailable/truncated evidence without a recorded disposition. Detail belongs in the ledger, not repeated technical lists in reader prose.
- [ ] Derive qualitative evidence labels from status/source breadth; avoid naked 0.90 confidence as a calibrated probability. Show generated-on separately from evidence-as-of/data period.
- [ ] Enforce <=8,000 words unless AnswerContract explicitly requests more; backmatter <=0.35 of rendered reader characters including Methodology/References. Move audit bulk into the separate ledger; do not remove necessary citations to hit the ratio.
- [ ] **GREEN:** Run composition/citation/renderer tests and inspect rendered Markdown for all answer forms. Commit, review, fix, push.

~~~python
def test_citation_urls_come_from_selected_support():
    evidence = {
        "e1": EvidenceUnit(evidence_id="e1", read_id="r1",
             source_url="https://independent.example/study",
             source_title="Independent study", locator="p3",
             excerpt="Study finding.", target_ids=["t1"], origin="fact_checker")
    }
    assert statement_source_urls(["e1"], evidence) == ["https://independent.example/study"]

def statement_source_urls(evidence_ids, evidence):
    return list(dict.fromkeys(evidence[item].source_url for item in evidence_ids))
~~~

statement_source_urls(evidence_ids: Sequence[str], evidence: Mapping[str,EvidenceUnit]) -> list[str] lives in report.py; unknown IDs fail validation before rendering.

Run: python -m pytest tests/test_agents/test_synthesizer.py tests/test_agents/test_report.py tests/test_agents/test_synthesis_seam.py tests/test_agents/test_tool_free_prompts.py -q

**Commit:** feat(synthesizer): compose substantive evidence-bound answers

### Task 8: Make the Critic a calibrated editor with actionable defects

**Files:**

- Modify src/deep_research/agents/critic.py, src/deep_research/agents/prompts.py, src/deep_research/utils/types.py, src/deep_research/utils/config.py, src/deep_research/runtime/assembly.py, config.yaml.
- Modify src/deep_research/evaluation/cases/critic.py.
- Tests: tests/test_agents/test_critic.py, tests/test_agents/test_tool_free_prompts.py, tests/test_evaluation/test_cases_critic.py, tests/test_evaluation/test_evaluators_agents.py, tests/test_runtime/test_assembly.py.

**Interfaces:** CritiqueGap adds gap_id, target_ids, claim_cluster_ids, statement_ids, kind, severity (critical/major/minor), repair_action, problem, recommended_queries. Kinds: coverage, missing_support, acquisition, identity, contradiction, semantic_duplicate, source_quality, mechanism, freshness, presentation. Actions: extend_plan, acquire, assess_source, adjudicate, consolidate, synthesize. An original-question omission uses target_ids=["question"] and extend_plan; it does not fabricate an existing topic ID. build_critic_packet(state, composition) includes the original question, answer contract, full reader content, evidence, hard checks, and open targets.

- [ ] **RED:** Critic has no tools and performs no ReAct/discovery calls. A search snippet cannot appear as verification evidence.
- [ ] **RED:** A major contradiction, fabricated limitation, and citation near the report's end remain visible beyond old prefix boundaries. Oversized evidence is batched without omitting report statements.
- [ ] **RED:** Calibration cases: strong answer; same answer with one minor gap; missing critical topic; unsupported central assertion; appropriately attributed primary fact; false independent-pair claim; polished verbose non-answer; honest but substantively incomplete answer.
- [ ] **RED:** A malformed CritiqueDraft can be repaired only against the same complete report/evidence fingerprint. Exhausted repair returns an explicit failed review, never a guessed score or empty-gap acceptance. Record schema field paths/categories and attempt count without restoring raw provider payload logging.
- [ ] Remove search/memory spot-check loops and set critic tool budget to zero. Review the exact candidate, not an abridged earlier draft.
- [ ] Keep >=7 as the editorial acceptance threshold. Score whole answer quality against the question; deterministic gates can block acceptance but cannot script a high score. Attribution and corroboration are judged according to Section 2, not penalized or rewarded indiscriminately.
- [ ] Require every major defect to identify affected statements/targets, what would fix it, and which action can do so. Queries only for acquisition. “Improve quality” and vague “more sources” are not actionable gaps.
- [ ] Calibrate paired examples by broad bands/ordering, not a demanded exact score. A minor omission should not collapse an otherwise sound answer; one unsupported central conclusion must not pass because the prose is polished.
- [ ] Measure false acceptance, false rejection, and missed-defect rates separately. Critic agreement with itself is not ground truth; independent report review and source checks remain required.
- [ ] Re-pin changed prompt/schema fingerprints deliberately with version rationale.
- [ ] **GREEN:** Run tool-free, packet, gap, and calibration-contract tests; paid semantic calibration happens in Task 13. Commit, review, fix, push.

~~~python
def test_presentation_gap_does_not_request_search():
    gap = CritiqueGap(
        gap_id="g1", target_ids=["t1"], claim_cluster_ids=[],
        statement_ids=["s1"], kind="presentation", severity="major",
        repair_action="synthesize", problem="The answer is repeated in three lists.",
        recommended_queries=[])
    assert gap.repair_action == "synthesize"
    assert not gap.recommended_queries
~~~

Add model validators: at least one affected target/statement/cluster for major gaps; acquire requires a concrete missing obligation; presentation rejects nonempty recommended_queries.

Run: python -m pytest tests/test_agents/test_critic.py tests/test_agents/test_tool_free_prompts.py tests/test_evaluation/test_cases_critic.py tests/test_evaluation/test_evaluators_agents.py tests/test_runtime/test_assembly.py -q

**Commit:** refactor(critic): review complete evidence packets with typed repair actions

### Task 9: Repair only the failed obligation and stop genuine non-progress

**Files:**

- Modify src/deep_research/graph/state.py, src/deep_research/graph/nodes.py, src/deep_research/graph/orchestrator.py, src/deep_research/utils/types.py.
- Modify src/deep_research/agents/researcher.py, src/deep_research/agents/source_evaluator.py, src/deep_research/agents/fact_checker.py, src/deep_research/agents/synthesizer.py.
- Modify src/deep_research/agents/planner.py for extension-only graph integration; include tests/test_agents/test_planner.py in the focused routing regression run.
- Tests: tests/test_state.py, tests/test_graph/test_state.py, tests/test_graph/test_nodes.py, tests/test_graph/test_orchestrator.py, tests/test_agents/test_researcher.py, tests/test_agents/test_fact_checker.py.

**Interfaces:** RefinementTarget carries gap_id, target_ids, claim_cluster_ids, statement_ids, action, requested_dimension, queries. ResearchProgress carries completed_target_ids, assessed_support_fingerprints, resolved_gap_ids, pending_work_ids, unresolved_major_gap_ids, and composition_fingerprint. Persist refinement_targets and progress_history on ResearchState/ResearchStateUpdate; history is bounded by the existing macro-iteration ceiling plus the initial checkpoint. route_refinement(target) -> node name uses typed actions; progress_improved(before, after) -> bool compares substantive repairs, not raw event volume or critic wording.

- [ ] **RED:** Missing B routes only its affected target/claim; source assessment failure routes to evaluator; already-present but omitted mechanism routes to synthesis; absent mechanism evidence routes to acquisition; contradiction routes to adjudication.
- [ ] **RED:** A topic has one raw metadata finding and is absent from Critic gaps, but its required economics/safety target remains unanswered. It must be eligible for repair. Replace _has_prior_finding-based interim satisfaction with validated required-target completion; union mechanically unmet targets with Critic-requested defects rather than letting Critic silence suppress them.
- [ ] **RED:** A critical original-question dimension absent from the plan routes to Planner extension, preserves existing obligations/IDs, increases rather than shrinks the coverage inventory, and then researches the new target. Merely adding the target is not answered-target progress.
- [ ] **RED:** A new independent supporting passage counts as progress before the final verdict changes; irrelevant extra searches/pages do not. Fixing a duplicated paragraph counts as presentation progress.
- [ ] **RED:** Identical fingerprints after a fully processed targeted repair stop before a second unchanged pass. Pending deferred evidence must be processed or explicitly capacity-limited, not mislabeled no_progress.
- [ ] Implement typed route dispatch and selective cumulative updates. All new acquisitions receive source assessment before adjudication; presentation-only fixes reuse evidence.
- [ ] Planner extension and the resulting acquisition form one repair job. Evaluate no_progress after that job finishes, not between adding a target and attempting its evidence. An added target alone is not answered coverage.
- [ ] Invalidate only affected claim/source/report reviews when inputs change. Keep stable IDs; preserve unrelated verified claims and target coverage.
- [ ] Use existing macro-iteration ceiling and explicit micro-batch continuations; do not create unbounded hidden loops. Stop reason distinguishes no_progress, pending_capacity, evidence_unavailable, provider_failure, and max_iterations.
- [ ] Retryable extraction/adjudication failure must leave the corresponding items pending, not consumed. A citation-bearing cache entry is reused only at a matching content/identity/temporal fingerprint; changed values at the same URL force rechecking.
- [ ] Compare support/target/defect state after each completed repair. Score-only or wording-only changes are not evidence progress. Publication-changing repairs require a new report review in Task 10.
- [ ] **GREEN:** Run all state/graph/targeting regressions. Commit, review, fix, push.

~~~python
def test_new_support_is_progress_even_before_a_verdict_changes():
    before = ResearchProgress(
        completed_target_ids=[], assessed_support_fingerprints=["a"],
        resolved_gap_ids=[], pending_work_ids=["b"],
        unresolved_major_gap_ids=["g1"], composition_fingerprint="old")
    after = before.model_copy(update={
        "assessed_support_fingerprints": ["a", "b"], "pending_work_ids": []})
    assert progress_improved(before, after)
    assert not progress_improved(after, after)
~~~

Route table implemented in graph/nodes.py:

~~~python
REPAIR_NODES = {
    "extend_plan": "planner",
    "acquire": "researcher",
    "assess_source": "source_evaluator",
    "adjudicate": "fact_checker",
    "consolidate": "fact_checker",
    "synthesize": "synthesizer",
}
~~~

Run: python -m pytest tests/test_state.py tests/test_graph/test_state.py tests/test_graph/test_nodes.py tests/test_graph/test_orchestrator.py tests/test_agents/test_researcher.py tests/test_agents/test_fact_checker.py -q

**Commit:** feat(graph): target evidence and editorial repairs with meaningful progress

---


### Task 10: Replace proxy quality scores with complete semantic report review

**Files:**

- Create src/deep_research/agents/report_review.py and tests/test_agents/test_report_review.py.
- Modify src/deep_research/agents/quality.py, src/deep_research/utils/types.py, src/deep_research/utils/config.py, src/deep_research/runtime/assembly.py, src/deep_research/graph/nodes.py, src/deep_research/graph/state.py, src/deep_research/graph/orchestrator.py, src/deep_research/providers/validation.py, config.yaml.
- Modify src/deep_research/e2e_evaluation/evaluators.py and src/deep_research/e2e_evaluation/models.py to distinguish legacy structural diagnostics from semantic judgments.
- Tests: tests/test_agents/test_quality.py, tests/test_graph/test_nodes.py, tests/test_graph/test_state.py, tests/test_runtime/test_assembly.py, tests/test_config.py, tests/test_e2e_evaluation/test_evaluators.py.

**Interfaces:** ReportReviewInput contains original question, answer contract, full reader content, statement map, selected evidence/assessment records, initial plus expanded target inventories, and deterministic checks. ReportReview contains status (scored/incomplete/provider_failed), dimensions, defects (typed CritiqueGap), per-statement dispositions, reviewed_statement_ids, input_fingerprint, rubric_version, and rationale. Persist report_review: ReportReview | None on ResearchState/ResearchStateUpdate; replacing the composition invalidates it unless its semantic fingerprint matches. review_report(provider, packet: ReportReviewInput) -> ReportReview is asynchronous and tool-free. semantic_review_passes(review) -> bool is local.

Dimensions keep the existing seven names but gain semantic definitions: completeness (answers original question), prioritization (importance and qualifications justified), evidence_quality (correctness/source fitness/entailment), attribution (faithful provenance and scope), uncertainty (calibrated and useful), readability (coherent and economical), actionability (usefulness for the requested task, not obligatory recommendations). A factual question may score highly without “should.”

- [ ] **RED:** A clean, correctly sized report with “should” and many ranked bullets but no substantive answer must fail semantic review. Removing that keyword alone cannot alter deterministic acceptance.
- [ ] **RED:** Test substantive coverage: a cutoff-date claim does not cover technical constraints; an unsupported topic association does not count; unknown unanswered critical targets block acceptance even if superficial topic coverage is high.
- [ ] **RED:** Review a late contradictory statement beyond 16,000 characters, fabricated citation, unqualified single-source conclusion, supported attribution, incorrect comparison denominator, and invented limitation.
- [ ] **RED:** A missing statement review, missing evidence batch, provider/schema failure, or fingerprint mismatch is incomplete/failed, not a default pass. A cosmetic status badge must not invalidate the semantic-content fingerprint.
- [ ] Replace the production proposal to reuse judge_whole_report's structural formula. Retain legacy diagnostics with explicit structural-only labels for historical compatibility; never call them an independent report judge.
- [ ] Implement source-bound semantic review using a fresh request context that does not see the Critic's score, prior run score, target threshold, or a suggested verdict. The reviewer can see hard defects and evidence, not acceptance coaching.
- [ ] Use an independently configured report_judge call role via LLMConfig.resolve_for("report_judge"), initially max effort with the production provider. Preflight validates this extra service role without adding it to the six-agent registry. All provider usage remains counted. A separate request is independent process review, not proof of independent model errors; external source review in Task 13 addresses that limit.
- [ ] Ensure full statement/evidence coverage. If evidence exceeds a packet, review per-statement batches plus a whole-report cross-section pass. The report itself must not be prefix-clipped; oversized reports are reviewed section-by-section with a complete manifest and cross-section checks. Missing coverage blocks a scored result.
- [ ] Enforce exactly seven finite scores in [0,1], local mean >=0.80, no unresolved critical/major semantic defect, complete statement coverage, and matching content fingerprint. No score averaging can hide a false settled claim.
- [ ] Compute substantive coverage from target-answer assessments plus local evidence-policy validation. Keep the original denominator and record target/topic metrics separately.
- [ ] Wire terminal review after an editorially acceptable candidate, or at the terminal bounded pass for a partial report where feasible. Review defects can consume an existing targeted-refinement opportunity; no new unbounded review loop. Reuse a review only for an identical semantic input fingerprint.
- [ ] Treat terminal-review provider/schema failure as an explicit quality-assessment failure: preserve completed report evidence, set review status provider_failed or incomplete and quality partial, and use strict exit 4; do not misclassify it as a successful review or an unrelated graph crash.
- [ ] Acceptance requires deterministic integrity, substantive coverage/critical targets, critic >=7, and scored semantic review >=0.80 without major defects. Partial reports remain publishable with honest status. Missing reviewer/provider support cannot silently revert strict acceptance to critic-only.
- [ ] Determine content quality before publication. Validate publication consistency after writes in Task 11; avoid a circular gate requiring artifact paths before artifacts can be created.
- [ ] **GREEN:** Run review/quality/graph/provider-config regressions. Commit, review, fix, push.

~~~python
REVIEW_DIMENSIONS = {
    "completeness", "prioritization", "evidence_quality", "attribution",
    "uncertainty", "readability", "actionability",
}

def semantic_review_passes(review):
    scores = review.dimensions
    return (
        review.status == "scored"
        and set(scores) == REVIEW_DIMENSIONS
        and all(math.isfinite(x) and 0 <= x <= 1 for x in scores.values())
        and sum(scores.values()) / 7 >= 0.80
        and not any(g.severity in {"critical", "major"} for g in review.defects)
    )

def test_review_cannot_average_away_a_major_false_claim():
    review = ReportReview(
        status="scored", dimensions={key: 1.0 for key in REVIEW_DIMENSIONS},
        defects=[CritiqueGap(
            gap_id="g1", target_ids=["t1"], claim_cluster_ids=["c1"],
            statement_ids=["s1"], kind="missing_support", severity="critical",
            repair_action="adjudicate", problem="The main number is not in the source.",
            recommended_queries=[])],
        per_statement_dispositions={"s1": "unsupported"},
        reviewed_statement_ids=["s1"], input_fingerprint="packet1",
        rubric_version=2, rationale="A central unsupported assertion.")
    assert not semantic_review_passes(review)
~~~

Before calling semantic_review_passes, validate packet/statement coverage and fingerprint; incomplete review.status cannot be scored. QualitySnapshot stores review status/score separately from critic_score and structural diagnostics.

Run: python -m pytest tests/test_agents/test_report_review.py tests/test_agents/test_quality.py tests/test_graph/test_nodes.py tests/test_graph/test_state.py tests/test_runtime/test_assembly.py tests/test_config.py tests/test_e2e_evaluation/test_evaluators.py -q

**Commit:** feat(quality): require substantive coverage and semantic report review

### Task 11: Make CLI reports and artifacts unambiguous and consistent

**Files:**

- Modify src/deep_research/cli.py, src/deep_research/runtime/outcome.py, src/deep_research/agents/report.py, src/deep_research/graph/nodes.py, src/deep_research/utils/types.py, src/deep_research/api/models.py, src/deep_research/api/sessions.py, src/deep_research/tools/write_document.py, README.md.
- Tests: tests/test_cli/test_arguments.py, tests/test_cli/test_render.py, tests/test_cli/test_entrypoint.py, tests/test_cli/test_report_quality_acceptance.py, tests/test_runtime/test_outcome.py, tests/test_agents/test_report.py, tests/test_api/test_sessions.py, tests/test_api/test_stream_and_artifacts.py, tests/test_tools/test_write_document.py.

**Interfaces:** Extend ResearchOutcome/API additively with quality_path, quality_contract_version, semantic_review_status/score, target/topic progress, evidence-status counts, duration_seconds. Add render_quality_record(state, composition, review) -> dict[str,JsonValue] in report.py. Quality record includes source/read/evidence/claim/statement IDs, selected excerpts, dispositions, identity aliases, configuration fingerprints, review findings, and artifact-content hashes.

- [ ] **RED:** CLI summary, reader, ledger, and JSON agree on status, counts, citations, scope, dates, pending targets, and review fingerprint. Serialized IDs must permit replay of every cited statement.
- [ ] **RED:** Failure to publish a required artifact cannot leave accepted advertised output. A final header/status update cannot alter reviewed claims. Preserve existing exit/interrupt precedence.
- [ ] **RED:** Normal output distinguishes “corroborated,” “primary-attributed,” “contested,” and “not established.” It never calls 16 claims verified just because they were checked.
- [ ] Render a compact outcome: direct answer/artifact paths, accepted/partial status and specific reason, substantive topic/critical-target completion, distinct corroborated claims and independent works, semantic review result, significant unresolved questions, elapsed time.
- [ ] Aggregate repeated errors by agent/type/cause, keeping affected target IDs in detail. A resolved 403 fallback is not an unresolved report defect; an unrecovered access problem names the missing question. No generic-only limitation when a concrete cause is recorded.
- [ ] Distinguish per-job attempts from final canonical claims; physical read calls from unique validated works/cache reuse; and all assessed sources from cited assessed sources (the last run had 10 assessed, 8 cited). A previously researched topic omitted from a refinement pass must not say "never researched." Emit no independent-publisher count derived from ordinary memory.
- [ ] Add --debug-events for complete bounded events. Normal output summarizes phases/outcomes; --verbose includes tool/request totals. Neither floods budget-event rows. Label model requests, searches, reads, retries, dropped proposals, and tokens separately.
- [ ] Publish reader Markdown, evidence Markdown, and quality JSON from one frozen composition. Stage writes through the existing publisher; expose artifact paths only after the set is complete. Retain a truthful failure record if a write fails. No claim that multiple file renames form one atomic filesystem operation.
- [ ] Compute semantic fingerprint excluding only generated presentation status/badge fields; content/ref/target changes require review. Artifact byte hashes are calculated on the actual final bytes and stored without self-referential hashing of the quality JSON.
- [ ] Keep exits: 0 for completed non-strict execution; 1 configuration; 2 usage; 3 graph failure; 4 strict quality non-acceptance; 130 interrupt. Non-strict 0 is not an accepted-quality claim.
- [ ] Update README quality semantics, source attribution, context/as-of wording, debug mode, and artifact meanings. API additions must not break existing clients; no raw full-page or provider payloads in API summaries.
- [ ] **GREEN:** Run CLI/renderer/outcome/API tests and inspect normal/verbose/debug snapshots. Commit, review, fix, push.

~~~python
def test_missing_semantic_review_keeps_strict_exit_four():
    snapshot = {"hard_failures": [], "critic_score": 8,
                "semantic_review_status": "incomplete", "quality_status": "partial"}
    assert strict_quality_exit(snapshot, require_quality=True) == 4
    assert strict_quality_exit(snapshot, require_quality=False) == 0

def strict_quality_exit(snapshot, *, require_quality):
    return 4 if require_quality and snapshot["quality_status"] != "accepted" else 0
~~~

strict_quality_exit is a pure CLI helper for completed runs only; configuration/graph/interrupt exits are handled first and remain unchanged.

Run: python -m pytest tests/test_cli tests/test_runtime/test_outcome.py tests/test_agents/test_report.py tests/test_api/test_sessions.py tests/test_api/test_stream_and_artifacts.py tests/test_tools/test_write_document.py -q

**Commit:** feat(cli): publish coherent reader evidence and quality artifacts

### Task 12: Prove the real agents and CLI in an offline adversarial matrix

**Files:**

- Create src/deep_research/e2e_evaluation/replay.py and tests/test_e2e_evaluation/test_real_agents.py.
- Modify src/deep_research/e2e_evaluation/cases.py, src/deep_research/e2e_evaluation/evaluators.py, src/deep_research/e2e_evaluation/models.py, src/deep_research/e2e_evaluation/runner.py.
- Modify src/deep_research/evaluation/cases/planner.py, researcher.py, source_evaluator.py, fact_checker.py, synthesizer.py, critic.py (all six exact modules in that directory).
- Modify src/deep_research/evaluation/cli.py, config.py, dependencies.py, models.py, evaluators.py, judging.py, targets.py (all under src/deep_research/evaluation/).
- Tests: tests/test_e2e_evaluation/test_cases.py, test_evaluators.py, test_runner.py; tests/test_evaluation/test_cases_registry.py, test_cases_planner.py, test_cases_researcher.py, test_cases_source_evaluator.py, test_cases_fact_checker.py, test_cases_synthesizer.py, test_cases_critic.py, test_evaluators_agents.py, test_judging.py, test_cli.py, test_dependencies_controlled.py, test_dependencies_live.py.
- Create docs/superpowers/validation/2026-09-16-real-agent-controlled-validation.md. Update docs/superpowers/validation/2026-09-15-release-status.md.

**Interfaces:** ReplayScenario defines raw search/HTTP/document responses, schema-specific scripted completion responses, expected request packet IDs, and CaseExpectation. build_replay_runtime uses runtime.assembly.build_runtime/build_agent and fake external clients. It never instantiates ScriptedGraphAgent for release tests. CaseExpectation separates expected terminal status/exit from test passed, names required answerable targets/claims, forbidden assertions, expected gaps, and allowed failure classes.

- [ ] **RED:** Spy on all six production agent classes, the real graph, terminal reviewer, renderer, publisher, and CLI entrypoint. A no-op/broken Researcher, missing upstream evidence, disabled support check, or invented synthesis statement must make the case fail.
- [ ] Replace agent substitutions in the production-quality matrix with real agents and scripted provider/tool boundaries. Keep old ScriptedGraphAgent cases explicitly labeled graph-only historical regression tests.
- [ ] Scripted completions assert the actual incoming schema/target/evidence packet before returning. They cannot populate missing state themselves or return a preassembled final ResearchState. Fake read content must actually entail the scripted claim.
- [ ] Add a network-denial guard for sockets/HTTP/provider/tracker transports. Tests must not load secrets or create external LangSmith runs.
- [ ] Test both cold and warm memory through the production memory bridge/tool/read classifier, not a memory-disabled fake that hides TR-01. Warm fixtures include repeated obsolete high-confidence generated claims, a known valid original cache artifact, forged cache metadata, and a fresh changed source. Reset isolated storage between repetitions and assert the initial memory/cache manifest.
- [ ] Assert next-decision packets retain the required late search candidate and document locator despite 200-character public summaries. A mutation restoring query_memory-as-read or prefix-only decision context must fail the positive/negative cases, just like dropping B does.
- [ ] Implement the matrix below. Each case has three offline repetitions for deterministic order/identity and state-isolation checks; they are not claims of model reliability.
- [ ] Use expected negative outcomes: partial/exit 4 may mean the test passed. Do not require every intentionally unsupported case to be accepted. Positive fixtures require nonzero useful claims and all named critical targets, preventing vacuous abstention.
- [ ] Preserve existing case IDs where meaningful and version their semantics. Derive the exact suite inventory from the versioned manifest, not a hard-coded “exactly three/seven cases” count.
- [ ] Extend individual-agent fixtures with new read/target/identity/status contracts and high-risk positive/negative pairs. Add production-parity CLI flag --production-parity, with resolved settings in outputs; experimental target overrides are labeled separately.
- [ ] Separate output correctness, task completeness, schema/provenance, and execution/provider status. A judge/provider failure invalidates scoring even when hard gates pass. Missing denominators are “not assessed,” not 1.0.
- [ ] Move the existing structural formula out of semantic-judge labeling. Offline semantic review is explicitly scripted; live semantic scores use the real review service. Test judge packet visibility and late-report coverage.
- [ ] Record exact SHA, case versions, test counts, fingerprints, reviewed snapshots, and zero-network evidence. Only then mark CONTROLLED READY / LIVE NOT VALIDATED.
- [ ] Commit, fresh review, fix, rerun, push.

| Case ID | Expected product result | Decisive assertion |
| --- | --- | --- |
| broad-constraints | accepted / 0 | Six topics with substantive mechanisms; late topics survive five-item batching; independently supported critical conclusions |
| comparative-conflict | accepted / 0 | Explains differing populations/methods; no invented universal winner; both sources cited |
| refinement-evidence-recovery | accepted / 0 | Only missing target is acquired/rechecked; new support changes fingerprint; no repeated stable checks |
| blocked-html-pdf-fallback | accepted / 0 | Exact denied URL is not retried; discovered official PDF supports answer; mirror not double-counted |
| same-work-mirror | partial / 4 | Critical independent-pair target has one underlying work; zero false verification |
| semantic-duplicate-claims | accepted / 0 | Queue/cutoff/PJM paraphrases collapse; conflicting units/years remain distinct |
| stalled-refinement | partial / 4 | Fully processed unchanged repair stops; correct unresolved target/cause |
| primary-attribution | accepted / 0 | Exact official measurement question answered as primary-attributed; not falsely verified |
| current-versus-forecast | partial / 4 | Historical observation/future forecast cannot satisfy a required current estimate |
| unsupported-mechanism | partial / 4 | Citations/formatting cannot rescue invented causal mechanism or recommendation |
| judge-failure | partial / 4 | Complete reader artifacts may exist, but absent semantic assessment cannot pass strict mode |
| non-constraint-answer | accepted / 0 | Factual/explanatory answer has suitable structure without meaningless rankings |
| late-contradiction | partial / 4 | Material conflicting passage/statement past prefix limits remains visible and blocks false settlement |
| empty-but-clean | partial / 4 | Zero findings, clean headings, and “should” never constitute a high-quality answer |
| memory-is-not-read | partial / 4 | Two remembered generated claims with different source URLs cannot establish a read or independent support; legacy memory remains a lead |
| validated-cache-reuse | accepted / 0 | Validated original evidence is reused without repeated body downloads; stale/changed/forged cache provenance cannot silently pass |
| decision-context-late-candidate | accepted / 0 | A needed third search result and late document section reach actual subsequent decision/extraction requests despite short public summaries |
| reopen-unanswered-target | accepted / 0 | Required topic work resumes despite one prior metadata finding and no matching Critic gap; completion comes from a substantive supported reader answer |

Case examples use reserved .example URLs with literal locally authored passages. Freeze reference facts/qualifiers before generating scripted responses; reviewers must compare fake content to every expected entailment.

~~~python
def test_negative_case_can_pass_without_product_acceptance():
    expected = CaseExpectation(
        terminal_quality="partial", exit_code=4,
        required_target_ids=[], forbidden_assertions=["two independent works"],
        required_gap_kinds=["identity"], minimum_answerable_claims=0)
    assert expected.terminal_quality == "partial"
    assert expected.exit_code == 4
~~~

For real-agent proof, assert the runtime's six instances are the production classes and inspect the actual CLI invocation result. Add mutation tests that monkeypatch claim_evidence_pool to omit B and statement_source_urls to return an invented URL; both must fail their positive matrix case.

Run:

~~~powershell
python -m pytest tests/test_evaluation tests/test_e2e_evaluation tests/test_cli -q
python -m deep_research.e2e_evaluation suite --tier controlled --repetitions 3
python -m pytest -q
python -m ruff check src tests
git diff --check
~~~

These commands must use fake external boundaries for the controlled end-to-end command. The individual-agent evaluation CLI is paid even for its controlled tier and is reserved for Task 13.

**Commit:** test(evaluation): exercise real agents and CLI against adversarial evidence

### Task 13: Validate output quality with real models, unseeded retrieval, and source review

**Files:**

- Create docs/superpowers/validation/2026-09-16-output-quality-live-protocol.md.
- Create docs/superpowers/validation/2026-09-16-output-quality-agent-results.md.
- Create docs/superpowers/validation/2026-09-16-output-quality-report-results.md.
- Create docs/superpowers/validation/2026-09-16-output-quality-defect-register.md.
- Update docs/superpowers/validation/2026-09-15-release-status.md.
- Store immutable per-run predeclarations/results under docs/superpowers/validation/output-quality-campaign/ using case ID, repetition, and candidate SHA in the filename. Keep large raw payloads under output, referenced by hash.

**Interface:** Each run has a frozen question, candidate/config/prompt/case versions, expected answer type, independently established required dimensions, source-review protocol, declared request/currency bounds, and run identity. Readiness applies only to that tested configuration.

- [ ] Reconcile historical spend/remaining authority; declare costs/ceilings for the stage and obtain confirmation. Never silently reset the old allowance or treat this plan as authorization to buy a new service.
- [ ] First run one high-risk controlled case for each real agent at production-parity settings. Then run the controlled individual-agent suite with three repetitions. Require all hard gates, complete usable outputs, existing per-agent quality floors, and no unresolved major semantic finding; averages alone cannot pass a failed repetition.
- [ ] Review LangSmith per-agent traces before full CLI runs. Source Evaluator/Synthesizer/Critic remain tool-free; Researcher/Fact Checker use exact read evidence; Planner scope and per-agent effective settings match the CLI. Include direct experiment/trace links and missing-link explanations.
- [ ] Run each versioned per-agent live case at the same target configuration. A canned successful A+B fixture is a controlled contract test, not proof that live acquisition found A+B.
- [ ] Then run the narrow document-rich smoke question and the five release questions below. Use actual CLI strict mode, clean per-run state, and unseeded research prompts. Repeat each release question twice at the unchanged candidate/configuration; no best-of-run selection.
- [ ] "Clean" means independently empty long-term/procedural research memory and an explicitly declared source-cache namespace for each release repetition. Record these before the run. Changing session_id alone is insufficient; the baseline entered with five duplicated prior findings. Use temporary isolated stores, never clear the user's working memory.
- [ ] After a passing cold broad regression, run one separately declared warm-memory challenge at the same candidate: repeated obsolete generated claims plus a genuine cached original document must not distort the fresh supported answer. Prescribed challenge content is diagnostic seeding, not another unseeded release success. Predeclare the expected correction and allowed cache behavior; review all load-bearing statements normally.
- [ ] The previously tuned battery/heat-pump topics are development regressions, not blind holdouts. Reserve two additional held-out questions selected and frozen by an independent reviewer after the implementation/prompt freeze, within declared categories below. The execution worker sees them only at run time; no fixtures, prompts, memory seeds, or prescribed source URLs derived from their answer keys.
- [ ] Freeze each held-out question's must-answer dimensions and source-review method before running, not an exact canned prose answer. If no independent question custodian is available, use the reserved questions as unseeded regressions and explicitly report that blind generalization remains unvalidated; do not call the release blind-ready.
- [ ] Store the live rubric/expectations outside runtime inputs: the research agents and terminal reviewer cannot read reference answers, held-out source lists, expected scores, or acceptance coaching. Per-agent judged outcomes are quality evidence only when no provider/schema failure masks an incomplete target output.
- [ ] All runs use the real terminal semantic reviewer >=0.80 and critic >=7, plus deterministic integrity/coverage/critical-target gates. Positive release cases must be independently established as answerable from legitimate accessible evidence; do not predeclare acceptance for an unknowable causal question.
- [ ] After each report, an independent output reviewer reads the complete reader/ledger, without the Critic/reviewer numeric scores or prior run outcome, and checks every load-bearing statement against its cited text. Also inspect the summary, mechanisms, comparisons, tables, implications, uncertainty, and limitations for unsupported additions.
- [ ] Reviewer additionally searches authoritative sources for major omissions, fresher data, and counterevidence. Start with 3–5 authoritative works, expand if needed: five sources are not an arbitrary ceiling for a broad claim audit. Count actual works/origins, not URLs.
- [ ] Record each statement as supported, correctly attributed, justified inference, contested correctly, unsupported, contradicted, or not assessable; record exact source passage/locator and materiality. Missing access is not an automatic pass or accusation of falsehood.
- [ ] Apply the semantic seven-dimension rubric independently; overall >=0.80, no critical/major defect, no false settled claim, no fabricated citation, all critical targets answered. Disagreements between the Critic, terminal reviewer, and external review require source-based resolution, not score averaging.
- [ ] Check whether the report answers the original question, explains mechanisms and decision-relevant distinctions, uses appropriate scope/freshness, prioritizes with evidence, and avoids repeated caveats/metadata filler. Do not reward unsupported advice for containing action verbs.
- [ ] Record cost/latency/evidence yield and policy stops separately. A recovered source failure is not a quality failure; an unresolved critical evidence gap is. A source appearing in search is not a successful read.
- [ ] Keep diagnostic source seeding separate: it can distinguish acquisition from reasoning but never counts as unseeded readiness. Curated source adapters added for general use need their own unseeded regression validation, not question-specific answer injection.
- [ ] Every failed run becomes an immutable record and a defect in Task 14. Do not rerun the same failing configuration indefinitely; stop paid repetition, repair the demonstrated cause, then revalidate under this plan.
- [ ] Commit/review/push each predeclaration and result record in the established workflow. Do not modify historic results into passes.

Initial agent commands (paid):

~~~powershell
python -m deep_research.evaluation agent planner --case scoped-evidence-targets --tier controlled --production-parity --verbose
python -m deep_research.evaluation agent researcher --case read-bearing-acquisition --tier controlled --production-parity --verbose
python -m deep_research.evaluation agent source-evaluator --case work-role-independence --tier controlled --production-parity --verbose
python -m deep_research.evaluation agent fact-checker --case upstream-independent-pair --tier controlled --production-parity --verbose
python -m deep_research.evaluation agent synthesizer --case canonical-evidence-report --tier controlled --production-parity --verbose
python -m deep_research.evaluation agent critic --case typed-gap-calibration --tier controlled --production-parity --verbose
python -m deep_research.evaluation suite --tier controlled --production-parity --verbose
~~~

Task 12 must register those exact high-risk IDs. Use the existing per-agent live command with --tier live --production-parity after controlled review. The configured repetition count must be recorded; do not assume the command default is three.

| Role | Question / selection rule | Purpose |
| --- | --- | --- |
| Smoke, one run, not release proof | What constraints do recent official U.S. reports identify for utility-scale battery storage interconnection? | Read/excerpt/identity/statement path and diagnosis in a document-rich domain |
| Release regression 1, two runs | What are the current constraints on grid-scale battery storage deployment? | Broad coverage, mechanisms, date/scope, independent evidence, previous observed failures |
| Release regression 2, two runs | What constraints do recent official U.S. reports identify for heat-pump deployment? | Known cross-topic regression; no forced denial, observe legitimate fallback when it occurs |
| Release regression 3, two runs | How do the evidence and practical constraints differ for air-source and ground-source heat pumps in existing U.S. homes? | Comparative scope, decision-relevant conditions, no unsupported universal winner |
| Held-out release 4, two runs | Reviewer chooses a document-rich, time-sensitive policy/infrastructure question outside batteries/heat pumps, with identifiable currently effective primary documents | Temporal correctness and generalization beyond tuned topics |
| Held-out release 5, two runs | Reviewer chooses a non-energy explanatory/comparative question with at least one genuine methodological disagreement and enough readable evidence to answer conditionally | Generalization of reasoning, contradiction handling, suitable answer form |

This is one smoke plus ten release runs plus one separately labeled warm-memory diagnostic, plus per-agent evaluations; it may exceed remaining historical authorization. Obtain a suitable stage allocation rather than silently shrinking repetitions and still claiming the same readiness evidence.

Literal CLI invocation for the existing broad regression:

~~~powershell
python -m deep_research "What are the current constraints on grid-scale battery storage deployment?" --config config.yaml --verbose --require-quality
~~~

Use the same flags with each exact frozen question. Capture the program's exit code, not the wrapper's last command. Record package import path so another worktree's installed code cannot produce misleading results.

**Commit family:** docs(validation): record output quality campaign <case-id> <repetition>

### Task 14: Close demonstrated defects and publish the final quality decision

**Files:** The affected implementation/test files owned by Tasks 1–12; docs/superpowers/validation/2026-09-16-output-quality-defect-register.md; create docs/superpowers/validation/2026-09-16-agent-production-readiness-final.md; update docs/superpowers/validation/2026-09-15-release-status.md and docs/superpowers/2026-09-08-cross-agent-planner-fix-parity-fix-log.md.

**Interface:** Every defect row contains defect ID, observed artifact/statement, expected behavior, root-cause boundary, owner task/agent, smallest failing reproduction, fix SHA, review disposition, offline result, and revalidation run IDs.

- [ ] Classify failures by evidence admission/memory contamination, acquisition, decision context, extraction/handoff, identity, entailment/claim handling, synthesis/editorial quality, critic/reviewer calibration, orchestration, or configuration. Use source evidence, not the lowest agent score alone, to assign ownership.
- [ ] Write a focused RED regression for every critical/major defect before fixing its owner module. Use the existing task's contract; this plan explicitly authorizes the implementation repair loop once execution is requested. Do not create another speculative plan for an already-defined defect.
- [ ] A prompt fix is acceptable only with a demonstrated semantic failure and paired positive/negative regression. Do not increase score examples, loosen verdict rules, remove failing cases, seed holdout answers, or inflate budgets as a substitute.
- [ ] Run focused tests, relevant seam tests, full offline suite, and fresh specification/code review. Commit and push only after clean review.
- [ ] Rerun the failed live case with a new immutable predeclaration at the repaired SHA and authorized budget. If common prompts/contracts/routing changed, rerun all affected agent and release cases. The final matrix must apply to one unchanged candidate; old successes from incompatible configurations cannot be pooled.
- [ ] A holdout used to tune a fix is now a regression. Replace it with a fresh reviewer-held question for generalization proof; keep the exposed question as a regression.
- [ ] If evidence is genuinely unavailable, record honest partial output and the remaining limitation. Do not manufacture readiness for that positive release case; choose a replacement only through a documented answerability audit, never because the agent scored badly.
- [ ] If new external authority/spend or material architectural scope beyond these contracts is required, stop and report the concrete dependency. Do not claim readiness. Otherwise continue the in-plan repair loop until the scorecard below is satisfied.
- [ ] At the final unchanged application SHA, rerun the complete offline suite, Ruff, diff checks, and artifact consistency checks. Check all review findings are closed; every live result has trace/config/artifact provenance and independent source-review disposition.
- [ ] Publish one honest outcome: OUTPUT QUALITY READY for the declared tested scope/configuration, or NOT READY with named failed criteria. No “conditionally ready” escape hatch for failed quality gates. This is not a safety/operational-readiness certification.
- [ ] Commit, fresh evidence/documentation review, push, and stop for the user-owned whole-branch review. Do not merge.

~~~powershell
python -m pytest -q
python -m ruff check src tests
git diff --check
git status --short
~~~

**Commit:** docs(validation): record final agent and CLI output quality decision

## 4. Final agent and CLI scorecard

All rows require evidence at the final candidate. Passing unit contracts alone is insufficient; strong scores cannot hide important named defects.

| Surface | Release condition |
| --- | --- |
| Planner | Schema-valid, scoped, time-aware, answer-shaped plans; all original-question critical dimensions present; feasible target batches; no invented sources; at most one memory call; production/evaluation settings match |
| Researcher | Every accepted finding exact-read-backed, never bare-memory-backed; actual decision packets expose required candidates/locators; no repeated body download at unchanged validated version; every selected/read item accounted for; no search starvation/deadlock; positive fixtures yield required evidence; zero unexplained handoff loss |
| Source Evaluator | Every cited source assessed; metadata grounded; mirrors stay usable but never add independent origin; claim-specific derivative/shared-data handling; relevance and date fitness visible |
| Fact Checker | A+B union works without C; stable atomic claims; complete support/contradiction checks; zero false independence; no fifth-claim starvation; all insufficiency reasons and lineage recorded; positive fixtures actually answer |
| Synthesizer | Correct direct answer; justified mechanisms/comparisons/prioritization; all substantive statements mapped; correct citations including verification sources; honest useful uncertainty; no invented limitations, internal IDs, or redundant claim inflation |
| Critic | Tool-free; full packet; paired calibration catches severe defects without punishing sound attribution; typed repairs; >=7 only for a substantively sound candidate |
| Terminal report review | Actual semantic review, not keyword/length proxy; all statements/evidence assessed; >=0.80; no unresolved critical/major defect; exact candidate fingerprint |
| Graph | Targeted evidence/editorial repair; required unanswered targets reopen even if Critic omits them; changed support invalidates checks; unfinished work remains visible; no repeated unchanged pass; consistent final status/publication |
| CLI / API | One coherent answer/report/ledger/JSON; strict exit 4 for non-acceptance; truthful evidence and date labels; concise summary with detailed drill-down; counts/paths/hashes match |
| Offline matrix | All 18 declared cases satisfy their positive or negative expectations in three isolated repetitions, through real agents and CLI; memory/decision-context mutations fail as intended; no network |
| Live / independent review | Per-agent tests at production settings, then both independently cold repetitions of all five release questions pass; two genuine held-outs remain unseeded; separate warm-memory challenge passes; no false settled statement/fabricated citation/major omission; no unassessed claimed success |
| Evidence and ownership | Every important defect closed with regression and review; no rewritten failures; reviewed commits pushed; user retains final branch review |

## 5. Self-review and execution handoff

The amendment specifically removes or corrects these inconsistencies from dd1b93f:

1. Structural scoring was promoted into a semantic production gate.
2. Offline end-to-end cases substituted agents while claiming to validate real-agent seams.
3. Known development questions were labeled blind.
4. Positive acceptance was demanded of intentionally unsupported negative fixtures.
5. Mirrors were rejected as supports instead of collapsed as identities.
6. A strict verification badge was conflated with faithful primary attribution.
7. Work-identity precedence could fail to join mixed-metadata copies; shared-origin independence was absent.
8. Cluster IDs changed when new members arrived; possible duplicate candidates were unconditional hard failures.
9. Read-before-success policy could deadlock after failed reads; host blocking was broader than measured evidence.
10. Four-publisher/packet and five-claim caps could delete required evidence or starve topics.
11. Verifier retrievals had no assured source-assessment path.
12. Coverage and answer quality could be inflated by metadata/ID presence.
13. Source/date/citation checks did not cover every narrative, mechanism, recommendation, and limitation.
14. No-progress logic ignored useful evidence and presentation repair.
15. Report review could hide late sections; publication checks risked circular dependencies.
16. Unvalidated latency/token targets and stale spending allowances were treated as new quality requirements.
17. Production/evaluation effort could differ despite shared agent construction.
18. Failed validation lacked a complete in-plan defect closure path.

The last-run trace review adds nine binding corrections (TR-01–TR-09): ordinary memory is not a source read; model decision context is not a log summary; successful reads need substantive yield and reuse; current plans need runtime date anchoring and defensible criteria; source assessments must constrain settlement; verification counts require valid original evidence; Critic silence cannot complete a target; caveats/methodology cannot introduce unchecked facts; schema retries must preserve the actual evidence packet. All have owner tasks, positive/negative regressions, and release checks above.

Execution order: Tasks 0–12 offline and reviewed; Task 13 authorized live evidence; Task 14 targeted repairs and final closure, repeating relevant validation as needed. This is one final implementation program with a built-in repair loop, not a promise that one attempt will pass.

Before each task, read its exact file list, interfaces, binding semantics, test expectations, trace-review findings, and prior task outputs. Do not dispatch an implementer with only the task heading. Astra owns this plan and its amendments; implementation uses sequential Luna Max code dispatches and fresh reviews with available fast/priority execution, then pushes after review. There is no Sol/High browser planning handoff. If dispatch capability differs, report the limitation rather than silently changing it.

**Plan amendment verification:** Reconciled freshly fetched merged-main code, all 1,079 last-run spans, local report/ledger/log hashes, and the offline memory-admission reproduction. Checked source/target/status semantics, task dependencies, file ownership, positive/negative expectations, and release evidence requirements. Merged-main baseline: 3,100 offline tests passed, one live test deselected. This is plan review and baseline verification, not proof the proposed repairs are implemented. The implementation checkboxes remain unchecked.

Documentation checks for this amendment: fifteen numbered tasks, eighteen offline matrix cases, seventeen Python snippets parsed for syntax, balanced fences, no unresolved plan placeholders, and a clean whitespace diff. These checks do not execute the proposed test snippets or certify semantic correctness.
