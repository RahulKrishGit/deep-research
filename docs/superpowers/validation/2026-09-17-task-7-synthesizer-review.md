# Task 7 review - "Produce an answer, not a claim inventory"

**Plan:** `docs/superpowers/plans/2026-09-16-evidence-integrity-and-agent-production-readiness.md`
**Brief:** `.superpowers/sdd/2026-09-16-evidence-integrity-and-agent-production-readiness/task-7-brief.md`
**Report:** `.superpowers/sdd/2026-09-16-evidence-integrity-and-agent-production-readiness/task-7-report.md`
**Range:** `0f0fbbf..f521c21` - 1 commit, 14 files, +4778/-172
**Reviewer:** Opus 5, in-session, reading the diff package plus targeted probes of the built tree.
**Method:** full read of the diff package; then eight behavioural probes run against the worktree's
`src` with `PYTHONPATH` set to it. Every Critical/Important finding below was reproduced, not inferred.
Tests were not re-run: the implementer's gates (3837 passed, ruff clean, guard proof by reverting
`_strip_unsupported_figures` and `validate_report_statements` in a temp copy) are accepted as reported.

---

## Spec Compliance

**Verdict: Issues found.**

Delivered and verified against the brief:

- `ReportStatement` with the exact field list, a reader mode enumeration separate from
  `Claim.evidence_status`, and an `inference` validator that requires a `basis`
  (`utils/types.py:1087-1129`).
- `ReportPoint` / `ReportConstraint` extended compatibly through an optional `statement` plus read-only
  properties, so pre-Task-7 callers keep working (`utils/types.py:1130-1214`).
- `statement_source_urls(evidence_ids, evidence)` living in `report.py`, deriving URLs locally from the
  evidence registry and raising `UnknownEvidenceError` on an id no unit answers
  (`agents/report.py:326`). The brief's verbatim kernel test is present.
- Mirror collapse to one reader reference, preferring the recorded original (`agents/report.py:350`).
- Per-answer-form second section, with no mandatory empty constraint grid
  (`agents/report.py:127-151`, `1021`, `1214`).
- Measured methodology: linkage, repetition and limitation sentences are computed against the rendered
  artifact and the statement map before they are printed (`agents/report.py:1410-1520`).
- Frozen word ceiling and backmatter ratio, with citations never deleted to hit the ratio
  (`agents/report.py:838-1010`).
- Balanced canonical packet with explicit `omitted_ids` and `continuation_batches`
  (`agents/synthesizer.py:913-1047`).
- Generated-on rendered separately from evidence as-of and date basis (`agents/report.py:1087-1103`).

### Missing

**S-1 (Important). The structured statement-support review is not implemented.** The brief requires
"deterministic atom/reference checks **plus** a structured statement-support review of all substantive
prose" and states that "unsupported connective claims cannot pass because their numbers happen to
match". Only the deterministic half shipped. The implementer records this honestly as Concern 2 and
proposes deferring the structured half to Task 9/10, but no controller ruling authorises that deferral -
carried ruling 4 defers the **routing** of returned assertions to Task 9, not the review itself.

This is a plan-text-versus-implementation conflict, so per the SDD process it is the human's call, not
the reviewer's or the implementer's. Two coherent resolutions:

- *(a) Close it here.* Add a model-backed support-review pass over `composition.statements`, reusing the
  existing `returned_to_fact_checker` disposition for anything it refuses. This is a real chunk of work
  and a new provider call in a task that already added 4778 lines.
- *(b) Defer it with a recorded ruling.* Record in the ledger that the structured review is deferred to
  Task 9/10, that Task 7 ships the deterministic half only, and that the residual gap is
  "a correctly-cited sentence that introduces a new lowercase mechanism phrase in prose is not caught
  offline".

Recommendation: **(b)**, with the ruling written down. The deterministic half plus the cell check
already blocks every adversarial case the brief names by example; the residual is a coverage boundary,
not a missing mechanism. But it must be a recorded decision, because the brief's word is "plus".

**S-2 (Important). The naked confidence number reaches the reader, and Task 7 propagated it into three
new tables.** The brief says "Derive qualitative evidence labels from status/source breadth; avoid naked
0.90 confidence as a calibrated probability." `EVIDENCE_BADGE_LABELS` does exactly this - but only in
the *model-facing* canonical packet (`agents/synthesizer.py:679`). The *reader* still gets
`f"{claim.verdict} {claim.confidence:.2f}"` in the Evidence strength column and a bare `0.76` in the
Confidence column (`agents/report.py:1200-1213`). The Confidence column is pre-existing in
`_reader_constraints`, which is why this is not simply a regression - but `_reader_answer_rows`
(`agents/report.py:1266`) is new code in this commit and reproduces both columns for the comparison,
factual and historical tables. The brief's bullet is therefore unmet in exactly the surface Task 7 owns.

Proposed fix, in `agents/report.py`:

```python
def _evidence_strength(claims: Sequence[Claim]) -> str:
    """The qualitative reading of the badges behind a row, not a probability."""
    if not claims:
        return _CELL_EMPTY
    labels: list[str] = []
    for claim in claims:
        badge = claim.evidence_status or ""
        label = EVIDENCE_BADGE_LABELS.get(badge, badge or claim.verdict)
        if label not in labels:
            labels.append(label)
    return _CELL_SEPARATOR_JOIN.join(labels)
```

and drop the Confidence column from the three headers in `_reader_constraints` and
`_reader_answer_rows` (and the matching `_weakest_confidence` call), leaving the numeric confidence in
the ledger's claim registry where a calibrated-probability caveat can sit beside it. Import
`EVIDENCE_BADGE_LABELS` from `synthesizer` is a cycle; move that dict into `utils/types.py` beside
`StatementMode` and import it from both.

Note this changes four rendered table headers, so `test_report.py` and
`tests/test_cli/test_report_quality_acceptance.py` column assertions move with it.

### Cannot verify from the diff

- **W-1. "Ranking requires an evidenced comparison basis ... when ranking is unjustified, group
  constraints by type/region and say no defensible universal order was established."** This ships as
  prompt text only (`answer_form_instruction`, `agents/synthesizer.py:689-744`). There is no local check
  that a rank was evidenced and no fallback grouping when it was not - unlike every other brief bullet in
  this task, which got a local gate. Controller: decide whether prompt-level satisfies this bullet. If
  not, it needs the same treatment as the cell check.
- **W-2. Reader *limitations* are not statement-backed.** The brief says limitations "must use statement
  records". `render_limitations` still renders from `composition.limitations`, a fixed reason-code
  vocabulary (`agents/report.py:184`). Defensible - these are project-authored strings,
  not model prose, so there is nothing to attest - but it is a literal deviation and should be recorded
  as an accepted one rather than left unremarked.

---

## Strengths

Accurate praise first, because the rest of this review is long and the work under it is good.

- **The guard proof is the right kind of evidence.** Reverting `_strip_unsupported_figures` and
  `validate_report_statements` in a temp copy and showing the 890 GW leak reproduce in the reader is
  proof the guard is load-bearing, not proof the test passes. More reviews should get this.
- **`statement_source_urls` is exactly the contract the brief asked for** - ids are the join key, the URL
  is whatever the recorded unit was served from, and an unknown id raises before anything renders. The
  model cannot contribute a URL to the reference list by any path.
- **Measured methodology is the strongest idea in the commit.** The report's sentences about itself
  ("every statement carries a claim link", "no statement repeats another") are computed from the
  rendered text and the statement map rather than asserted, and they are explicitly excluded from
  backmatter compaction (`agents/report.py:1487`). That directly kills the failure class where scope
  prose over-claims what the artifact shows.
- **The `context` mode is the right escape valve.** Making "this pass's own framing" a first-class,
  source-free, non-substantive mode is what lets the mapping validator be strict about everything else
  without refusing honest prose.
- **`_corpus_tokens` exists at all.** The report's section 2.2 shows the implementer hit the
  `"12" in "1200 hectares"` substring bug during development and fixed it properly with whole-token
  matching rather than patching the one failing case. Findings I-2 and I-3 below are that same fix not
  being applied consistently - the diagnosis was right, the rollout was partial.
- **Test quality is good.** 46 new test functions across `test_report.py` and `test_synthesizer.py`;
  an AST scan found zero that assert nothing or only exercise a call. Every adversarial case the brief
  names has a dedicated test, and most have a positive counterpart so the check cannot pass by refusing
  everything.
- **Existing-test changes are each justified in place** with the contract change that caused them, and
  the six prompt fingerprints were re-pinned deliberately rather than loosened.

---

## Issues

### Critical (Must Fix)

None. Nothing here can publish an unsupported statement as settled, fabricate a citation, or silently
lose evidence. The findings below are wrong behaviour inside guards that are themselves sound.

### Important (Should Fix)

---

#### I-1. The uncertainty grouping vocabulary does not round-trip: six of nine note kinds land ungrouped

**Where:** `agents/synthesizer.py:1799` (`_uncertainty_basis`) against `agents/report.py:1348`
(`_uncertainty_group`) and `agents/report.py:226` (`UNCERTAINTY_GROUPS`).

**What's wrong.** `_uncertainty_basis` returns *the token it matched* ("disagree", "conflict",
"contradict", "no read", "was not retrieved", "out of scope", ...). `_uncertainty_group` then asks
whether one of `UNCERTAINTY_GROUPS`' own tokens ("uncertain", "conflicting", "contested",
"not acquired", "not_acquired", "outside scope", "out_of_scope") is a substring **of that basis**. The
two vocabularies were written independently and mostly do not intersect, and the substring test runs in
the wrong direction: `"conflicting" in "conflict"` is `False`.

**Reproduced** against the built tree:

```
note                                              basis                  group
"Sources disagree about the 2030 figure."         'disagree'             <UNGROUPED>
"The evidence conflicts on this point."           'conflict'             <UNGROUPED>
"An independent source contradicts the finding."  'contradict'           <UNGROUPED>
"No read was acquired for the transmission..."    'no read'              <UNGROUPED>
"That data was not retrieved this pass."          'was not retrieved'    <UNGROUPED>
"This question is out of scope..."                'out of scope'         <UNGROUPED>
"This question is outside scope..."               'outside scope'        Outside scope
"...the topic was not acquired."                  'not acquired'         Not acquired
"Confidence is low here."                         'uncertainty this...'  Uncertain or conflicting
```

Only the two notes that happen to use the group heading's exact wording group correctly. Everything
else falls into the unheaded bullet list `_reader_uncertainty` emits before the three `###` groups
(`agents/report.py:1355-1382`).

**Why it matters.** The brief's bullet is "Separate 'not acquired,' 'uncertain/conflicting,' and
'outside scope.'" A reader who cannot tell a live disagreement from a gap nobody retrieved has been told
none of the three - which is the exact failure the `UNCERTAINTY_GROUPS` comment says it exists to
prevent. The grouping renders, so the tests that assert a `### Uncertain or conflicting` heading exists
still pass; they pass on the default-fallback path.

**Fix.** Make `_uncertainty_basis` return the *group key* rather than the matched token. The existing
`_uncertainty_group` then matches unchanged, because each group key contains that group's own first
token as a substring. Replace `agents/synthesizer.py:1799-1811` with:

```python
# The basis a source-free note carries is the group key the renderer reads,
# not the token that happened to match: the two tables are one vocabulary.
_UNCERTAINTY_BASIS_TOKENS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "not acquired",
        ("not acquired", "was not retrieved", "no read", "not read",
         "never retrieved", "could not be retrieved"),
    ),
    (
        "outside scope",
        ("outside scope", "out of scope", "beyond the scope", "not in scope"),
    ),
    (
        "uncertain/conflicting",
        ("disagree", "conflict", "contradict", "uncertain", "contested",
         "inconsistent"),
    ),
)


def _uncertainty_basis(text: str) -> str:
    """Classify a source-free note into the group the reader meets it in."""
    lowered = text.casefold()
    for key, tokens in _UNCERTAINTY_BASIS_TOKENS:
        if any(token in lowered for token in tokens):
            return key
    return "uncertain/conflicting"
```

Order matters: "outside scope" is checked before "uncertain/conflicting" so a note saying
"conflicting evidence, but out of scope anyway" lands in the narrower group. "not acquired" stays first
because an unretrieved topic is a stronger fact than a disagreement about it.

**Test to add** (`tests/test_agents/test_synthesizer.py`), which fails today:

```python
def test_each_uncertainty_kind_reaches_its_own_reader_group() -> None:
    """The note vocabulary and the renderer's groups are one vocabulary."""
    for note, heading in (
        ("The sources disagree about the projection.", "### Uncertain or conflicting"),
        ("No read was acquired for transmission costs.", "### Not acquired"),
        ("Retail tariffs are out of scope for this pass.", "### Outside scope"),
    ):
        composition = _composition_with_uncertainty(note)
        assert heading in render_reader_report(composition)
```

**Cleanup in the same edit:** the current `_uncertainty_basis` loops over three-tuples whose first two
elements are empty strings and never read - drop that shape when you replace it.

---

#### I-2. `unattested_words` is a substring test, so the table-cell check can be defeated by any prefix

**Where:** `agents/synthesizer.py:1297`.

```python
def unattested_words(text: str, corpus: str) -> list[str]:
    return [token for token in _content_tokens(text) if token not in corpus]
```

`corpus` is a raw concatenated string, so `token not in corpus` is a substring test. This is the same
bug the implementer already diagnosed and fixed for figures with `_corpus_tokens`
(`agents/synthesizer.py:1266`, and report section 2.2) - it just was not applied to the words path.

**Reproduced:**

```
corpus = "carbon capture generation across the national grid"
unattested_words("car gen", corpus)        -> []     # expected ['car', 'gen']
unattested_words("carbon capture", corpus) -> []     # correct
```

**Why it matters.** This is the gate behind the brief's "uncited factual table cell must be rejected or
repaired". Every content word of a fabricated mechanism or geography that happens to be a prefix of a
word in the evidence is treated as attested, and `_build_cell` (`agents/synthesizer.py:1613`) publishes
the cell. "Gen" passes on "generation"; "US" passes on "usual"; "Chile" passes on nothing but "Chin"
passes on "China". The check is more porous than a word-boundary test by a wide margin.

**Fix.** Use the whole-token set that already exists, and make its tokens comparable by stripping the
trailing punctuation the corpus regex keeps (see I-3, which is the same edit):

```python
def unattested_words(text: str, corpus: str) -> list[str]:
    """Every content word of a short cell the corpus does not carry.

    Whole tokens, for the same reason the figures use them: a substring test
    lets "generation" vouch for "gen", and a cell is meant to be lifted from
    the evidence rather than composed.
    """
    tokens = _corpus_tokens(corpus)
    return [token for token in _content_tokens(text) if token not in tokens]
```

**Expect fallout.** This tightens the cell gate, so cells that passed by prefix now repair to
`not stated`. That is the intended direction (the implementer's Concern 1 already accepts
under-publishing over over-claiming), but run `test_report.py` and `test_synthesizer.py` and check
whether `test_an_attested_mechanism_and_geography_are_published_with_their_evidence` still has a
genuinely attested fixture - if its fixture was passing on a prefix, the fixture needs the real word,
not a loosened check.

**Test to add:**

```python
def test_a_cell_word_is_not_attested_by_a_longer_word_that_contains_it() -> None:
    """"generation" does not vouch for "gen": the cell check is whole-token."""
    assert unattested_words("gen", "carbon capture generation") == ["gen"]
```

---

#### I-3. Corpus tokens keep trailing punctuation, so a sentence-final figure in the evidence reads as unsupported

**Where:** `agents/synthesizer.py:1266` (`_corpus_tokens`), consumed by `unattested_atoms`
(`:1276`), `_derivation_premises` (`:181`) and `_strip_unsupported_figures` (`:1828`).

```python
return set(re.findall(r"[a-z0-9][a-z0-9'.,-]*", corpus.casefold()))
```

The character class includes `.` and `,` and is greedy, so a figure that ends a sentence in an evidence
excerpt becomes the token `"1,200."` or `"2035."`. The lookup side strips that punctuation
(`_figure_number` does `.strip(".,'")`), so the two sides never meet.

**Reproduced:**

```
corpus = "Installed capacity reached 1,200."
_corpus_tokens(corpus)  -> {'1,200.', 'capacity', 'installed', 'reached'}
unattested_atoms("capacity was 1,200 in total", corpus)  -> ['1,200 in']
```

and, through `_strip_unsupported_figures`, a note is mutilated:

```
corpus   = "the plan covers the period to 2035."
note     = "Cost beyond 2035 was not examined."
repaired = "Cost beyond was not examined."        # the 2035 IS in the corpus
```

Move the period off the corpus figure and the same note survives intact. Evidence excerpts are exact
sentences from source documents, so figures ending a sentence are not an exotic case.

**Why it matters two ways.**

1. **False refusal.** `_unsupported_figures` returns a non-empty list, `_build_point` refuses the
   statement, and a correct, fully-evidenced finding never reaches the reader
   (`agents/synthesizer.py:1570-1597`).
2. **False repair, in the exact surface the brief made a RED case.** The 890 GW guard's job is to strip
   a figure the evidence does not carry. Here it strips one the evidence *does* carry, and the reader
   gets a caveat with its subject removed ("Cost beyond was not examined"). The brief's instruction is
   "express the missing topic without the unsupported figure" - not "remove supported figures".

**Fix.** Strip the same characters on both sides, in `agents/synthesizer.py:1266`:

```python
def _corpus_tokens(corpus: str) -> set[str]:
    """The words and figures the corpus carries, as whole tokens.

    Whole tokens, not substrings: "1200 hectares" does not attest "12". The
    trailing separators are stripped so the corpus side and ``_figure_number``
    agree - an excerpt is an exact sentence, so its figures often end one.
    """
    tokens = {
        token.strip(".,'-")
        for token in re.findall(r"[a-z0-9][a-z0-9'.,-]*", corpus.casefold())
    }
    tokens.discard("")
    return tokens
```

Only the ends are stripped, so `"1,200"` keeps its internal separator on both sides and stays distinct
from `"1200"` - which is what the whole-token rule is for.

**Related, same area, smaller.** `_FIGURE_PATTERN` (`agents/synthesizer.py:122`) swallows the following
word: `_significant_figures("capacity was 1,200 in total")` yields `['1,200 in']` and
`_significant_figures("reached 12 sites")` yields `['12 sites']`. Lookup is unaffected because
`_figure_number` takes the leading numeric run, and the strip path is unaffected because the tail is
discarded unless it is in `_UNIT_WORDS`. But the token is what lands in `context.returned` and in the
ledger's disposition text, so an operator reads "an unsupported figure: 1,200 in". Tighten the unit
group to a unit-shaped tail, e.g. `(?:\s?(?:%|[A-Za-z]{1,4}\b))?`, or keep the pattern and report
`_figure_number(token)` rather than `token` in the disposition.

**Test to add:**

```python
def test_a_figure_that_ends_a_sentence_in_the_evidence_is_attested() -> None:
    """An excerpt is an exact sentence, so its figures often carry a period."""
    corpus = "Installed capacity reached 1,200."
    assert unattested_atoms("capacity was 1,200 in total", corpus) == []
    assert (
        _strip_unsupported_figures("Growth to 1,200 was not examined.", corpus)
        == "Growth to 1,200 was not examined."
    )
```

---

#### I-4. The geographic-extrapolation check misses short acronyms and every sentence-initial name

**Where:** `agents/synthesizer.py:123`.

```python
_PROPER_NOUN_PATTERN = re.compile(r"(?<![.!?]\s)(?<!^)\b[A-Z][A-Za-z][\w'-]{2,}\b")
```

Two independent holes:

1. `[A-Z][A-Za-z][\w'-]{2,}` requires four characters. `UK`, `EU`, `US`, `IEA`, `EIA`, `IRA`, `NREL`
   (four, so caught) - the two- and three-character forms are never matched.
2. `(?<![.!?]\s)` skips any name that follows a sentence terminator plus a space. The companion
   `(?<!^)` is not `MULTILINE`, so it anchors only the whole string's first character; the guard is
   inconsistent with itself.

**Reproduced,** with evidence text `"output rose sharply during the period"`:

```
"Deployment grew in the UK."               -> []             # miss
"Deployment grew in the EU."               -> []             # miss
"Output rose. California added capacity."  -> []             # miss
"Deployment grew in California."           -> ['California'] # caught
```

**Why it matters.** This is the prose half of the brief's named RED case "wrong geographic
extrapolation". The dedicated test passes because its fixture puts the place mid-sentence and spells it
out. A draft that says "Deployment grew in the EU" or that starts a sentence with the invented place
walks through.

**Fix.** Drop both lookbehinds and the length floor, and let the stopword list carry the false-positive
suppression it was built for. In `agents/synthesizer.py`:

```python
# A capitalised token is a candidate name wherever it sits: a sentence-initial
# position is not evidence that a word is ordinary, and "EU" is as much a place
# as "California". Ordinary sentence openers are suppressed by
# _ATTESTATION_STOPWORDS instead, which is the list that can be reasoned about.
_PROPER_NOUN_PATTERN = re.compile(r"\b[A-Z][A-Za-z][\w'-]*\b")
```

and filter in `unattested_atoms` (`agents/synthesizer.py:1276`):

```python
    for match in _PROPER_NOUN_PATTERN.finditer(text):
        token = match.group(0)
        folded = token.casefold()
        if folded in _ATTESTATION_STOPWORDS:
            continue
        if folded not in tokens and token not in found:
            found.append(token)
```

`_ATTESTATION_STOPWORDS` (`agents/synthesizer.py:193`) must gain the sentence-opening function words it
does not carry today, or every sentence starting "The", "A", "An", "In", "On", "At", "By", "If", "As",
"Of", "To", "No", "We", "It", "One", "Two", "Both", "Where", "How", "Why" produces a spurious
unattested atom:

```python
_ATTESTATION_STOPWORDS = frozenset(
    {
        # ... existing entries unchanged ...
        # Sentence openers: a capitalised function word is capitalised by
        # position, not because it names anything.
        "a", "an", "in", "on", "at", "by", "if", "as", "of", "to", "no",
        "we", "it", "one", "two", "both", "where", "how", "why", "so",
        "up", "out", "all", "any", "new", "per",
    }
)
```

**Expect fallout, and measure it.** This widens the refusal surface materially. Run the full suite after
the change; if legitimate drafts start being refused for an ordinary capitalised word, the right
response is to extend the stopword list (a list a reviewer can audit) and **not** to reintroduce a
position-based lookbehind (a rule nobody can reason about). If the fallout is large enough to be a
judgement call rather than a fix, stop and raise it with the controller rather than tuning it silently.

**Tests to add:**

```python
def test_a_two_letter_place_the_evidence_never_names_is_refused() -> None:
    assert unattested_atoms("Deployment grew in the EU.", "output rose") == ["EU"]


def test_a_sentence_initial_place_the_evidence_never_names_is_refused() -> None:
    text = "Output rose. California added capacity."
    assert unattested_atoms(text, "output rose") == ["California"]


def test_a_sentence_initial_ordinary_word_is_not_an_unattested_name() -> None:
    assert unattested_atoms("The output rose.", "output rose") == []
```

---

#### I-5. Answer-row label cells are published with no attestation check at all

**Where:** `agents/synthesizer.py:1715` (`_build_answer_row`).

```python
    labels = [
        ReportStatement(
            statement_id=context.next_id("A"),
            text=label,
            mode="context",
            basis="row label composed by this pass",
        )
        for label in (
            _optional_text(draft.subject, limit=_CELL_CHARS),
            _optional_text(draft.dimension, limit=_CELL_CHARS),
        )
    ]
```

`_optional_text` only clamps length. The constraints table routes its two cells through `_build_cell`
(`agents/synthesizer.py:1613`), which attests them against the row's evidence and repairs an unbacked
cell to `not stated`. The comparison, factual and historical tables route their two cells through
nothing.

**Why it matters.** The brief's RED case is "uncited factual table cell must be rejected or repaired",
and the columns those labels fill are `Option`, `Dimension`, `Subject`, `Period`
(`agents/report.py:139-151`). A Period cell is a date; a Subject cell is usually a named entity. The
exact extrapolation the constraints table blocks is open in every other answer form, and marking the
cell `mode="context"` makes it non-substantive, so `validate_report_statements` skips it too
(`agents/report.py:552`). Task 7 is the commit that introduced these three tables, so this is not
inherited.

**Fix.** Attest the labels the same way the constraint cells are attested:

```python
    if point is None:
        return None
    labels: list[ReportStatement] = []
    for cell_name, raw in (
        ("subject", draft.subject),
        ("dimension", draft.dimension),
    ):
        _, statement = _build_cell(
            text=raw,
            row=point,
            context=context,
            where=where,
            cell=cell_name,
        )
        labels.append(statement)
    return ReportAnswerRow(cells=[*labels, point.statement])
```

`_build_cell` already returns a `("", not-stated statement)` pair for an empty or unattested cell and
records `unsupported_cell` plus `returned_to_fact_checker`, so no new disposition vocabulary is needed
and `_reader_answer_rows` renders `cell.text` unchanged - an unattested label simply reads
`not stated`.

One consequence to accept deliberately: `_build_cell` returns its statement with `mode="attributed"`,
which is substantive, so an attested label will start appearing in `distinct_statement_count` and in
`ReportAnswerRow.statement`. `ReportAnswerRow.statement` returns the *first* substantive cell
(`utils/types.py:1216-1233`), which after this change would be the subject label rather than the
finding. Fix that at the same time by selecting the finding explicitly - it is always the last cell:

```python
    @property
    def statement(self) -> ReportStatement | None:
        """The row's evidenced finding: the last cell, by column contract."""
        return self.cells[-1] if self.cells else None
```

**Test to add:**

```python
def test_an_answer_row_label_the_evidence_does_not_carry_is_repaired() -> None:
    """A Subject or Period cell is a factual assertion like a mechanism cell."""
    composition, rejected = _compose_comparison(subject="Patagonia")
    row = composition.answer_rows[0]
    assert row.cells[0].text == "not stated"
    assert "unsupported_cell" in composition.statement_dispositions
```

---

#### I-6. Two near-verbatim copies of the statement-derivation logic, in two modules

**Where:** `agents/synthesizer.py:1358` (`_statement_for_claims`) and `utils/types.py:1396`
(`statement_for_point`); plus `agents/synthesizer.py:1329` (`_clusters_for_claims`) and
`utils/types.py:1374` (`_cluster_ids_for_claim`).

Both pairs implement the same algorithm - resolve clusters from a claim in both directions, union the
cluster and claim evidence ids in that order, union the cluster and claim target ids in that order,
collect required dimensions, call `answered_required_dimensions` - in about forty and twenty lines
respectively. The docstrings of `_clusters_for_claims` and `_cluster_ids_for_claim` are the same
sentences with one clause dropped.

**Why it matters.** This is the rubric's "verbatim duplication of a logic block". The two copies are
already diverging: `_statement_for_claims` collects dimensions from `context.targets` while
`statement_for_point` collects them from `required_dimensions_for_targets(composition, ...)`, and
`_statement_for_claims` forces `mode="inference"` when a basis is present while `statement_for_point`
never sets a basis at all. Whichever half of the pair a future fix lands in, the other will keep the
bug - and the pair is the boundary between the validated path and the fixture/legacy path, which is
exactly where a silent divergence is hardest to notice.

**Fix.** Keep one implementation in `utils/types.py` parameterised over its two lookups, and have the
synthesizer call it. Roughly:

```python
# utils/types.py
def derive_statement(
    *,
    statement_id: str,
    text: str,
    claims: Sequence[Claim],
    clusters: Mapping[str, ClaimCluster],
    evidence: Mapping[str, EvidenceUnit],
    required_dimensions: Sequence[str],
    basis: str = "",
) -> ReportStatement:
    """The one derivation both paths use. See _cluster_ids_for_claim."""
```

`statement_for_point` becomes a thin wrapper that supplies
`required_dimensions_for_targets(composition, target_ids)`; `_statement_for_claims` becomes a thin
wrapper that supplies the dimensions from `context.targets` and passes `basis`. Delete
`_clusters_for_claims` from `synthesizer.py` and export `_cluster_ids_for_claim` as
`cluster_ids_for_claim`.

This is a refactor with no behaviour change, so it is verified by the existing suite staying green - no
new test needed, and no test should need editing. If one does, the two copies had already diverged in a
way that matters, and that is worth reporting back.

---

### Minor (Nice to Have)

- **M-1. `fit_report_composition` lists constraint statements as droppable but cannot drop them.**
  `_statement_locations` (`agents/report.py:884`) walks `summary`, `constraints` and `sections`;
  `_drop_statement` (`agents/report.py:937`) only removes from `summary` and `sections`. A constraint
  statement is popped from `pending`, `_drop_statement` returns `False`, no words are freed, and the
  entry is consumed. The loop still terminates correctly (`dropped == 0` breaks), so this costs
  iterations and can reach `length_budget_floor_reached` while droppable prose remains. Either stop
  listing constraint rows in `_statement_locations`, or let `_drop_statement` remove a constraint row.
  Listing-only is the smaller change and matches the sacrifice order's intent (the ranked answer is the
  last thing a report should lose).

- **M-2. `render_statement_map` re-derives the fit, so the ledger render transitively renders the reader
  two-to-four more times.** `agents/report.py:1562` calls `fit_report_composition(composition)` and then
  `_render_reader(composition, compact_backmatter=2)` purely to recompute `fit_reasons` and the
  `backmatter_floor_reached` flag. Beyond the cost the implementer flagged as Concern 5, this couples
  the ledger's disposition list to re-deriving what the *reader* render did: hand `render_statement_map`
  a composition in a different state and the two artifacts disagree about the same pass, which is the
  one property `ReportComposition`'s own docstring promises. Compute the fit once in
  `build_report_composition`, append its reasons to `composition.statement_dispositions`, and let both
  renderers read the recorded list.

- **M-3. `StatementMappingError` is raised only by `validate_report_statements`, not by the renderers.**
  The report (section 1.2) says "the renderers raise too"; they raise `UnknownEvidenceError` (through
  `statement_source_urls`) but never the mapping error. The production path is safe because
  `build_report_composition` validates (`agents/synthesizer.py:1981`), but a composition rehydrated from
  state and re-rendered bypasses the only guard. One line at the top of `render_reader_report`
  (`agents/report.py:1035`) closes it: `validate_report_statements(composition)`.

- **M-4. `backmatter_ratio` locates the backmatter with `markdown.find("## Methodology")`.**
  `agents/report.py:856`. A statement whose text contains that string moves the boundary and inflates
  the ratio. The prompt forbids headings inside text fields but nothing enforces it. `rfind` is a
  one-character fix that makes the failure mode benign.

- **M-5. `ANSWER_TABLE_COLUMNS` carries a dead second element and a dead key.**
  `agents/report.py:139`. Each entry is a pair whose second element is never read
  (`labels = [label for label, _ in ...]`), and the `"constraints"` key is never looked up because
  `_reader_table` routes that form to `_reader_constraints` with its own hardcoded header. The
  annotation `dict[str, tuple[tuple[str, ...], tuple[str, ...]]]` also does not describe the value
  shape. Reduce to `dict[str, tuple[str, ...]]` holding just the label names, and drop `"constraints"`.

- **M-6. Double mirror collapse.** `_statement_urls` (`agents/report.py:608`) wraps
  `statement_citation_urls` in `collapse_mirror_urls`, but `statement_citation_urls`
  (`agents/report.py:436`) already returns a collapsed list. Idempotent, so harmless; delete the outer
  call.

- **M-7. `validate_report_statements` computes before it skips.** `agents/report.py:536`:
  `linked_clusters` and `claims` are built for every statement, then `if not statement.substantive:
  continue` discards both. Move the guard above them.

- **M-8. `_build_cell` sets a basis to force `mode="inference"`, then overrides the mode via
  `model_copy`.** `agents/synthesizer.py:1613-1676`. Pydantic's `model_copy` does not re-validate, so
  the `inference`-requires-`basis` validator is bypassed rather than satisfied. It works, but it works
  by not running the check. Construct the statement with `mode="attributed"` directly instead of
  building an inference and rewriting it.

- **M-9. `compact_backmatter` is passed `False` in one place and `0` everywhere else.**
  `agents/report.py:1002` versus `:1020`, `:1047`. The parameter is typed `int`. Same value, mixed
  types; make it `0`.

- **M-10. `answered_atom_dimensions` marks a whole field group filled when any one field is.**
  `utils/types.py:1281`: if `observation_period` is set, `forecast_status` is appended too. Consumers
  only ask `any(name in evidenced for name in fields)`, so behaviour is correct today - but the returned
  list reads as a per-field record and is not one, and any future caller that reads it that way will be
  wrong. Either return the group key or append only the fields actually filled.

- **M-11. Answer-kind fallbacks disagree.** For an `answer_kind` outside the five known forms,
  `reader_sections` (`agents/report.py:1021`) falls back to `## Constraint ranking` while `_reader_table`
  (`agents/report.py:1214`) routes to `_reader_answer_rows` with empty label columns. `AnswerKind` is a
  frozen `Literal` so this is unreachable today; make the two fallbacks name the same form so it stays
  unreachable by construction rather than by luck.

- **M-12. Missing blank line between `_names_an_operation` and `_ATTESTATION_STOPWORDS`.**
  `agents/synthesizer.py:191-193`. Ruff's selected rules do not flag it; every other top-level
  definition in the file has two.

---

## Assessment

**Task quality: Needs fixes.**

**Reasoning.** The architecture is right and the hard parts are done well: statement records drive the
reader end to end, citations are derived locally from selected evidence with no path for a
model-supplied URL, the methodology's claims about the report are measured against the rendered
artifact, and the guard proof shows the 890 GW leak actually reproduces when the guard is removed. What
needs fixing is a consistent pattern rather than a design error - the whole-token matching the
implementer correctly introduced for figures was not carried across to words (I-2) or to the corpus side
(I-3), the two new statement-derivation paths were written twice instead of once (I-6), and the
attestation the constraints table gets was not extended to the three tables this commit added (I-5).
I-1 and I-4 are two vocabularies that were each written correctly and never checked against each other.
All six are local, mechanical, and covered by the tests proposed above.

Two items are not the implementer's to settle. S-1 (the structured statement-support review is absent,
and the brief says "plus") and W-1 (no local check that a ranking has an evidenced basis) are
plan-text-versus-implementation questions; per the SDD process they go to the human with the plan text
beside them, and the recommendation above is to defer S-1 explicitly with a recorded ruling rather than
to let it pass unremarked. S-2 is the implementer's to fix.

**Suggested fix order for whoever picks this up:** I-3 first (it changes what `_corpus_tokens` returns
and both I-2 and I-4 measure against it), then I-2, I-4, I-5, S-2, and I-6 last as a pure refactor
verified by the suite staying green. The Minors can ride along in the same round; M-1, M-2 and M-3 are
the three worth not deferring.
