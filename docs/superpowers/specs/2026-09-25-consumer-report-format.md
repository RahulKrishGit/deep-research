# Consumer report format: answer-first skeleton, question-shaped table, parallel writer

> **Superseded in part (2026-09-30)** by `docs/superpowers/specs/2026-09-30-notes-progress-report-stop-design.md` §7: the bottom line is a direct answer of at most two sentences, then one line per topic and one per reader note; the findings table becomes Key figures (What · Figure · Source, at most 10 rows, one per label) and prints after the topic sections, as `## Key figures` or `## Options compared`; and the console shows the report as one card per section with a contents list. Everything else below stands.

**Date** 2026-09-25 · **Plan tree** `.worktrees/evidence-verifier` (branch `codex/evidence-verifier-pipeline`, head `b7a7f8a`) · **Brief** `.superpowers/sdd/2026-09-24-evidence-verifier-pipeline/report-format-architect-brief.md`, plus four controller amendments: the parallel writer; the structural table choice; Fable's run-3 writer-side defects (D5, D6, D15, D16, D18, D19, D20); the new `quoted` finding status.

**Supersedes** the reader layout of `docs/superpowers/specs/2026-09-24-evidence-verifier-pipeline-design.md` §6.1 and its single-call writer (§6.2). **Unchanged:** the Evidence Verifier, the Statement Check prompt (VER-1..4), and the quality-gate definitions except §11.2.

Legend: [INFERENCE] = not observed. In the examples, (R) = a run's checked statement verbatim, (E) = one edited as noted, (I) = illustrative text written for this spec, not model output.

## 1. Problem and scope

Today's reader report (`agents/report.py:998-1067`) opens with pipeline counts and the plan's scope. It prints an 8–9-column "Key facts" table whose cells say "not stated", "(unchecked context)" and "(source does not attribute it)", ends each figure sentence with a code label ("house.gov's own figure; actual; …"), and closes with an internal "Not found" list ("N searches made, M pages read"). Live run 3 (session `2f74eaf9…`, pass 0) shows the cost: 39 Key-facts rows for about 14 facts (the Sony WH-1000XM6's $390 printed as "390.00 dollars", "390.00 USD" and "390.00 $"), a price filed under the measure "best audio quality" (K001), and a microphone half answered by nameless sentences (S011 "this is the model to beat", S012 "the buds'"). The user approved an answer-first skeleton with one question-shaped table. This spec fixes the shape, the data behind every consumer-visible element, how each element is verified, the writer that produces it (parallel, as the user asked), and the writer-side run-3 defects.

**Out of scope.** The content wave, running in parallel, owns passage selection, extraction, binding, fact-row dedup and unit folding, page dates, routing and reviewer severity (Fable D1–D4, D7–D14, D17, D21); §12 names the interfaces this design needs from it. Also out of scope: the planner's answer-kind classifier (§4.5, Q2), the Statement Check prompt, and `requested_word_limit`.

## 2. Decisions at a glance

|#|Decision|Choice|Tradeoff|
|---|---|---|---|
|1|Question-shaped table|Chosen by structure; `answer_kind` is only a framing hint. An **options table**, assembled by code from option marks on checked sentences, when the question's own parts name ≥2 options; else a **findings table** of verified figures when ≥2 qualify; else none. No model call writes table text.|Every cell word is verified wording and picks read at a glance; the writer's `picked` mark is structured metadata over a checked sentence and is not independently judged (§4.2, R1).|
|2|Bottom line|2–4 sentences from one short call made last, fed only the checked section statements; it may cite only findings those statements cite; Statement-Checked.|One serial call plus one check batch on the critical path (~30–45 s [INFERENCE]) buys a bottom line that cannot say more than the sections verified.|
|3|Sources line|`n. Publisher — [Title](url) (date)`; publisher = the page's own name (`page_owner`), else the host; date = the Source Evaluator's validated publication date only.|Many pages show a host (`cornell.edu`) and, until the content wave's page-date fix (D14, §12 I1), no date; never a wrong name or date.|
|4|What moves|Counts, scope, exact as-of and the full fact-row table go to the evidence log; table, sources, parts, marks and full review text go to the quality JSON.|The report alone no longer shows the plan's scope or verification counts; an auditor opens the log.|
|5|Fallbacks|Omit what is empty; state what is missing in plain sentences; a deterministic bottom-line fallback.|Degraded runs read thinner, never padded.|
|6|Parallel writer|One call per plan sub-topic ("part") with only its findings; each part's Statement Check starts when its draft returns; the bottom line last; a redraft re-asks only the parts a defect names.|More calls; faster when the single draft was long (run 2 pass 0: 186 s → ~125 s [INFERENCE]), ~12 s slower on short reports [INFERENCE]; one failing part no longer loses the report.|

## 3. The skeleton

Identical for every `answer_kind`:

```markdown
# {question}

{evidence line}

## Bottom line

{sentence 1} [a]. {sentence 2} [b][c]. …

{table}

*{caption}*

## {part title}

- {point} [n].

## What we couldn't confirm

{plain sentences}

## Sources

1. {Publisher} — [{Title}]({url}) ({date})

How this was researched: [evidence log]({report-…-evidence.md})
```

### 3.1 Line rules

1. **Title.** `composition.question` verbatim (the frozen question). Tradeoff: the user's own casing and typos print as typed; rewriting would put model text in the title.
2. **Evidence line.** `Evidence as of {YYYY-MM-DD} · {n} source(s)`: the UTC date of `composition.as_of` (`report_as_of`: newest `finding.extracted_at` / `read.retrieved_at`) and `n = len(written_citations(composition))`. An empty or unparseable `as_of` prints `No source could be checked.` No pipeline counts, no scope. Tradeoff: the brief says "run date"; the evidence date equals the run date for fresh reads and is older only when every read came from cache, and the project rule (`report_as_of` docstring) forbids presenting a clock read as the evidence date (Q3).
3. **Bottom line.** One paragraph. Each sentence's markers go before its final stop (`…overall [1].`), a deterministic move of trailing punctuation that leaves the verified words unchanged.
4. **Table.** §4. Omitted with its caption when none qualifies; no placeholder sentence (today's "No figure passed the Evidence Verifier." is cut).
5. **Part sections.** One per part with kept points, in plan order, titled by its writer (§6.3); bullets with markers before the final stop.
6. **What we couldn't confirm.** §10; omitted when empty.
7. **Sources.** §8. An ordered list, so Markdown renders one line per source and `runner._REFERENCE_LINE` (`^(\d+)\. (.*)$`) keeps canonicalising reports. Tradeoff against the brief's `[n] …` notation: consecutive `[1] A` lines merge into one paragraph in Markdown.
8. **Link line.** Last line: `How this was researched: [evidence log]({evidence_report_filename(...)})`. The three filename helpers move from `report_writer.py:292-328` to `report.py`, because the renderer links the artifact family it renders and `report_writer` imports `report`, not the reverse.
9. **Cut:** the header counts (`_header_counts`), the scope line, `## Executive summary`, `## Key facts` and its cell texts, the `## Not found` wording ("planned question", "checked finding", "N searches made, M pages read"), and the code label after each sentence (`_written_point`'s ` — *label*`). One exception: a kept sentence whose Statement Check verdict is `unchecked` (batch failure, D8) and that carries a fact row (`_carried_rows`) ends with `(figure: {who reported it (and when)})`, in the findings table's Who wording (§4.3), so the only sentences printed without an independent check keep a deterministic provenance line. Tradeoff: a rare visible inconsistency in degraded runs.

### 3.2 What `answer_kind` does

It never selects the table (§4.1). It is wired from `state.answer_contract.answer_kind` into the writer task and the composition (today it is never set: the `ReportComposition(...)` call at `report_writer.py:777-785` omits it), printed to both writer calls as `Answer form: {answer_form_requirement(kind)}` (framing only), recorded in the quality JSON, and already shown to the reviewer (`report_reviewer.py:916`).

|answer_kind|Framing line (`planner._ANSWER_FORM_REQUIREMENTS`)|Table the structural rule usually yields|
|---|---|---|
|comparison|the same dimension for every option, on one basis|options table when the parts name ≥2 options; else a findings table (e.g. two periods of one figure)|
|factual|the thing asked for, in the form its evidence takes|findings table; an options table for a which-is-best question (P1 is `factual`)|
|historical|the state of affairs in the named period|findings table (the period shows in "What was measured")|
|explanation|a causal mechanism with evidence for each step|usually none; a findings table when ≥2 figures qualify (Example 13.3)|
|constraints|the binding constraints with instrument and effective date|usually none (date-shaped figures are refused at admission)|

## 4. Decision 1 — the question-shaped table

### 4.1 Choice rule (computed after the bottom line is checked)

1. **Options table** when a single required part (a part whose sub-topic has ≥1 required target, so the question's own ask compares options) alone carries a mark (§4.2) on kept statements whose verdict is `consistent` or `corrected` for ≥2 distinct options; two different required parts that each mark only one option do not qualify — the ≥2 test is per required part, consistent with the column rule (§4.2).
2. Else a **findings table** when ≥2 fact rows are eligible (§4.3).
3. Else no table.

Tradeoff: structural and domain-free (D11); no keyword can force or suppress a table. A which-is-best report whose writer marks no options falls back to the findings table, which is still a valid report.

### 4.2 Options table

`report_table.options_table(...)` builds it from kept statements (bottom line and sections), their option marks (§6.4 rule 8), the parts (§6.1) and the page credits (§8).

- **Columns:** `Option`; then one column per part whose statements mark ≥2 distinct options, required parts first, then plan order, at most 4, header = the part's printed title; then `Recommended by` (always present).
- **Rows:** each distinct marked option (key = `cosmetic_text` of the name with whitespace and dash variants folded; label = its first spelling in report order) with ≥1 non-empty cell. Sort by (−distinct pages that pick it, −non-empty part cells, first mention in the report: bottom line, then sections). At most 8; beyond that the caption adds `Showing 8 of {N} options; the rest are in the sections below.`
- **A statement's parts:** a section statement's own part; a bottom-line statement's mark counts only toward the parts of the cited findings whose own page is that mark's `source_url` — never every part a multi-finding bottom-line statement happens to cite, so a source's verdict never shows under a criterion it did not judge.
- **Part cell:** the option's marks in that part's statements, grouped by page in report order: `{verdict}; {verdict} — {Publisher} [n]` (a mark without a verdict prints `{Publisher} [n]`); at most 2 pages per cell, further pages as bare markers; identical verdicts (cosmetic) printed once. Empty prints `—`.
- **Recommended by:** the distinct pages of marks with `picked`, in report order: `{Publisher} ({date}) [n]`, the date only when the page credit has one. Empty prints `—`.
- **Caption (code):** `Each cell quotes the report's own sentence about the option in that part; the whole sentence is in the section of the same name. "Recommended by" names the sources that pick the option. Options picked by more sources come first.`
- A mark counts only when its name and verdict are verbatim spans of the statement's **final** text (after any correction), its `source_url` is the page of one of the statement's cited findings (never a page the sentence does not rest on), and the statement's verdict is `consistent` or `corrected`, never `unchecked`; a mark failing the source-url check is dropped and recorded in `dropped_marks`.

Tradeoffs: every word in a cell is a span of a checked sentence, and publishers and dates are code-derived. Ordering by the number of picking sources shows consensus on a basis the sources give, and the caption says so, but it could be read as the report's own ranking. Because each finding belongs to one part (§6.1), an option's price stated inside an audio-part sentence stays in that sentence rather than in a Prices column.

**Acceptance fixture (Fable's run-3 paragraph, "which findings a question-shaped table would have had to show").** A unit fixture (tests are not model-read) with parts Sound quality (required), Microphone (required) and Prices (optional); page credits SoundGuys (2026-09-17), CNET (2026-08-06), What Hi-Fi? (2026-01-16) and Business Insider (2026-05-29); consistent statements marking these options and verdicts:

- Sony WH-1000XM6: SoundGuys "best wireless headphones" (picked) and "sound score of 4.8 out of 5"; CNET "a score of 9.3"; SoundGuys "good mic quality" and "clear voice capture"; CNET "excellent voice-calling performance with more mics"; What Hi-Fi? "the best over-ear headphones with a mic" (picked); SoundGuys "$390 at Amazon".
- Sony 1000X The Collexion: CNET "a 9.2 score" (picked), "excellent voice-calling", "$599".
- Apple AirPods Max 2: SoundGuys "sound score of 4.3 out of 5" and "best noise canceling headphones" (picked); CNET "a score of 9.1" and "slightly improved calls"; SoundGuys "$479 at Amazon".
- JBL Tour ONE M3: SoundGuys "sound score of 3.9 out of 5", "performs well for calls", "$449.95 at Amazon".
- Bose QuietComfort Ultra Headphones (2nd Gen): CNET "a 9.2 score" and "excellent voice-calling"; SoundGuys "$399.99 at Amazon" (picked); Business Insider "$369" (picked).
- Anker Soundcore Liberty 5 Pro: CNET "top voice-calling performance" (picked), "$170".
- Sony WF-1000XM5: What Hi-Fi? "the best headphones with a mic for most people" (picked).
- Apple AirPods Pro 3: What Hi-Fi? "great call quality", "$199 at Amazon".

Expected: columns `Option | Sound quality | Microphone | Prices | Recommended by`; rows in the order Sony WH-1000XM6, Bose QuietComfort Ultra Headphones (2nd Gen), Sony 1000X The Collexion, Apple AirPods Max 2, Anker Soundcore Liberty 5 Pro, Sony WF-1000XM5, JBL Tour ONE M3, Apple AirPods Pro 3; the XM6's Microphone cell holds the SoundGuys and CNET entries plus What Hi-Fi?'s bare marker; the XM6's Recommended by is `SoundGuys (2026-09-17) [..]; What Hi-Fi? (2026-01-16) [..]`; adding a ninth option triggers the cap caption.

### 4.3 Findings table

`report_table.findings_table(...)`; columns `What was measured | Result | Who reported it (and when) | Source`.

- **Eligible rows:** fact rows with `context_unchecked == False` that answer a required target, or whose finding (or a duplicate) is cited by a kept statement whose verdict is `consistent` or `corrected`. At most 12; selection priority: answers a required target, then cited by the bottom line, then the rest. Display order: plan order of the part owning the row's finding, then row id. When capped, the caption reads `Showing 12 of {N} verified figures; all are in the evidence log.`
- **What was measured:** the verified subject (first letter capitalised), plus ` ({scope})` when the scope's words are not all in the subject, plus `, {period}` when the period's words are not already present; a resolved relative period prints `, {period} (counted from the page's date, {period_resolved_from})`. The **quoted form** — the figure's Context Check `evidence_words` in quotation marks, clamped to 140 characters at word boundaries around the value with `…` at each cut — replaces it when the subject is empty, when it starts with a pronoun or possessive (`it`, `its`, `this`, `these`, `their`, `they`), or when the row has a **rival** in the table: same kind, matching periods (`same_period`, or both empty), subjects that can name one thing (`verified_facts.same_subject`, where empty is compatible) and a different value.
- **Result:** the value as the page writes it. If every row is actual, the caption reads `No figure in this table is a forecast.`; if every row is a forecast, `Every figure in this table is a forecast.`; if mixed, each Result ends `, actual` or `, forecast`. Earlier editions (`row.earlier`) append `; earlier: {value} ({date})`, `{date}` the *earlier finding's own* `release_date`, else its `statement_date` (looked up by `edition.finding_id`) — never `EarlierEdition.release`, which starts with the unverified vintage.
- **Who reported it (and when):** own → `{organisation}`; relayed → `{organisation}, reported by {relay publisher}`; unattributed → `{page publisher}`. `{organisation}` is `row.organisation` unless it is empty or equals the page's host identity, in which case the page credit's publisher prints; `{relay publisher}` is the relay page's credit (fallback `row.relay_host`). The "when" is ` (released {finding.release_date})`, else ` (stated {finding.statement_date})` (both admitted against the read at `researcher.py:1408,1413`), else, for a forecast, ` (no release date given)`. Never the vintage: `researcher.py:1409` copies it from the draft unchecked (§12 I6).
- **Source:** the markers of `_row_finding_ids(row)`.

Tradeoffs: "What was measured" prints only page-verified fields, so a measure's meaning (added vs cumulative) shows only where two numbers could be confused, through the page's own words. Unchecked-context rows leave the table (they stay citable in prose and whole in the log). Edition names are lost until vintage is admitted against the read.

### 4.4 Rejected alternatives

- **(a) A deterministic subject × measure pivot of fact rows, plus text findings bound to per-item targets.** It fails on real data. `FactRow.measure` is the bound target's measure and a finding's binding covers all its figures, so run 3 files the XM6's $390 under "best audio quality" (K001) and lets prices "answer" "sound quality score" (K015, K019). For unscaled dimensions `_figure_answers` accepts any unit the parser does not know (`verified_facts.py:881-910`). Subjects fragment ("Sony WH-1000XM6 sound/ANC/MSRP/per charge"). Microphone verdicts are text with no structured subject, so no Microphone column could exist: the half of P1 that Fable rated material.
- **(b) Writer-drafted table rows, each cell a Statement Check item.** It contradicts the user's "no writer call" for the table, duplicates the sections, roughly triples the Statement Check items, and a checker correction cannot be mapped back into a cell.
- **Chosen (c): code assembly from verbatim spans of checked sentences.** What would change my mind: if audits find mis-marked picks in more than 1 of 10 live reports, move the pick into the Statement Check (a VER change) or drop the Recommended-by column.

### 4.5 The classifier

Do not widen `_COMPARISON_MARKERS` with superlatives ("best", "top", "most", "fastest", "which … for"): they are English keywords with a high false-positive risk ("most recent", "top-level", "best practice"), and the table no longer depends on them. Adding "obligation(s)" to the constraints markers is a narrow change that would give P4 the constraints framing, but it changes the planner's contract line (pin and probe), so it is the planner owner's call (Q2). Tradeoff: P1 and P4 keep the factual framing ("items with their attributes…", "a rule's provisions"), which already covers them.

## 5. Field-by-field data mapping

|Consumer element|Source field(s)|When absent|
|---|---|---|
|Title|`composition.question`|—|
|Evidence date|UTC date of `composition.as_of`|the line becomes `No source could be checked.`|
|Source count|`len(written_citations(composition))`|0|
|Bottom line|`composition.summary` (`ReportPoint`s)|§10|
|Citation numbers|`written_citations` order: bottom line → table → sections|—|
|Table|`composition.table` (`ReportTable`)|omitted|
|Part title and points|`ReportSection.title` / `.points` (new `coverage_id`)|part omitted|
|What we couldn't confirm|`composition.not_found` (`question`, `searched`); `composition.parts` with status `failed`; `composition.unreachable`|section omitted|
|Sources: publisher|`composition.page_credits[url].publisher` = `evidence_verifier.page_owner(read)` at compose time|`publisher_identity(url)`|
|Sources: title|`Citation.title` minus a first or last `wording.title_segments` segment naming the publisher (`same_organisation` or cosmetic equality)|the full title|
|Sources: date|`page_credits[url].date` = `ScoredSource.temporal.publication_date` (a validated `TemporalClaim`), else the `ReadRecord` page date (§12 I1)|omitted|
|Options cells|`ReportStatement.items` (`ItemMark`) and page credits|`—`|
|Findings: what, result|`FactRow.subject/scope/period/period_resolved_from/value/kind/earlier`; `FigureResult.evidence_words` of the row's kept figure|quoted form|
|Findings: who, when|`FactRow.attribution/organisation/relay_host`; `Finding.release_date/statement_date`|see §4.3|
|Link line|`evidence_report_filename(session_id, iteration)`|—|

New and changed types in `utils/types.py`:

```python
class ItemMark(ContractModel):
    name: str                  # verbatim span of the statement text, <= 80 chars
    verdict: str = ""          # verbatim span, <= 80 chars
    picked: bool = False       # the sentence reports that this page picks or recommends the option
    source_url: str            # the page this mark rests on (resolved from the draft's `by`)

class ReportStatement(ContractModel):   # existing, plus:
    items: list[ItemMark] = Field(default_factory=list)

class ReportSection(ContractModel):     # existing, plus:
    coverage_id: str = ""

class ReportPart(ContractModel):
    coverage_id: str
    sub_topic_title: str
    finding_ids: list[str] = Field(default_factory=list)          # the partition (§6.1)
    context_finding_ids: list[str] = Field(default_factory=list)  # D16 (§6.2)
    status: Literal["written", "carried_over", "failed", "empty"]

class PageCredit(ContractModel):
    publisher: str
    date: str | None = None

class UnreachablePage(ContractModel):
    url: str
    title: str = ""
    reason: str = ""           # empty until §12 I2 lands

class TableEntry(ContractModel):
    text: str = ""             # verbatim spans joined "; " in options cells; "" for Recommended by
    source_url: str
    date: str | None = None    # Recommended by only

class TableCell(ContractModel):
    text: str = ""             # findings-table text; "" with no entries renders "—"
    entries: list[TableEntry] = Field(default_factory=list)
    statement_ids: list[str] = Field(default_factory=list)
    finding_ids: list[str] = Field(default_factory=list)
    row_ids: list[str] = Field(default_factory=list)

class ReportTable(ContractModel):
    shape: Literal["options", "findings"]
    columns: list[str]
    rows: list[list[TableCell]]   # validator: len(row) == len(columns)
    caption: str = ""

class ReportComposition(ContractModel):   # additions; `summary` stays the Bottom line's field
    parts: list[ReportPart] = Field(default_factory=list)
    table: ReportTable | None = None
    page_credits: dict[str, PageCredit] = Field(default_factory=dict)  # normalized URL -> credit
    unreachable: list[UnreachablePage] = Field(default_factory=list)
    dropped_marks: list[str] = Field(default_factory=list)            # "S004: 'Model A' is not in the sentence"
```

`summary` keeps its name. Tradeoff: renaming it to `bottom_line` would break persisted compositions under `extra="forbid"` for no reader-visible gain. `FindingStatus` `quoted`, `ReportQualitySnapshot.quoted_findings` and the evidence-log label `quoted (snippet found on the page; not checked for context)` already landed with the content wave (D21, FixVerifierR3); this design keeps that label and treats `quoted` as citable (every status except `dropped`, `verified_facts.py:94`).

## 6. Decisions 2 and 6 — the parallel writer

### 6.1 Parts and the partition

A **part** is a plan sub-topic (`state.sub_topics`, in priority order). `report_parts(task)` places every citable finding in exactly one part: the part of the first target, in plan order, among

1. required targets it answers and binds (`finding.target_ids`);
2. required targets it answers otherwise;
3. optional targets it answers and binds;
4. optional targets it answers otherwise;
5. targets it binds without answering;
6. the sub-topic its `related_sub_topic` names (the resolution `_names_the_targets_sub_topic` uses).

Otherwise the finding is unplaced (recorded in the evidence log, not written). A part with no findings is `empty` and makes no call.

Tradeoff: nothing is written twice and every call is small, but a finding relevant to two parts is written only in the first (run 3's "good mic quality … model to beat", bound to t02-1 and t03-3, lands in Microphone); the bottom line connects parts. Steps 2 and 4 disappear when the content wave's explicit-only binding lands (§12 I4); the rule is otherwise unchanged.

### 6.2 Registry lines (D5) and context-only findings (D16)

`registry_lines` (`report_writer.py:358-397`) prints, per finding: the header; `content: {finding.content}` (the researcher's wording, which names the referent a snippet leaves as a pronoun: run 3's F10 content says "…the Sony WH-1000XM6 is the model to beat"); `snippet:`; for a finding with no kept figure, `passage: {statement_passages[id]}` (the same bounded `context_passage` the Statement Check reads, so the writer can name only what the checker can verify); then the figure or statement lines as today. Passages are computed at task build time for the whole registry (today only for cited findings, at check time). When §12 I3 lands, a `subject:` line carries the finding's admitted subject.

A finding is **context-only** when it binds no target (`finding.target_ids == []`) and its source's `relevance_score < CONTEXT_ONLY_RELEVANCE = 0.5` or the source is `low_confidence` (run 3's Shure pages: unbound, relevance 0.45). Context-only findings are listed in their part's request under `# Context only`, and a point resting only on them is refused. Tradeoff: a heuristic threshold; bound findings are never context-only.

### 6.3 Section call

Messages are static-first, so every part shares a cacheable prefix: developer = the section system prompt (WRI-1'); user = `render_structured_request([rules (WRI-2'), reply format (WRI-3')], material)`, where material is `# Question`; `# Answer form`; `# This part of the question` (the sub-topic title, then each target as `- {id}: {question} ({required|optional}; answered by F02, F07 | no listed finding answers it)`); on a redraft, `# Your previous section` and `# Defects to fix`; `# Verified findings for this part` (registry lines of its placed findings, with the global labels F01… from `finding_registry`); and `# Context only`.

Reply schemas in `report_writer.py` (`ReportWriterDraft` and `WriterSectionDraft` are deleted):

```python
class ItemMarkDraft(ContractModel):
    name: str
    verdict: str = ""
    picked: bool = False
    by: str = ""                      # a finding label; required when the point cites pages of more than one site

class WriterPointDraft(ContractModel):
    text: str
    finding_labels: list[str] = Field(default_factory=list)
    items: list[ItemMarkDraft] = Field(default_factory=list)

class SectionDraft(ContractModel):
    title: str
    points: list[WriterPointDraft] = Field(default_factory=list)

class BottomLineDraft(ContractModel):
    sentences: list[WriterPointDraft] = Field(default_factory=list)
```

### 6.4 Mechanical rules (code; the Statement Check judges everything else)

1. At least one known label (today's rule).
2. Every label belongs to this part; else refused `cites a finding outside this part`.
3. Not only context-only labels; else refused `rests only on context-only findings`.
4. At most `MAX_POINT_CHARS`, with today's sentence-boundary split.
5. **Unnamed subject (D6):** refused `a judgement with no named subject` when the text, or a quotation inside it, opens with `it|this|that|these|those|they|he|she` followed by `is|are|was|were|has|have|had|remains|offers|delivers|makes|sounds`, and the point keeps no option mark. Tradeoff: an English closed list aimed at the observed failure (S011's quoted "this is the model to beat"); unquoted descriptions ("the buds'") are left to the prompt rule and the reviewer.
6. At most 10 kept points per section (`over the section's ten points`) and 4 bottom-line sentences (`over the bottom line's four sentences`). **Superseded by review** (controller ruling 2026-09-25, "no strong limits"): the shipped writer drops the per-section points cap outright; only `MAX_POINT_CHARS` (per point) and the 4-sentence bottom-line cap remain.
7. A section title of at most 80 characters; an empty title falls back to the sub-topic title.
8. **Marks:** each `name` and `verdict` must be a verbatim span (cosmetic, case- and whitespace-insensitive) of the point's final text after any correction, at most 80 characters; `by` must be one of the point's labels when the point cites more than one site; the mark's `source_url` is `by`'s page, else the point's only page. A failing mark is dropped (the point stays) and recorded in `dropped_marks`.
9. Restatement dedup (`_carried_rows`) within each section and within the bottom line.

### 6.5 Pipelined Statement Check

Each part task runs draft → rules → `check_statements(items, gate=pass_gate, …)` at once → verdicts applied (today's `finalize`, then rule 8 on the final text). One `asyncio.Semaphore(agents.verifier_concurrency)` per writer pass is shared by every batch, through a new keyword `check_statements(..., gate: asyncio.Semaphore | None = None)` (`evidence_verifier.py:1350-1392`; `None` keeps today's private semaphore). The Statement Check prompt is unchanged.

### 6.6 Bottom-line call

It starts when every part task has finished. Messages: developer = WRI-4; user static = rules (WRI-5) and reply format (WRI-6); material = `# Question`, `# Answer form`, and `# Checked statements` grouped under each part's title, with every kept `consistent` or `corrected` statement as `- {text} (cites F02, F07; options: Sony WH-1000XM6 [picked])`; on a redraft also `# Your previous bottom line` and `# Defects to fix`. The reply is a `BottomLineDraft`. Rules 1, 4–6, 8 and 9 apply, plus: every label must be cited by a kept checked section statement, else refused `cites a finding no checked section statement cites`. One Statement Check batch through the same gate.

### 6.7 Assembly

- Flight keys are `P{part:02d}.{n:02d}` and `B{n:02d}`. Once all checks finish, statement ids are renumbered `S001…` in render order (bottom line, then sections in plan order) and `statement_verdicts` is remapped; `RejectedDraftPoint.where` is `bottom_line[n]` or `section[{coverage_id}].points[n]`. The output is identical whatever order the calls complete in.
- `page_credits` for every URL a kept statement or a table row cites: `PageCredit(page_owner(read) or publisher_identity(url), evaluated_page_date(sources, read) or the read's page date (§12 I1))`.
- `unreachable`: for each sub-topic with ≥1 required target, `acquisition_state_by_target[coverage_id].denied_urls` with the candidate record's title and (§12 I2) reason, deduplicated, in plan order.
- `answer_kind` from the contract; then `report_table.build_table(composition)` sets `composition.table`.
- `system_prompt()` returns the section prompt; each call is fingerprinted with its own schema name.

### 6.8 Failure handling

|Event|Effect|
|---|---|
|A part's draft hits `ProviderOutputLimitError`|re-asked once at `OUTPUT_LIMIT_RETRY_EFFORT` (today's F10 rule, per part); a second truncation fails the part|
|A part's draft raises `ProviderError`, `StructuredOutputError` or `ValidationError`|the part is `failed`; recoverable `report_writer_section_failed` (details: coverage_id, attempt, exception_type); the other parts stand; What we couldn't confirm names it|
|`ProviderConfigurationError` anywhere|propagates; the `asyncio.TaskGroup` cancels the other parts and the run halts, as today|
|A part's check batch fails|D8 as today: sentences kept `unchecked`, error recorded; `unchecked` statements feed neither the bottom line nor the table|
|Every non-empty part failed|no bottom-line call; today's non-recoverable `report_writer_provider_error`; `stop_reason="provider_error"`|
|The bottom-line draft fails or truncates twice|fallback: move up to 4 kept checked section points into the bottom line (the first of each part with a required target, then the first of the other parts); recoverable `report_writer_bottom_line_failed`|
|The bottom-line check fails|D8: kept `unchecked`, recorded|
|No citable finding|no call, as today; §10|

### 6.9 Redraft (D18)

When `task.defects` is non-empty (same evidence, the reviewer's material defects), route each defect by its statement ids (the parts, or the bottom line, that held them in `state.composition`) and its target ids (the parts owning the targets); a defect with no ids goes to every part and the bottom line. A part with no routed defect is **carried over** from `state.composition`: same points, verdicts and marks, no call (`status="carried_over"`). A part with routed defects is re-asked with its previous section and the defects, under the rule "return the previous section with only the edits these defects need; keep every other point word for word". The bottom line is always re-asked last, with its previous text. Tradeoff: carried-over points are not re-checked (same text, same evidence); this is what stops run 3's redraft from dropping 12 unrelated statements, and it saves calls.

### 6.10 Concurrency and config

- New `agents.writer_section_concurrency: int = Field(default=7, ge=1)` beside `verifier_concurrency` in `utils/config.py`, env `AGENTS_WRITER_SECTION_CONCURRENCY`, a `config.yaml` entry, `tests/test_config.py`; and `_CONCURRENCY_KNOBS["report_writer"]` in `observability/run_telemetry.py`.
- At most `writer_section_concurrency` drafts in flight; at most `verifier_concurrency` checks across the whole pass (the shared gate).
- Tradeoff: the default 7 equals `max_sub_topics`, so every part starts at once; the worst case is 7 drafts and 8 checks in flight (the researcher already runs 7 concurrent calls with 0 rate limits recorded). Lower the knob if rate limits appear (Q6). **Superseded by review**: a controller override for this build raised the shipped default to 10 (`max_sub_topics`'s own shipped value), not 7.

### 6.11 Prompt contract (directions, not final wording; Fable reviews before merge)

**WRI-1 → the section system prompt.** One section of a research report: the part of the question named in the request, from the verified findings listed for that part only. Replace "Code builds the key facts table, the Not found list and the sources; you write the executive summary and a few short explanatory sections" with: code builds the table, the list of what could not be confirmed and the sources, and another request writes the bottom line from the checked sections. Keep the label, snippet and host paragraph (the D1 wording). Add (D5): each finding also shows the researcher's `content`, used only to identify what the snippet is about, and a finding with no figure shows the page `passage` around its snippet; take a judgement's subject from them.

**WRI-2 → the section rules.** Keep: cite by label; forecast verbs; numbers, dates and qualifiers; scope words; only the complete part of a snippet; only organisations the findings name; a figure stays with its subject; the credit rules (own, "according to X, as reported by Y", as the page names itself); never a label's own words; housekeeping; verdict words; one fact per point; the length rule; never the same figure twice. Change: the executive-summary rules become "state every answer the listed findings carry for this part's targets, required targets first in the listed order, each in the form its evidence takes; every required target a listed finding answers is stated by a point citing that finding"; keep "a point never announces an absence". The title rule becomes: the part of the question this section answers, in the question's words where it has them, at most eight words, never a judgement or a status. Delete "at most four sections" and "every section adds something the executive summary does not carry". Add D6: every judgement, pick or verdict names the item it is about, as the content or passage names it; never a pronoun or an unnamed "the model" or "the buds'"; such a point is refused. Add D15: the criterion rule covers a pick made on value or for the price ("an edge to the older model in overall value") and the newer or alternative model the page names beside the pick: state them with it or leave the judgement out. Add D16: a point never rests only on findings under `# Context only`. Add the marks: `items` for each option the point is about (a product, place, service or other thing the question asks to choose among or compare), `name` exactly as the text writes it and the same way every time, `verdict` the shortest span of the text giving that source's verdict, score or price for it, `picked` only when the text reports that the source recommends, picks or ranks it first, and `by` when the point cites more than one site; empty for a point about no option. Add D18: when the previous section is shown, return it with only the edits the defects need.

**WRI-3 → the section reply examples.** Two neutral examples (D10: no benchmark or probe terms): a figure finding giving one point with an option mark (for instance "Example Tester gives Model A a noise rating of 4.5 out of 5"); and a no-figure finding whose snippet says "it is the one to beat for the price" while its passage names Model B, so the point names Model B and keeps "for the price".

**New WRI-4 (the bottom-line system prompt).** Write the bottom line: two to four sentences answering the question directly, from the checked statements listed; each was checked against the findings it cites; state nothing they do not.

**New WRI-5 (the bottom-line rules).** The most direct answer first, then the question's parts in order; cite only labels the listed statements cite; credit every judgement, pick or ranking to its source as the statement does; for a question asking which option is best, say which option each source picks and by what criterion, giving each where the sources differ, and never a pick, ranking or criterion of your own; a forecast keeps its issuer, its release and a forecast verb; keep qualifiers and criteria; never state a source's date (the report prints it), never announce an absence, never list every option (the table does), never copy a statement word for word; marks as for sections; on a redraft, minimal edits.

**New WRI-6 (the bottom-line reply example).** One neutral example.

### 6.12 Wall time [INFERENCE until measured]

Measured (LangSmith): run 2's writer, pass 0, took 186 s (one draft of 102.6 s, then parallel checks of 29.6, 24.0 and 83.8 s); pass 1 took 97.7 s (56.5 s; 25.2, 16.1 and 41.1 s); run 1's draft took 15–26 s with checks up to 33 s; the reviewer takes 134–163 s per pass and the planner 326 s. The new stage takes about max over parts (draft + checks) + the bottom-line draft + its check. Assuming a part's draft time follows its share of the output (run 3's audio part holds 10 of 16 statements, so ~0.5), one check round of 25–40 s, a bottom-line draft of 10–20 s and one check batch of 15–30 s: run 2 pass 0 ≈ 51 + 35 + 15 + 25 ≈ 126 s (−32 %); pass 1 ≈ 93 s (about the same); run 1 ≈ 67 s (about +12 s). The writer is 10–15 % of a run, so the run-level effect is under a minute; redrafts gain more, because carried-over parts make no call. Measure on the next live run: the report_writer node's span per pass in LangSmith (`StageTelemetry.seconds` sums call durations and overstates a parallel stage), per-part draft and check spans, and prefix-cache hits from the provider's recorded `prompt_cache_hit_tokens` (simultaneous first calls probably miss; DeepSeek's cache is best-effort).


### 6.13 Statement target ids and fallback answers (Fable D9)

Today a statement's `target_ids` come from `task.answered` (`report_writer.py:751`, the `ReportStatement(...)` in `finalize`), i.e. `answered_target_ids(..., sub_topics=...)`, which includes **fallback answers**: a finding with no explicit binding answering a target only because it names the target's sub-topic.

Decision:

- **Statement `target_ids` = explicit bindings only:** the targets a cited finding answers *and* lists in its own `finding.target_ids`. The same rule applies to bottom-line statements.
- **Coverage is unchanged:** fallback answers still count toward `answered_target_ids`, the Not-found computation and the quality gates. `quality.compute_report_quality` already requires a fallback answer to reach the reader through a kept statement citing its finding (it tests finding ids, not statement target ids), so the gate keeps working.
- **Section writer:** a fallback answer is listed on its target's line as `answered by F07 (through its sub-topic only)`, so the writer states it and the gate is satisfied; the partition places the finding by §6.1 step 2 or 4.
- **Context-only answers:** a required target whose only answers are context-only findings (§6.2) counts as unanswered *for the report*. `build_task` computes the report's Not-found list from answers that exclude context-only findings, so the target appears under What we couldn't confirm, while the gate's own `answered` set still includes it and the listing keeps it accounted for. Run 3's topic-03-target-01, "answered" only by the Shure page, reads as unconfirmed rather than answered.
- **Findings table:** "answers a required target" in §4.3 means an explicit binding; a row whose finding answers only by fallback is eligible only when a kept checked statement cites it.
- **Options table and bottom line:** unaffected by target ids. A fallback answer reaches the bottom line only through a checked section statement that cites it (the label-subset rule).
- **Redraft and reviewer routing** (§6.9, §11.1) use the explicit target ids. A defect naming a target still reaches the part that owns that target through the plan.

Tradeoff: a statement that states a fallback answer carries no target id for it, so the reviewer cannot scope a defect to that target through that statement (it can still name the statement or the whole report). In exchange, no statement claims to answer a target its finding never named. When the content wave's explicit-only answering lands (§12 I4), fallback answers disappear and this subsection reduces to its first bullet.

T4 adds RED tests: statement target ids exclude fallback-only targets; a fallback-only answer is still listed to the writer and still satisfies the gate; a required target answered only by context-only findings is listed under What we couldn't confirm; a fallback-only fact row enters the findings table only when a kept statement cites it.

## 7. Verification coverage of every consumer-visible claim

|Element|Checkable against|Checked by|Residual risk|
|---|---|---|---|
|Title|the frozen question|—|—|
|Evidence line|recorded timestamps; cited pages|code|—|
|Bottom-line sentence|the cited findings' snippets, passages and figures; every cited finding is also cited by a checked section statement|Statement Check + the label-subset rule|`unchecked` on a batch failure (D8; the `unjudged_sentences` gate; the provenance suffix of §3.1)|
|Section point|its part's cited findings|Statement Check + §6.4|the same; a sentence resting on a `quoted` finding is checked for wording, but the finding's relevance was never judged (D21) — D16 and the content wave reduce it|
|Options cell text|a verbatim span of a consistent or corrected sentence, re-validated after correction|Statement Check through the sentence; code containment|the span choice and `picked` are the writer's (R1)|
|Options cell source and date|the mark's page; its page credit|code|—|
|Findings: what, result|Context Check fields; Figure Match; verbatim evidence words|Evidence Verifier|a measure's meaning is not a verified field (the rival rule)|
|Findings: who, when|Context Check attribution and organisation; release and statement dates admitted against the read|Evidence Verifier + admission|no edition (vintage is unverified)|
|What we couldn't confirm|plan questions, acquisition records, part statuses|code|statements about the run, not the world|
|Sources|cited URLs; the page title; `page_owner`; the validated publication date|code + Source Evaluator|a host shows where the page does not name itself|

Every printed statement and table cell is recorded in the quality JSON with its finding, statement and fact-row ids (§9), so a replay can check each one.

## 8. Decision 3 — the Sources line

`n. {Publisher} — [{Title}]({url}) ({date})`, numbered in first-use order (bottom line → table → sections).

- **Publisher:** `page_owner(read)` computed at compose time (the page's own title, opening or copyright naming an organisation that `same_organisation` confirms against the host; else the registrable host) and stored in `page_credits`, because the renderer is pure and has no reads.
- **Title:** the page's title without a leading or trailing `title_segments` segment that names the publisher; unchanged when nothing would remain.
- **Date:** `ScoredSource.temporal.publication_date` (the Source Evaluator's `TemporalClaim`, validated against the read), else the `ReadRecord` page date once §12 I1 lands; otherwise omitted. Never `statement_date` (a figure's date, not the page's) and never the vintage.
- The URL is the Markdown link target, not printed text. `render_citations` is replaced.

Tradeoffs: `page_owner` falls back to the host for pages that do not name themselves (LII shows `cornell.edu`); in run 3 every `publication_date` was null (Fable D14), so no dates print until I1; a wrong date is worse than none.

## 9. Decision 4 — the evidence log and the quality JSON

**Evidence log** (`render_finding_log`):

- A new `## About this report` block after the session line: evidence as of (to the minute, `_reader_as_of`) and printed on (`generated_on`); the scope (`report_scope`, moved from the report header; `composition.scope` stays set for the `missing_scope` gate); the question form (`answer_kind` and its requirement line); the findings counts (moved from `_header_counts`): `{v} verified, {c} verified with corrections, {q} quoted (snippet found on the page; not checked for context), {d} dropped; {u} with unchecked context; {k} required questions unanswered`; the parts (coverage id, sub-topic title, printed title, status, finding count, context-only count); the table's shape and caption.
- `## Verified figures`: today's Key-facts table unchanged (Organisation | Subject | Measure | Period | Value | Kind | Scope | Release or edition), with finding labels in the Source column — the audit view, including "not stated" and unchecked context.
- Per-finding status labels as today, including the landed `quoted (snippet found on the page; not checked for context)`, each plus `; context unchecked` when flagged.
- `## Not found` unchanged (queries, pages read); new `## Pages that could not be opened` (all of them, uncapped); `## Refused sentences` with the new reasons; new `## Dropped option marks` and `## Unplaced findings`.

**Quality JSON** (`render_quality_record`): add `answer_kind`; `table` (shape, columns, caption, rows of cells with text, entries, `statement_ids`, `finding_ids`, `row_ids`); `sources` (number, url, publisher, title as printed, date); `parts`; per statement, `part` (`bottom_line` or a coverage id) and `items`; `unreachable`; `dropped_marks`. D19: store `review.defects[*].problem` whole — `_review_record` (`report.py:430-464`) clamps it through `_display_clamp` (`report.py:262`); the evidence log may still clamp for display. Tradeoff: a larger JSON, and every consumer-visible cell becomes replayable.

## 10. Decision 5 — fallbacks

- **No verified figure:** no findings table; an options table may still appear (it needs marks, not figures).
- **Nothing answered (no citable finding):** the evidence line (`… · 0 sources` or `No source could be checked.`); `## Bottom line` reading `No source we could check answers this question.`; `## What we couldn't confirm`; no table, no part sections, no `## Sources`; the link line. **Superseded by review** (P1-3): the shipped condition is narrower than "no citable finding" alone — this sentence prints only when the pass cites nothing *and* holds no citable finding at all; a pass that holds a finding or prints a table/section but wrote no bottom line gets the "A summary could not be written this time" sentence instead, never the false "no source answers this."
- **Partially answered comparison:** option rows show `—` where a part has no mark; the bottom line reports the picks that exist and never announces a gap; the gap appears under What we couldn't confirm when it is a required target with no answering finding.
- **Every part failed:** `## Bottom line` reads `This report's sections could not be written this time; the evidence log shows what was verified.` (with `the table and` before `the evidence log` when a table qualifies); the table if it qualifies; the failed parts listed.
- **The bottom-line draft returns no kept sentence, but the pass still cites something** (a D8 outage on the bottom-line call alone, distinct from every part failing): `## Bottom line` reads `A summary could not be written this time; the sections below give what was found.`; the table and the part sections print as they otherwise would.
- **What we couldn't confirm** prints each group only when it is non-empty, in this order: `We found no source we could check that answers:` then `- {question}` (searched targets); `This run did not research:` then `- {question}` (targets not searched); `We found sources on these but could not state a checked answer:` then `- {question}` (a required target a citable finding answers -- an explicit binding or a fallback through the sub-topic -- but that no printed statement states and that is not already listed above; a vanished part, or one whose sentence was refused, closes the loop this way instead of silently disappearing; `agents/report.answered_not_stated_targets` computes this set once, and `quality.compute_report_quality`'s `unaccounted_required_targets` gate reads the same set as a disclosure, so a target this group names is never also a hard failure); `We could not write up {sub-topic title}; its sources are listed in the evidence log.` (failed parts); `These pages could not be opened:` then `- {title or host} ({host})` plus ` — {reason}` when recorded (at most 5, then `- and others, listed in the evidence log`). No search or page counts. The pages header stays this plain rather than adding "so nothing from them is in this report": a denied landing page can still reach the report through content read another way (a PDF fallback from the same work), and the renderer has no reliable signal on `UnreachablePage`/the citation index to rule that out. Tradeoff: plain and honest, at the cost of long lists on capped runs (Example 13.3 lists 12 questions).

## 11. Impact

### 11.1 Terminal reviewer (`agents/report_reviewer.py`)

- `REPORT_REVIEW_SYSTEM_PROMPT`: replace "Read each sentence against the code-built label it ends with… That label is what the reader sees beside the sentence…" and "A sentence that ends with no label…" with: each statement is shown with the verified labels of the figures it states (code-built, not printed beside the sentence; the reader sees provenance in the table and the sources); the table is assembled by code from the statements' option marks and the verified figures, and a cell crediting a source with a verdict or pick its backing statement does not carry is a defect against that statement's id.
- Packet: `# Key facts` becomes `# Verified figures` (the same `_fact_row_line` lines); `# Not found` becomes `# What the report could not confirm` ("the report must list these and must not present them as answered"); a new `# Table` block has one line per row with the cell texts and the backing statement and fact-row ids; each finding block gains a `status:` line (verified / verified with corrections / quoted, not checked for context).
- `REPORT_REVIEW_PROMPT_VERSION = "report-review-5"`; `composition_semantic_fingerprint` adds `table`, `parts` and `unreachable` (option marks already ride on the statements).
- Shared file: the content wave's D11 edits `REVIEW_DEFECT_RULES` in the same module. Different constants; whichever lands second rebases; one Fable review covers both.

### 11.2 Quality gates (`agents/quality.py`)

The formulas stay: `points` (summary plus sections) is the bottom line plus the part sections; table cells are not points, so a required target must still be stated by a statement (the section rule guarantees it). `quoted_findings` already landed with the content wave. One change: `forecasts_without_release` counts forecast rows whose finding has neither `release_date` nor `statement_date` (today it reads `row.release`, which can be a vintage alone), matching what the table prints.

### 11.3 E2E harness (`src/deep_research/e2e_evaluation/`)

- `replay.py:1268-1313` `_reply_ReportWriterDraft` becomes `_reply_SectionDraft` (one point per figure line of the part's request, marking the figure's subject as the option, with the figure text as its verdict, when the line names a subject) and `_reply_BottomLineDraft` (up to 4 of the checked statements' texts with their labels); the harness routes by schema name as today.
- `replay.py:2884` (`composition.answer_kind == "constraints"`) becomes live because `answer_kind` is now set; re-verify the cases it guards.
- `replay_matrix.py` phrases, re-expressed on the new rendering (prefer structured checks on `composition.table`): `"| Subject |"` becomes `"| Option |"` (2034, 2120-2123; `"| Kettle K1 |"` and `"| Kettle K2 |"` still match option rows); `"No figure passed the Evidence Verifier."` (2344) becomes an assertion that no table prints; the forbidden `"| the number of Kettle units shipped | 2024 |"` (2433) becomes the same fact in the findings-table row shape; `"period resolved from the page date 2026-02-20"` (2561) becomes `"counted from the page's date, 2026-02-20"`; `"Example News (source does not attribute it)"` (2632-2635) becomes the Who cell `Example News` (the forbidden `according to Example Institute` stays); `"…'s own figure"` and `"relayed by news.example.test from …"` (2126-2127, 2746-2749) become `"{Org}"` and `"{Org}, reported by {relay publisher}"`; the forbidden `"Wire Service's own figure"` (912-915) becomes a structured check that the row's attribution is `relayed`.
- `runner.py`: `_REFERENCE_LINE` still matches the ordered Sources list; update the byline docstring of `canonical_report_fingerprint`.

### 11.4 CLI, API, graph

The CLI and the API print or return the report and paths only (`cli.py:1115-1117`, `api/app.py:238-263`): no change. `graph/nodes.py:389-391` re-renders the finalized composition with the same pure functions: no change. `report_written_event` metadata gains `table` (shape or none), `parts` and `failed_parts`.

### 11.5 Pins, tests, docs

`tests/test_evaluation/test_config.py` `PINNED_TARGET_PROMPT_FINGERPRINTS`: the `report_writer` and `evidence_verifier` pins move (module source is hashed). Tests pinning today's format: `tests/test_agents/test_report_layout.py`, `test_report.py`, `test_report_writer.py`, `test_report_reviewer.py`, `test_tool_free_prompts.py`; `tests/test_evaluation/test_cases_report_writer.py`, `test_evaluators_general.py`, `conftest.py`; `tests/test_cli/test_report_quality_acceptance.py`; `tests/test_e2e_evaluation/test_replay_doubles.py`, `test_runner.py`; `tests/test_graph/test_orchestrator.py`; `tests/test_runtime/test_outcome.py` (`(\d+) sources cited` becomes `· (\d+) sources?`). Docs: a superseded-by note in the 2026-09-24 spec §6.1; the WRI blocks of `docs/prompt-review/agent-prompts-review.md` after Fable's review.

## 12. Interfaces requested from the content wave

|Id|Interface|Used for|Until it lands|
|---|---|---|---|
|I1|A page date on `ReadRecord` (published or modified metadata, or a byline date, as the page writes it) or a populated `ScoredSource.temporal.publication_date` (D14)|Sources dates; Recommended-by dates (Fable's "guide date")|no dates print|
|I2|A denial reason on `CandidateRecord` (access refused 401/403/451, not found 404/410, blocked, paywall) (D20)|the reason under "pages could not be opened"|URL and title only|
|I3|`Finding.subject` for no-figure findings, admitted only when the read names it (D7)|a `subject:` registry line; mark checks|the `content` and `passage` lines|
|I4|Explicit-only answering (D9)|partition steps 2 and 4 vanish; statement `target_ids` from explicit bindings|rules unchanged|
|I5|One fact row per fact across loops and unit spellings (D13)|findings-table rows|duplicates may print|
|I6|`vintage` admitted against the read (optional)|the edition in "when"|never printed|
|—|FindingStatus `quoted`, `quoted_findings` (D21; landed)|status labels (§9); the `quoted` risk in §7|—|

## 13. Examples

Three renderings from real output. Partitions follow §6.1 over each run's recorded bindings; part titles, option marks and bottom lines are (I); section points are the run's checked statements (R) unless marked. Publishers are what `page_owner` would return [INFERENCE: not recorded in the artifacts]. No source dates print: every `publication_date` was null (D14).

### 13.1 Options table — run 3 (session `2f74eaf9…`, pass 0; `answer_kind` factual)

```markdown
# which is the best headphones to buy 2026 for best audio quality and best mic

Evidence as of 2026-09-25 · 5 sources

## Bottom line

SoundGuys names the Sony WH-1000XM6 the best wireless headphone overall for most people in 2026 [1]. CNET's 2026 guide presents the Sony 1000X The Collexion as the best high-end wireless noise-canceling headphones [2], and Business Insider says the Sony WH-1000XM5 are still the ones to beat among wireless over-ear headphones, giving the older model the edge in overall value [3]. For the microphone, Tom's Hardware's pick for best overall gaming headset is Razer's BlackShark V2 Pro, whose detachable boom mic, it says, makes your voice sound great [4].

| Option | Audio quality | Microphone | Prices | Recommended by |
|---|---|---|---|---|
| Sony WH-1000XM6 | the best wireless headphone overall; sound score of 4.8 out of 5 — SoundGuys [1] | good mic quality — SoundGuys [1] | — | SoundGuys [1] |
| Sony WH-1000XM5 | still the ones to beat; the most versatile and well-rounded Bluetooth headphones you can buy — Business Insider [3] | — | $298, down from $399.99 — Business Insider [3] | Business Insider [3] |
| Sony 1000X The Collexion | the best high-end wireless noise-canceling headphones; a 9.2 score — CNET [2] | — | — | CNET [2] |
| BlackShark V2 Pro | — | best overall gaming headset — Tom's Hardware [4] | — | Tom's Hardware [4] |
| Apple AirPods Max 2 | its best noise canceling headphones — SoundGuys [1] | — | — | SoundGuys [1] |
| Bose QuietComfort Ultra Headphones (2nd Gen) | — | — | $399.99 at Amazon — SoundGuys [1] | SoundGuys [1] |
| OpenFit Pro | among the best-sounding open earbuds — CNET [2] | — | $250 — CNET [2] | — |
| Liberty 5 Pro | — | voice-calling performance is truly top-notch — CNET [2] | — | — |

*Each cell quotes the report's own sentence about the option in that part; the whole sentence is in the section of the same name. "Recommended by" names the sources that pick the option. Options picked by more sources come first.*

## Audio quality

- SoundGuys names the Sony WH-1000XM6 the best wireless headphone overall for most people in 2026, combining class-leading ANC, customizable sound and long battery life in a comfortable, travel-friendly design [1].
- SoundGuys lists the Sony WH-1000XM6 as the best wireless headphones at 390.00 dollars at Amazon against an MSRP of 459.99 dollars, with a sound score of 4.8 out of 5, an ANC score of 8.7 out of 10 and 37 hours per charge [1].
- CNET's 2026 guide presents the Sony 1000X The Collexion as the best high-end wireless noise-canceling headphones with a 9.2 score, redesigned for improved comfort, reinforced with stainless steel and carrying upgraded drivers for a more expansive sound stage and refined sound than the XM6, a more powerful V3 chip and Bluetooth 6.0 [2].
- SoundGuys names the Apple AirPods Max 2 its best noise canceling headphones at 479.00 dollars at Amazon, with excellent noise cancellation, great sound quality and seamless working with Apple devices [1].
- Tom's Hardware says Audeze's original Maxwell is one of the best-sounding gaming headsets it has ever tested, and that the second-generation Maxwell 2 is very similar [4].
- According to the Best Headphones to Buy in 2026, Tested and Reviewed, the OpenFit Pro are well-designed and among the best-sounding open earbuds [2].
- Business Insider says the Sony WH-1000XM5 offer a standout blend of excellent sound quality, useful features and effective noise cancellation, and calls them the most versatile and well-rounded Bluetooth headphones you can buy [3].
- Business Insider says that if you're after wireless over-ear headphones, the Sony WH-1000XM5 are still the ones to beat, giving the older model the edge in overall value over the pricier WH-1000XM6 [3].
- SoundGuys says it has tested more than 400 pairs of headphones in its lab, from everyday on-ears to audiophile-grade wired designs [1].
- SoundGuys says its picks are backed by objective data: it measures frequency response and noise attenuation on a B&K 5128 head simulator, the same equipment used by professional audio engineers and hearing aid manufacturers, and uses the MDAQS algorithm to generate sound quality scores from a virtual panel of listeners [1].

## Microphone

- Tom's Hardware's pick for best overall gaming headset is Razer's updated-for-2023 BlackShark V2 Pro, a lightweight and extremely comfortable wireless over-ear headset with a detachable boom mic that, it says, makes your voice sound great [4].
- SoundGuys writes that if you want great ANC, good mic quality and support for high-quality codecs like LDAC, SBC, AAC and LC3, the Sony WH-1000XM6 is the model to beat [1].
- CNET says the Liberty 5 Pro buds' voice-calling performance is truly top-notch [2].

## Headphones or a headset

- SoundGuys' buying guide says the headset with the best microphone quality is usually one with an external boom mic, like a gaming headset, but that there are plenty of great headphones for conference calls beyond the gaming arena [5].

## Prices

- Business Insider lists the Sony WH-1000XM5 as its best overall pick at $298, down from $399.99 [3].
- SoundGuys lists the Bose QuietComfort Ultra Headphones (2nd Gen) as its best headphones for comfort at $399.99 at Amazon [1].
- CNET says the OpenFit Pro are earbuds that cost $250 [2].

## What we couldn't confirm

These pages could not be opened:
- Wirecutter (nytimes.com)
- A Business Insider guide to wired and wireless headphones (businessinsider.com)

## Sources

1. SoundGuys — [The best headphones in 2026: Lab-tested by experts](https://soundguys.com/best-headphones-2559)
2. CNET — [Best Headphones to Buy in 2026, Tested and Reviewed](https://cnet.com/tech/mobile/best-headphones)
3. Business Insider — [Best Headphones of 2026](https://businessinsider.com/guides/tech/best-headphones)
4. Tom's Hardware — [Best Gaming Headsets 2026: Our Tested Picks for Comfort, Connectivity, and Communication](https://tomshardware.com/peripherals/gaming-headsets/best-gaming-headsets)
5. SoundGuys — [Headphones buying guide: Everything to get you started](https://soundguys.com/headphones-buying-guide-8290)

How this was researched: [evidence log](report-2f74eaf95d644e688d2673cff04106f9-0-evidence.md)
```

How this rendering was derived:

- Parts: topic-01 "published leaders for audio quality" (required t01-1) → Audio quality; topic-02 "published leaders for microphone quality" (required t02-1) → Microphone; topic-03 "device class and combined-use fit" (optional only) → Headphones or a headset; topic-04 "current purchase frame" (optional only) → Prices.
- (R): every Audio point except the eighth, Microphone point 1, and the headset point. (E): Audio point 8 is S008 plus Business Insider's value criterion and the newer model from the same page section (the D15 rule; Fable A3 locates the words on the page) [INFERENCE: the exact passage wording]. (I): Microphone points 2 and 3 are S011 and S012 naming the item the finding content names (D5, D6); as printed in run 3, S011 would be refused by §6.4 rule 5. Also (I): the Prices points (topic-04's 13 findings were cited by no run-3 statement), the bottom line, and every option mark.
- Not rendered: S010 (Fable D8, a caption read as a verdict; a content-wave fix; this format would print it) and S016 (the Shure page, context-only under D16).
- Table: the parts marking ≥2 options are Audio quality, Microphone and Prices; the headset part marks none. Row order: six options picked by one page each (the XM6 and XM5 have two part cells each), then OpenFit Pro (two cells) and Liberty 5 Pro (one).
- What we couldn't confirm (I): Fable found Wirecutter (403 twice) and a Business Insider wired-vs-wireless page (404) denied; titles and reasons wait for I2.
- Run 3's own findings cannot fill Fable's acceptance table (only its prices exist there); that table is the §4.2 fixture.

### 13.2 Findings table — pre-flight run 4 (session `9390e6e1…`, pass 0; `answer_kind` factual)

```markdown
# How much grid-scale battery storage capacity was added in the United States in 2024, and what do the latest forecasts project for 2025?

Evidence as of 2026-09-25 · 4 sources

## Bottom line

For 2024, house.gov reports that generators added 10.4 GW of new battery storage capacity, the second-largest generating capacity addition after solar [1]. The Energy Information Administration's forecast, released 2025-06-10 and reported by Utility Dive, projects domestic storage capacity rising from about 28 GW at the end of Q1 2025 to 64.9 GW at the end of 2026 [2]. The U.S. Energy Information Administration reports that by the end of 2025 the U.S. power system had operational battery storage capacity of 43.6 GW [3].

| What was measured | Result | Who reported it (and when) | Source |
|---|---|---|---|
| "Generators added 10.4 GW of new battery storage capacity in 2024, the second-largest generating capacity addition after solar." | 10.4 GW, actual | house.gov (stated 2025-03-12) | [1] |
| "cumulative utility-scale battery storage capacity exceeded 26 gigawatts (GW) in 2024, according to our January 2025 Preliminary Monthly…" | 26 gigawatts (GW), actual | house.gov (stated 2025-03-12) | [1] |
| Battery storage capacity, 2025 | 43.6 gigawatts (GW), actual | U.S. Energy Information Administration (stated 2026-08-07) | [3] |
| Battery storage (first six months of 2026) | 8.3 GW, actual | U.S. Energy Information Administration (stated 2026-08-07) | [3] |
| "Utility-scale battery storage in the United States is poised to more than double over the next two years and will close out 2026 at nearly 65 GW…" | 65 GW, forecast | Energy Information Administration, reported by Utility Dive (released 2025-06-10) | [2] |
| Battery storage (utility-scale), Q1 2024 | 17 GW, actual | Energy Information Administration, reported by Utility Dive (released 2025-06-10) | [2] |
| "Counting projects larger than 1 MW in the electric power sector, EIA said domestic storage capacity will rise from about 28 GW at the end of Q1'25…" | 28 GW, actual | Energy Information Administration, reported by Utility Dive (released 2025-06-10) | [2] |
| "…EIA said domestic storage capacity will rise from about 28 GW at the end of Q1'25 to 64.9 GW at the end of 2026." | 64.9 GW, forecast | Energy Information Administration, reported by Utility Dive (released 2025-06-10) | [2] |
| ERCOT, 2025 | 15 GW, actual | EIA, reported by energi.media (stated 2026-01-21) | [4] |
| ERCOT, 2027 | 37 GW, forecast | EIA, reported by energi.media (stated 2026-01-21) | [4] |

## Capacity added in 2024

- For 2024, house.gov reports that generators added 10.4 GW of new battery storage capacity, the second-largest generating capacity addition after solar, from the January 2025 Preliminary Monthly Electric Generator Inventory [1].
- house.gov also reports that cumulative utility-scale battery storage capacity exceeded 26 GW in 2024, from the same January 2025 Preliminary Monthly Electric Generator Inventory [1].
- The U.S. Energy Information Administration reports that by the end of 2025 the U.S. power system had operational battery storage capacity of 43.6 GW [3].
- The U.S. Energy Information Administration reports that operators added another 8.3 GW of battery storage during the first six months of 2026 [3].

## The EIA's forecasts

- According to the Energy Information Administration, as reported by Utility Dive and released 2025-06-10, domestic storage capacity will rise from about 28 GW at the end of Q1 2025 to 64.9 GW at the end of 2026, counting projects larger than 1 MW in the electric power sector [2].
- According to the Energy Information Administration, as reported by Utility Dive, utility-scale battery storage in the United States is forecast to more than double over the next two years and to close out 2026 at nearly 65 GW, a rise from 17 GW in the first quarter of 2024 (forecast released 2025-06-10) [2].

## Other forecasts

- The EIA expects battery capacity in ERCOT to rise from about 15 GW in 2025 to 37 GW by the end of 2027, according to EIA, as reported by energi.media [4].

## What we couldn't confirm

We found no source we could check that answers:
- How much grid-scale battery storage capacity did the United States add in 2024, according to the U.S. Energy Information Administration?
- What 2025 grid-scale battery storage capacity additions did the U.S. Energy Information Administration's latest forecast covering 2025 project?

## Sources

1. house.gov — [U.S. battery capacity increased 66% in 2024](https://docs.house.gov/meetings/II/II00/20260513/119199/HHRG-119-II00-20260513-SD003.pdf)
2. Utility Dive — [US utility-scale energy storage to double, reach 65 GW by 2027: EIA](https://utilitydive.com/news/us-utility-scale-energy-storage-to-double-reach-65-gw-by-2027-eia/750338)
3. U.S. Energy Information Administration — [Battery storage capacity averaged 70% growth over the last three years](https://eia.gov/todayinenergy/detail.php?id=67925)
4. energi.media — [U.S. Electricity Generation Set to Rise as Solar and Battery Capacity Expand: EIA Forecasts - Thoughtful Journalism](https://energi.media/news/u-s-electricity-generation-set-to-rise-as-solar-and-battery-capacity-expand-eia-forecasts)

How this was researched: [evidence log](report-9390e6e10b324fb6a6dc17477738a531-0-evidence.md)
```

How this rendering was derived:

- No row answers a planned target (both required targets name the U.S. EIA and the answering gate did not accept house.gov's print), so eligibility comes from the checked statements' citations: all 10 rows (K001–K010). The rival rule quotes K001/K002 (one subject, 2024, actual) and K004/K007 (2026 forecasts; K007 has no subject); K006 has no subject. Kinds are mixed, so each Result carries its kind and there is no caption.
- Who: house.gov's own figures show the host (the page names no body the host confirms) [INFERENCE]; relayed rows credit the EIA and the relay; `stated` and `released` are the admitted dates. The vintage "January 2025 Preliminary Monthly Electric Generator Inventory" is absent from the table (an unverified field) but stays in the prose, where the Statement Check verified it against the snippet.
- The placement of F03/F06 (the EIA's own page) in "Capacity added in 2024" is [INFERENCE]: their bindings and `related_sub_topic` are not in the artifacts.
- (E): the first point of each of the first two parts drops the absence clauses and label words ("as house.gov's own figure") that WRI-2 already forbids; (R): the rest of the points; (I): the bottom line and the part titles. Not printed: S006–S009, the old format's sections restating its summary (each finding is now written once).

### 13.3 Explanation — generality smoke (session `e5d6cc40…`, pass 0; `answer_kind` explanation; a capped run)

```markdown
# How does the U.S. Electoral College work?

Evidence as of 2026-09-25 · 5 sources

## Bottom line

Under the Constitution, each State appoints, in the manner its Legislature directs, a number of electors equal to its Senators and Representatives in Congress [1][2]; archives.gov reports 538 electoral votes in all, with 270 needed to elect, for the 2024 and 2028 presidential elections [3]. The electors meet in their respective states and vote by ballot for two persons [1], and if two or more candidates remain with equal votes, the Senate chooses the Vice President from them by ballot [2].

| What was measured | Result | Who reported it (and when) | Source |
|---|---|---|---|
| The District of Columbia | three electors | National Archives | [3] |
| "…Senators and Representatives in its U.S. Congressional delegation—two votes for its Senators in the U.S. Senate…" | two votes | National Archives | [3] |
| Total Electoral Votes, 2024 and 2028 presidential elections | 538 electoral votes | National Archives | [3] |
| Majority Needed to Elect, 2024 and 2028 presidential elections | 270 votes | National Archives | [3] |

*No figure in this table is a forecast.*

## How many electors there are

- archives.gov reports 538 total electoral votes and 270 votes as the majority needed to elect, for the 2024 and 2028 presidential elections, on allocations based on the 2020 Census [3].
- archives.gov states that under the 23rd Amendment of the Constitution the District of Columbia is allocated three electors and treated like a State for purposes of the Electoral College [3].
- archives.gov states that electoral votes are allocated among the States based on the Census, every State receiving a number of votes equal to the number of Senators and Representatives in its U.S. Congressional delegation — two votes for its Senators in the U.S. Senate plus a number of votes equal to the number of its Congressional districts, one for each Member in the House of Representatives [3][4].

## How electors are appointed and who may serve

- According to cornell.edu and justia.com, the Constitution provides that each State shall appoint, in such manner as the Legislature thereof may direct, a number of electors equal to the whole number of Senators and Representatives to which the State may be entitled in the Congress [1][2].
- According to justia.com and cornell.edu, no Senator or Representative, or person holding an office of trust or profit under the United States, shall be appointed an elector [1][2].
- cornell.edu states that the Constitution's text and the Nation's history both support allowing a State to enforce an elector's pledge to support his party's nominee — and the state voters' choice — for President [5].

## How the electors vote

- According to cornell.edu, the electors shall meet in their respective states and vote by ballot for two persons, of whom one at least shall not be an inhabitant of the same state with themselves [1].

## When no candidate has a majority

- According to justia.com, if two or more candidates should remain with equal votes, the Senate shall choose from them by ballot the Vice President [2].

## What we couldn't confirm

This run did not research:
- What date does federal law set for the appointment of presidential electors?
- Which states award their electoral votes by congressional district rather than statewide?
- On what day must the presidential electors meet to cast their votes?
- In what place must the electors meet to cast their votes?
- What instrument must a state's executive issue to certify which electors were appointed?
- To which officials must the certificate identifying a state's appointed electors be transmitted?
- What legal effect does a state's determination of its electors have if it is made under the federal safe-harbor provision?
- On what date must Congress count the electoral votes?
- Who presides over the joint session at which Congress counts the electoral votes?
- Which body elects the President if no candidate receives a majority of the electoral votes?
- What voting arrangement governs the House of Representatives when it elects the President?
- What voting arrangement governs the Senate when it elects the Vice President?

## Sources

1. cornell.edu — [Article II | U.S. Constitution | US Law | LII / Legal Information Institute](https://law.cornell.edu/constitution/articleii)
2. Justia — [Electoral College :: Article II. Executive Department :: U.S. Constitution Annotated :: Justia](https://law.justia.com/constitution/us/article-2/03-electoral-college.html)
3. National Archives — [Distribution of Electoral Votes](https://archives.gov/electoral-college/allocation)
4. National Archives — [What is the Electoral College?](https://archives.gov/electoral-college/about)
5. cornell.edu — [CHIAFALO v. WASHINGTON | Supreme Court | US Law | LII / Legal Information Institute](https://law.cornell.edu/supremecourt/text/19-465)

How this was researched: [evidence log](report-e5d6cc40fa74403ab7af075a9153c305-0-evidence.md)
```

How this rendering was derived:

- A capped smoke run (2 of 7 sub-topics researched). Parts with findings: topic-01 (optional targets only) → How many electors there are; topic-02 → How electors are appointed and who may serve; topic-04 → How the electors vote; topic-07 → When no candidate has a majority. F01–F03 answer t02-1/t02-2 (required) before t01-1 (optional), so they land in topic-02.
- Table: an explanation with no options, so no options table; 4 rows are eligible because checked statements cite F06, F07 and F08 (F09/F10's rows — 538 electors, 270 electoral votes, 3 electors — are cited by no statement and answer no required target, so they stay in the evidence log). K002's subject "its Senators" starts with a possessive, so it prints the quoted form [INFERENCE: the evidence words beyond the recorded first 140 characters]. All rows are actual, hence the caption. Who: the Context Check organisation is the host `archives.gov`; the page credit gives "National Archives" from the title's segment [INFERENCE]; the "2020 Census" edition is a vintage (unverified) and does not print, while the prose keeps it (S005, checked).
- The Justia title keeps its "::" segments because `title_segments` splits only on `|`, en and em dashes and ` - `.
- Not printed: S009–S015 (restatements) and S017/S018 ("The findings cited here set out…", commentary on the report itself).
- What we couldn't confirm: 12 required targets with `searched: false` (the run's cap), in one plain list.

## 14. Task breakdown (TDD; one owner per file)

Each task writes its RED tests first, then the code. Mid-flight, a task runs only its own test files; the controller runs the full gates once all tasks land. Build in a new worktree from the plan tree's head once the content wave's commits that touch the same files (`utils/types.py`, `agents/quality.py`, `agents/report.py:1113`, `agents/evidence_verifier.py`, `agents/report_reviewer.py`) have merged.

|Task|Owner files (exclusive)|Depends on|RED tests first|Done when|
|---|---|---|---|---|
|T1 Types|`utils/types.py` and its tests|—|the `ReportTable` row-width validator; a composition round-trip with `parts`, `table`, `page_credits`, `unreachable`, `dropped_marks` and statement `items`; a composition without them (an older snapshot) still validates|its tests pass|
|T2 Table builders|new `agents/report_table.py`; new `tests/test_agents/test_report_table.py`|T1|(1) the §4.2 Fable fixture: columns, row order, cells, Recommended by with dates, the cap caption on a ninth option; (2) the choice rule: marks for ≥2 options only in an optional part give no options table, ≥2 eligible rows give a findings table, 1 row gives none; (3) marks on `unchecked` statements are ignored; (4) the Example 13.2 rows: rival quotes for K001/K002 and K004/K007, K006 quoted for lacking a subject, the Who strings, the mixed-kind suffixes; the Example 13.3 rows: the all-actual caption and K002's possessive quote; `context_unchecked` rows excluded; the vintage never printed; the cap-12 selection priority; (5) Who: an own organisation equal to the host prints the page publisher; unattributed prints the publisher alone|pure functions, no provider|
|T3 Renderer, evidence log, quality|`agents/report.py`, `agents/quality.py`; `tests/test_agents/test_report_layout.py` (rewritten), `tests/test_agents/test_report.py`, the quality tests|T1, T2|the skeleton order and the cuts (no Executive summary, Key facts, Not found, counts or scope); the evidence line, including `No source could be checked.`; bottom-line markers before the stop; citation order bottom line → table → sections; the Sources line (publisher, title strip, link, optional date); the couldn't-confirm groups and the 5-page cap; the unchecked-sentence suffix; the evidence log's About block, Verified figures table and new lists; the quality JSON additions and the unclamped review problem (D19); `forecasts_without_release` on admitted dates; goldens of §13.2 and §13.3 rendered from fixture compositions built from those runs' quality JSONs|the report and quality tests pass; the three filename helpers live in `report.py` and every caller imports them from there|
|T4 Parallel writer|`agents/report_writer.py`; `check_statements(gate=...)` only in `agents/evidence_verifier.py`; `utils/config.py`, `config.yaml`, `observability/run_telemetry.py`; `tests/test_agents/test_report_writer.py`, `tests/test_config.py`, the pins in `tests/test_evaluation/test_config.py`, `tests/test_agents/test_tool_free_prompts.py`|T1 (T2, T3 for the composed result)|the partition order and exactly-once placement; context-only refusal (D16); registry `content:` and `passage:` lines (D5); one call per non-empty part, each listing only its part's registry lines, all sharing an identical static prefix; pipelining (a fake provider shows a part's check starting before a slower part's draft returns); the two concurrency bounds held (counting fakes); failure isolation (one part raises → the others compose, `report_writer_section_failed`; a truncated part is re-asked once at high effort); `ProviderConfigurationError` cancels the siblings and propagates; the bottom line runs last, refuses a label no checked statement cites, keeps at most 4, falls back by moving points; the mechanical rules (outside-part label, the unnamed-subject guard, the ten-point cap, the title fallback, marks re-validated after a correction); ids renumbered in render order whatever the completion order; the redraft carries over untouched parts with no call and shows the touched part its previous section and routed defects (D18); `answer_kind`, `page_credits` and `unreachable` set; the config key and its env override|its tests pass; the prompt generality check finds no probe term in model-read strings; pins updated|
|T5 Reviewer|`agents/report_reviewer.py`; `tests/test_agents/test_report_reviewer.py`|T1–T3|the packet's `# Verified figures`, `# Table` (with statement ids) and `# What the report could not confirm` blocks and per-finding `status:` lines; the prompt no longer says "the code-built label it ends with"; `report-review-5`; the composition fingerprint changes when the table or the parts change|its tests pass; rebased on the content wave's D11 edit|
|T6 E2E harness, evaluation, docs|`src/deep_research/e2e_evaluation/*`; `tests/test_e2e_evaluation/*`, `tests/test_evaluation/*`, `tests/test_cli/test_report_quality_acceptance.py`, `tests/test_runtime/test_outcome.py`, `tests/test_graph/*`; the docs of §11.5|T3–T5|the scripted `SectionDraft` and `BottomLineDraft` replies; the §11.3 phrase rewrites, as structured checks on `composition.table` where a phrase pinned a label|controlled e2e 32/32 × 3 with zero network; the full suite green|

Order: T1 → (T2 ∥ T4's partition and drafting core) → T3 → T4 (compose → render) → T5 → T6.

Gates before merge: **G1** Fable reviews the WRI-1–6 texts produced by T4 (the D10 check included). **G2** offline gates: the full suite, `test_state`, controlled e2e × 3, the prompt generality check. **G3** the next live run measures §6.12 and shows the options table on a which-is-best question.

## 15. Risks

- **R1 Mis-marked picks.** `picked` rests on the writer's mark over a checked sentence. Mitigations: the reviewer's `# Table` block names each cell's backing statement ids; audits count mis-marks (the §4.4 threshold).
- **R2 Cross-part coherence.** Parts are written without seeing each other. Mitigations: the exactly-once partition; the bottom line reads every checked part.
- **R3 No guide dates yet.** Fable's acceptance asks for a guide date per option; live runs show none until I1 lands (the fixture exercises the code path).
- **R4 Churn.** About 20 test files and the e2e expectations change; structured checks on `composition.table` replace label phrases so the next wording change does not repeat it.
- **R5 Shared files with the content wave.** `utils/types.py`, `agents/quality.py`, `agents/report.py`, `agents/evidence_verifier.py`, `agents/report_reviewer.py`: merge order per §14; separate regions or constants.
- **R6 The unnamed-subject guard is English-only.** It targets quoted pronoun clauses; the prompt rule and the reviewer cover the rest.
- **R7 Tokens.** The static prefix repeats per part and no-figure findings now carry passages; the prefix cache is best-effort (§6.12).
- **R8 Host publishers.** Where a page does not name itself in words its host confirms, the Sources line and the Who column show the host (`cornell.edu`).

## 16. Open questions

- **Q1** The table has no writer call of its own, but its option cells come from the section writer's marks over checked sentences. Accept that (chosen), or restrict the table to verified figures only? Then run 3's report would show prices and scores and no picks or Microphone column.
- **Q2** Should the planner owner add "obligation(s)" to the constraints markers (a narrow change; P4 would get the constraints framing)? This design does not need it; superlative markers are not recommended.
- **Q3** Evidence line: the newest evidence timestamp's date (chosen) or the run's own date (`generated_on`, the brief's "run date")? They differ only when every read was served from cache.
- **Q4** An unattributed figure's Who cell shows the publisher alone, per the brief's cut. Keep that, or add a plain "(names no original source)"?
- **Q5** Unreachable pages: up to 5 lines in the report (chosen), or only in the evidence log?
- **Q6** `agents.writer_section_concurrency` default: 7, so every part starts at once (chosen), or 4?

## 17. Rulings on the open questions (controller, 2026-09-25; the user delegated the format choice)

- **Q1** Accepted as chosen: option cells come from the section writer's marks over checked sentences, name and verdict word for word; the reviewer's `# Table` block and audits police R1.
- **Q2** Yes: "obligation(s)" joins the constraints markers — assigned to the content wave's planner owner (S5), not to this spec's tasks.
- **Q3** As chosen: the newest evidence timestamp's date.
- **Q4** As chosen: the publisher alone (the evidence log keeps the attribution detail).
- **Q5** As chosen: up to 5 unreachable pages in the report, the rest in the evidence log.
- **Q6** As chosen: `agents.writer_section_concurrency` default 7.
