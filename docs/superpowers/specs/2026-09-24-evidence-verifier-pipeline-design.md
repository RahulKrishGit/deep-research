# Evidence Verifier pipeline: replace the fact checker, simplify the graph

Status: draft for review. Branch `codex/evidence-verifier-pipeline`, from
`1008fdc` on `codex/agent-cli-quality-trace-plan`.

## 1. Why

Four live runs of the benchmark question

> How much grid-scale battery storage capacity was added in the United States
> in 2024, and what do the latest forecasts project for 2025?

were each judged NOT GREAT by an independent audit. The architecture audit of
2026-09-24 (`agent://ArchAudit`) found:

- The fact checker took 61-70% of every run's wall time and 94% of its web
  searches, most of it spent looking for a second, independent organisation to
  confirm figures that only one issuer publishes. In 89 adjudications across
  the four runs it confirmed none with a second source, returned
  `insufficient_evidence` 88 times and `contradicted` once, and that one was
  wrong.
- It caused or amplified 16 of about 35 ranked report defects: figures dropped
  when findings were re-extracted as claims, claims left unbound to targets,
  the "Insufficient independent evidence" dump, duplicate bullets, and
  mislabelled contradictions. It prevented 0-1.
- The honesty rules the user relies on are enforced by the researcher's
  verbatim checks and the report writer's guards, not by the fact checker. No
  stage checks that a finding's figure is on its cited page. That check is
  small and deterministic; replayed on the retained runs, all 25 audit-2
  findings and all 34 audit-3 claims pass it.
- The critic duplicates the report review, and the planner creates required
  targets the question never asked for. Each unanswered one is a guaranteed
  gate failure.

## 2. Decisions (user-approved, 2026-09-24)

| # | Decision |
|---|---|
| D1 | Replace the fact checker with the **Evidence Verifier**. It has two steps: **Figure Match** (code) and **Context Check** (one batched AI call, no web search). This is the audit's option "A+B". |
| D2 | **No corroboration step.** No stage searches for a second source, and the report never claims one source confirms another. This supersedes the earlier rule that comparisons, rankings, derivations and "independently confirm" requests need an independent pair. |
| D3 | Include all four simplifications: a smaller plan, one reviewer (the critic merged into the Report Reviewer), a bigger research budget, and a field-based duplicate key replacing claim clustering. |
| D4 | Extra research passes are configurable through `max_extra_passes`, **default 1**. A pass runs only when the Report Reviewer names a missing required target, and it is targeted to those targets only. |
| D5 | Rename the synthesizer to **Report Writer** and the report review to **Report Reviewer**. |
| D6 | Clean cutover on a new branch. No feature flag, no retained old path. |
| D7 | Provenance reaches the reader as the code-built label on every figure (organisation, kind, release), from the Context Check's verified fields. |
| D8 | The LLM replaces the code figure checks: the Context Check judges whether each figure is stated by its snippet, and the Statement Check (§5.4) judges each drafted sentence against its cited findings. Code keeps only "the quoted words are on the page" (snippet and evidence words) and mechanical rules. LLM calls are parallelised for latency: 5 items per call, at most 8 calls in flight. |
| D9 | Latency: researcher sub-topics run concurrently (at most 5 in flight); tool calls stay serialised run-wide under one lock (one body, one download); findings and events fold in plan order. Source-evaluator scoring batches run concurrently (at most 3). Every concurrency cap and token budget is a config value (§7.3) and can be lowered from live results without a code change. |

Honesty rules that stay binding:

- A relay is never presented as the organisation it relays.
- No provenance, scope or date that the page does not carry.
- Every number in the report is traceable to its cited page.
- Forecasts are reported with issuer and release. Actuals are labelled as actuals.
- No as-of cutoff is inferred from a year in the question; an explicit "as of" still freezes the date.
- Only 1-iteration live runs until the report is judged great.

## 3. Pipeline

```
Planner -> Researcher -> Source Evaluator -> Evidence Verifier
        (snippet on page, then Context Check) -> Report Writer (then
        Statement Check) -> Report Reviewer
        -> [missing required target and extra passes left: targeted Researcher
            pass -> Source Evaluator -> Evidence Verifier -> Report Writer ->
            Report Reviewer] -> Publish
```

| Stage | AI | Responsibility |
|---|---|---|
| Planner | yes | Produces targets for what the question asks, and nothing else required (section 7). |
| Researcher | yes, with tools | Searches and reads, the organisation's own page first. Emits findings with a verbatim snippet and structured figures (section 4). |
| Source Evaluator | yes | Scores credibility and identifies the owning organisation. Unchanged. |
| Evidence Verifier: snippet on page | no | Checks that the snippet is on the page (section 5.1). |
| Evidence Verifier: Context Check | yes, parallel batches | Confirms each figure is stated by its snippet and its year, scope, attribution and kind. The AI points to the evidence words and code confirms they are on the page (section 5.2). |
| Evidence Verifier: Statement Check | yes, parallel batches | Checks each drafted sentence against its cited findings, and keeps, corrects or refuses it (section 5.4). |
| Report Writer | yes, plus code guards | Writes from verified findings only, with the reader labels in section 6. |
| Report Reviewer | yes | The single quality judgement, plus the list of missing required targets (section 6). |
| Publish | no | Writes the report, the evidence log and the quality JSON. |

## 4. Finding

`Finding` (`utils/types.py`) keeps its existing admitted fields:
`attributed_issuer`, `attribution_quote`, `measure_scope`, `data_period`,
`release_date`, `vintage`, `statement_date` and `target_ids`. Their admission
rules are unchanged: a field is admitted only when the read's own text carries
it.

New fields:

- `snippet: str`: one or two sentences copied from the read, verbatim.
- `read_id: str` and `locator: str`: which stored read the snippet came from,
  and where in it. The researcher validates these today and then drops them;
  they are now persisted.
- `figures: list[FindingFigure]`, where `FindingFigure` is
  `{value: str, unit: str, period: str | None, kind: "actual" | "forecast" | None}`.
  `value` is the number as written in the snippet.

`content` stays free prose. Nothing downstream parses it for numbers,
organisations or years.

### Finding verification state

`verification: FindingVerification`:

- `status`: one of `verified`, `verified_corrected` or `dropped`.
- `figure_results`: one per figure. Each records `matched`, `context` (the
  confirmed or corrected period, scope, attribution and kind),
  `evidence_words`, and `reason` when dropped.
- `dropped_reason`, when the whole finding is dropped.

Only `verified` and `verified_corrected` findings can be cited. Dropped
findings, and every dropped figure, are listed in the evidence log with their
reason.

## 5. Evidence Verifier

### 5.1 Snippet on page (deterministic)

For each finding, `snippet` must be contained in the read's stored text after
cosmetic normalisation only: whitespace and line breaks, curly and straight
quotes, soft hyphens and line-break hyphenation, and case. If it is not, the
whole finding is dropped with `snippet_not_on_page`. This reuses
`excerpt_matches`. It is the only code check on a finding before the Context
Check (D8): whether each figure is stated by the snippet is judged by the AI
(§5.2).

### 5.2 Context Check (one batched AI call)

Input, per finding: the snippet, the read's surrounding passage (bounded, for
example ±1 paragraph), the finding's recorded fields and its figures.
Findings are batched, 5 per call, with at most 8 calls in flight, to keep
latency low (D8). There is no tool access and no web search.

Output, per figure, as a schema-validated reply:

- `period`: confirmed, or the corrected value;
- `scope`: confirmed, or the corrected value (for example "all segments");
- `attribution`: `own` (the page's publisher), `relayed` (with the originating
  organisation), or `unattributed`;
- `kind`: `actual` or `forecast`;
- `evidence_words`: the exact words on the page that state the figure with
  that period and scope;
- `verdict`: `confirm`, `correct` or `reject`, with a reason.

Code enforces the reply:

- `evidence_words` must be contained in the read text under the same cosmetic
  normalisation as 5.1. Otherwise the figure is dropped with
  `evidence_not_on_page`. This is the one check code makes on the AI's
  reply: the AI judges that the words state the figure, and code confirms
  that the words are on the page.
- A correction to period or scope is applied only when the corrected wording
  is itself contained in `evidence_words` or the passage. Otherwise the figure
  is dropped with `correction_not_on_page`. A correction is never guessed.
- `relayed` needs a named originating organisation that occurs in the
  passage. The existing `attribution_quote` admission cues apply ("according
  to", "reported by", a possessive, and similar).
- If the AI call fails after its retry ladder, the affected findings keep
  their snippet-on-page result. They are marked `context_unchecked` and cited
  only with an "unchecked context" label, and the run records the error. They
  are never silently promoted to verified.

A finding is `verified` when all its figures are confirmed, and
`verified_corrected` when at least one was corrected and none dropped. If only
some figures drop, the finding keeps the surviving ones and is
`verified_corrected`. If all of them drop, the finding is `dropped`.

### 5.3 Duplicates and revisions (deterministic)

Key: (organisation, measure family, period, kind, normalised value), where the
organisation is the attributed issuer when present and the read's owner
otherwise.

- **Same key:** one fact. The report cites the organisation's own page ahead
  of a relay; the other copies stay in the evidence log.
- **Same organisation, measure family, period and kind, different value:** a
  revision. The report states the latest release and notes the earlier one
  with its release. Example: 10.4 GW in the January 2025 inventory, released
  2025-03-12; 10.3 GW in the earlier edition.
- **Different organisations, same measure and period:** two figures, each
  attributed. Never called a conflict or a confirmation.

The measure family is derived from the unit dimension (power, energy or
percent) plus the target the finding answers. It is never parsed from prose.

### 5.4 Statement Check (AI, parallel batches)

After the Report Writer drafts, every sentence is checked by the AI against
the findings it cites. This replaces the code checks on sentence wording
(D8). It uses the Context Check's batching (5 sentences per call, at most 8
in flight), reasoning effort high, no tools and no web search.

Input, per sentence: its text, and for each cited finding its verified
figures (value, unit, period, kind, scope), organisation, attribution, reader
label and evidence words.

Output, per sentence, schema-validated:

- `verdict`: `consistent`, `corrected` or `inconsistent`;
- `corrected_text`, when corrected: the minimal rewording that makes the
  sentence agree with its findings (numbers, dates, scope, organisation,
  forecast or actual);
- `reason`.

Applied by code, without re-judging the wording:

- `consistent`: the sentence is kept;
- `corrected`: `corrected_text` replaces the sentence;
- `inconsistent`: the sentence is refused and published with its reason.

A failed batch keeps its sentences. They carry code-built labels with the
verified provenance, and the error is recorded. It never stops the run.

## 6. Report Writer and Report Reviewer

### 6.1 Report shape

1. Header: question, as-of, scope, counts.
2. Executive summary: a direct answer to each part of the question, first.
   For the benchmark: the 2024 actual; then each organisation's latest 2025
   forecast with its release; then 2025 actuals, labelled as actuals.
3. Key facts table: one row per deduplicated figure. Columns: organisation,
   measure, period, value, kind, scope, release or edition, source.
4. Findings sections: explanations such as segment basis, revisions and
   units.
5. Not found: each required target without a verified finding, and where it
   was searched.
6. Sources: numbered, and only sources the report cites.
7. Evidence log (a separate file): every finding with its snippet and
   verification result, and every dropped figure with its reason.

Reader labels, attached to each figure:

- who: *\<organisation\>'s own figure*, *relayed by \<site\> from
  \<organisation\>*, or *source does not attribute it*;
- kind: *actual*, or *forecast (\<release\>)*;
- edition: vintage or release date, where the finding carries one;
- *unchecked context*, only for `context_unchecked` findings.

The report uses no verdict, corroboration or "insufficient evidence" wording.

### 6.2 Report Writer checks (D7, D8)

The Report Writer cites findings by one label per finding, stamped by one
registry. The Context Check (§5.2) has already verified each figure's
organisation, kind, period and scope. Code attaches those as the reader label
on every figure, so the label carries the verified provenance whatever the
prose says.

The wording of each sentence (numbers, dates, scope, organisation, forecast
or actual) is checked by the Statement Check (§5.4), not by code patterns.
Code keeps only the mechanical rules:

- a sentence cites at least one known label;
- the length limit;
- a refused sentence is published in the evidence log and the quality JSON
  with its full text, cited labels and reason.

The writer's prompt asks it to state a forecast with a forecast verb, to use
only the numbers and dates of the cited findings, to use the scope words the
findings state (never the question's), and to name only organisations and
publications the cited findings name.

Removed with the fact checker: the two-label packet, the "left out" renderer,
sentinel table cells, and the verdict notes.

### 6.3 Report Reviewer

- One AI call, which merges today's critic and report review. It scores the
  existing seven dimensions and gives per-statement dispositions.
- It may still record a defect for a sentence whose wording contradicts its
  label; the Statement Check (§5.4) is the primary guard.
- Acceptance: mean ≥ 0.80, no material defect, and no gate failure (6.4).
- Output: `missing_required_target_ids`, used by 6.5.

### 6.4 Gates (deterministic)

Kept: citations resolve; every settled statement is cited; no duplicate fact
rows; as-of and scope present.

New:

- every kept sentence was judged by the Statement Check, or its batch failure
  is recorded;
- every required target is answered by a verified finding (6.6), or is listed
  under "Not found" with its search trail.

Removed: `broad_plan_coverage_below_0.80`, the claim-binding coverage
counters, and every verdict or pair gate.

### 6.5 Extra research passes

- Setting: `max_extra_passes` (default 1).
- Trigger: the Report Reviewer returns non-empty
  `missing_required_target_ids` and passes remain.
- Scope: the Researcher runs only for those targets, with the same budget
  rule. New findings go through the Source Evaluator and the Evidence
  Verifier. The report is rewritten and reviewed once.
- Otherwise the report is published, with the missing targets under "Not
  found".

### 6.6 Target answering (deterministic, from fields)

A verified finding answers target T when:

- T is among the finding's `target_ids`, named by the Researcher;
- the unit dimension of one of its verified figures fits T's measure (power
  for capacity, energy for MWh, percent for share);
- that figure's verified period matches T's period;
- that figure's verified kind matches T's kind (actual or forecast);
- T's organisation, when T names one, equals the figure's organisation.

No prose is parsed.

## 7. Planner and Researcher

### 7.1 Planner

- One target per (organisation, measure, period, kind) that the question asks
  for. Dimensions: measure, period, geography, organisation. The
  answer-form and evidence-period boilerplate is removed.
- `required` only for what the question names. Targets the planner adds itself
  (for example MWh for a capacity question, facility types or definitions) are
  optional, and so are paywalled-only issuers. Optional targets never fail a
  run.
- About five targets are expected for the benchmark: EIA 2024 actual; EIA,
  Wood Mackenzie and BNEF latest 2025 forecasts; optionally other published
  2025 outlooks.
- The existing temporal contract is kept: latest forecasts, no inferred
  cutoff, actuals separately labelled.
- `support_policy` and `independent_pair` are removed (D2).

### 7.2 Researcher

- Per-sub-topic tool budget rises from 10 to 20; the value is configurable.
- Sub-topics run concurrently (D9). Each loop has its own scratchpad and
  acquisition context; the tool section (policy decision, fetch, admission)
  runs under one run-wide lock.

### 7.3 Concurrency and budget telemetry (D9)

- Knobs in `config.yaml`, each overridable by an environment variable:
  `agents.sub_topic_concurrency` (default 5),
  `agents.source_scoring_concurrency` (3),
  `agents.verifier_batch_size` (5) and `agents.verifier_concurrency` (8). The
  existing token budgets (`llm.max_tokens` and the per-operation caps) stay
  where they are.
- Every run records, in the quality JSON and in one CLI summary line:
  - DeepSeek rate-limit errors (429s): the count, and how many a retry
    recovered;
  - the peak number of provider calls in flight;
  - per-stage calls, total seconds and the slowest call;
  - per-operation output tokens: the maximum used against the configured cap,
    and the count of output-limit (truncation) errors.
- The run summary advises, but never auto-tunes: "rate limits hit N times;
  consider lowering <knob>" when N > 0, and "output within X% of the <op>
  cap; consider raising it" when a call used 90% or more of its cap or was
  truncated.
- Prompt rule: read the organisation's own page (for example eia.gov or
  woodmac.com) before relays; use a relay only when the original is not
  reachable, and record it as a relay.
- Emits `snippet`, `read_id`, `locator`, `figures` and `target_ids` (section 4).
- A targeted extra pass receives only the missing target ids and their
  dimensions.

## 8. Removed

| Area | Removed |
|---|---|
| Agents | `agents/fact_checker.py`, `agents/claim_clusters.py`, `agents/critic.py` |
| Types | `Claim`, the claim verdict and evidence-status vocabulary, `ClaimCluster`, pair and support-policy fields, and the critique types. Shared types are kept only where another stage still reads them. |
| Report | The "Insufficient independent evidence" list, verdict notes, the "left out" renderer and sentinel cells |
| Quality | Claim-binding coverage, broad-plan coverage and pair or verdict gates |
| Graph | The fact_checker and critic nodes and their routes; the route after the Report Reviewer replaces the critic route |
| Evidence identity | Pair-only machinery in `agents/evidence.py`. The issuer identity and first-party host rules are kept; the rest is pruned only where nothing reads it. |
| Evaluation | Fact-checker and critic cases and gates in `evaluation/`. The e2e replay doubles for `ClaimsDraft`, `ClaimVerdictDraft`, `ClaimEquivalenceDraft` and `CritiqueDraft`. |
| API | Claim-derived coverage fields in `api/models.py` and `api/sessions.py`, re-derived from verified findings |

The e2e replay matrix (`e2e_evaluation/replay_matrix.py`) is reworked, not
dropped. Cases that exist only to test corroboration (`same-work-mirror`,
`semantic-duplicate-claims`, `late-contradiction`, independent-pair cases)
become Evidence Verifier cases, or are retired with a stated reason:

- relay labelled as relay;
- figure not on page dropped;
- AI evidence words not on page rejected;
- scope corrected from "grid-scale" to "all segments";
- revision noted;
- forecast versus actual kept apart.

Per-agent evaluation (`evaluation/`) gains `evidence_verifier` cases and
drops `fact_checker` and `critic`. `AGENT_NAMES` changes accordingly.

## 9. Delivery

The steps run in order. Each step's proof must pass before the next step
starts. Offline proofs use the retained states: `%TEMP%/audit2/final_state.json`
(findings and reads) and the audit-3 quality record
`output/report-e5f951f6a51f4094837dc4eccdb1c508-1-quality.json`, plus its
plan in `%TEMP%/audit3/runs.jsonl`.

| Step | Change | Proof |
|---|---|---|
| 1 | Finding fields (section 4); Researcher persists them; budget and own-page rule | Rebuilding findings from audit-2 reads keeps the 19.6 GW and STEO 14 GW findings with structured figures; Figure Match passes 25/25 |
| 2 | Evidence Verifier (section 5) | Unit tests for each normalisation case and each enforcement rule (invented `evidence_words` rejected, unsupported correction dropped); a replay labels audit-2's 18.9 GW as all-segment |
| 3 | Report Writer on findings; labels; Not found; duplicates and revisions (sections 5.3 and 6.1-6.2) | Offline composition from replayed findings: the summary carries the 2024 actual and at least two 2025 forecasts with organisation and release; no duplicate figure line; no verdict wording |
| 4 | Remove the fact checker, claim clusters and critic; Report Reviewer; gates; `max_extra_passes`; graph, API and evaluation cutover (sections 6.3-6.5 and 8) | Full test suite green; the reworked e2e replay matrix green |
| 5 | Planner (section 7.1) | The benchmark question plans five or fewer required targets, with no MWh, facility-type or definition requirements |
| 6 | One capped live pre-flight, then one live run (default one extra pass) | Success criteria in section 10, plus an independent audit |

Fingerprint pins (`tests/test_evaluation/test_config.py`) and
`agents/__init__.py` re-exports are updated where the agent set changes.

## 10. Success criteria for the live run

1. Wall time ≤ 45 minutes (target about 30).
2. The executive summary answers both halves:
   - EIA's 2024 figure (10.4 GW), with its release;
   - at least two organisations' latest 2025 forecasts, each with its
     release;
   - 2025 actuals, where present, labelled as actuals.
3. Every number in the report traces to a verified figure on its cited page,
   and no relay is presented as the originating organisation.
4. No figure carries a wrong scope, period or kind. In particular, an
   all-segment figure is never labelled grid-scale, and an actual is never
   presented as a forecast.
5. The Report Reviewer accepts, with no gate failure.
6. An independent audit rates the report GREAT.

## 11. Risks

- **Researcher target naming can be wrong.** Section 6.6 checks unit
  dimension, period, kind and organisation, and the smaller plan removes most
  of the targets that were mis-bound before.
- **Context Check misjudges meaning.** Its corrections must be carried by the
  page's own words, so a wrong "confirm" is the residual risk. Mitigations:
  per-figure `evidence_words`, and the audit criterion 4.
- **Size of the cutover.** Step 4 is the largest step, so it gets its own
  review and the full suite as proof. There are no half-states between
  steps.
- **Loss of the e2e safety net during rework.** Matrix cases are rewritten
  before the old ones are deleted, in the same step.

## 12. Out of scope

- Streamlit UI changes, beyond what compiles against the new types.
- Other benchmark questions; they are exercised by the e2e matrix only.
- Automatic adjustment of concurrency or budgets during a run (§7.3 only
  advises).
