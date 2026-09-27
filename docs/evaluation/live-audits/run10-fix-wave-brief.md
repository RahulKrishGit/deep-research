# Run-10 fix wave: generalization (P4 scored 69)

The head that scored 84.5 on the development question (P6, "Why did the Roman Republic fall?") scored **69** on the first new question: P4, "What obligations does the EU AI Act place on providers of general-purpose AI models, and when do they apply?" The audit is `audit-run10-fable.md` in this directory; read the defect table, the attribution table and the **Generalization** appendix before coding. By the user's rule this is a generalization problem, so every fix must be general: it must work for any question in any domain and never name this question's subject.

**Root causes.** Several rules written for P6 over-reached or were shape-specific:
1. **Planner (material D1).** The planner turned "obligations" into targets for Articles 53 and 55, made Article 54 optional and left Article 52 out, working from its own knowledge. No rule stops that on non-why questions, so the report never states the Article 52 notification duty, although twelve findings recorded it.
2. **Credit guard (D3, D5).** It refused seven legitimate points: instrument credits like "Article 3(63) defines …", and "The Commission states …", which the "the X states" exclusion strips.
3. **Dissent re-ask (D2).** "however" alone anchored a candidate, so a source's own proviso became a "dispute" and went into the bottom line with "but".
4. **Writer housekeeping rule (D4).** It hid a reproduced provision's "amended, not yet updated" notice, which is the currency of a "when" answer.
5. **Nothing catches an omitted item before acceptance (D1's safety net).**
6. **`document_reader` (D6)** fails on HTML ("unsupported document format"), so the official text was never read.

## Shared constraints (every slice)

**Where to work**
- Worktree: `<fix worktree>`, branch `ev/t9-format`, head f27ac7e.
- Touch only your slice's files; four slices run at once. Report anything outside your files instead of editing it.
- Never commit, stash or `git add -A`. Leave changes uncommitted.
- Temp files go under %TEMP% and are deleted afterwards. Never read `.env`. Make no live provider calls.

**Environment**
- Python: `<venv python>`, with `PYTHONPATH='src;.'`.
- Your `edit` tool misbehaves. Use `write` for new files, or a Python replacement script that asserts the old text occurs exactly once.

**Tests**
- Test first: RED, then GREEN, for every behaviour change.
- Behaviour tests only. **No wording-pin tests** (asserting a sentence is in a prompt); Main deletes them. Prompt text is covered by Fable's prompt review.
- Run your slice's test files, the full suite (`python -m pytest -q -p no:cacheprovider`), and the controlled e2e suite (`python -m deep_research.e2e_evaluation suite --tier controlled --repetitions 3`, 35/35).
- Prompt fingerprint pins will move; Main re-pins them.

**Model-read text**
- General for any question and domain (D11).
- Never names an audited or benchmark subject (D10): no EU, AI Act, Article 52/53/54/55, Commission, general-purpose AI, Rome, Roman, Republic, headphones, games, drugs, companies, climate or programming languages.
- Examples use neutral material: "Example Institute", "example-register.test", "a 2019 regulation", "Section 4 of the Example Act".

**Honesty rules (inviolable)**
- No relay presented as the issuer.
- No invented date, scope or provenance.
- Every number traceable.
- Forecasts keep issuer and release.
- No verdict, pick or ranking in the report's own voice.

**Report back:**
- files changed;
- each new test with its RED evidence;
- the commands you ran and their results;
- any deviation, with the reason.

## Slice G1: planner (FixSweepR5), `src/deep_research/agents/planner.py`

Fable's appendix §2.4 and §3.1 apply here.
- Add a **general** required-target rule to `PLAN_INSTRUCTION`, not a rules-specific one. Place it next to the existing why/how sentence, which stays: "Whatever the question asks for (its reasons, the rules or obligations that apply, the items it asks to compare, the figures it asks for), that is the required target, in the question's own words, and it covers everything of that kind the sources state about the question's subject. A provision, section, article, chapter, body, document, episode, product or example you name in order to find it is a lead for a search query or an optional target: never the required target's boundary or measure, and never a sub-topic's title."
- Keep the run-8 why/how optional-target sentence.
- Prompt-only. No test beyond the existing suite.

## Slice G2: dissent relation (FixSweepR5, after G1), `src/deep_research/agents/researcher.py`, `src/deep_research/utils/types.py`, tests

Fable's fix §3.4 applies here.
1. **Cue.**
   - A connective never anchors a dissent candidate by itself: "however", "in fact", "but", "yet", "although", "though".
   - A passage qualifies only when it carries at least one non-connective cue from `_DISSENT_CUE`: challenged, rejected, disputed, contested, little or no evidence, revised, and so on.
   - Split the regex so connectives are a secondary set, counted only alongside a primary cue.
   - Test: a proviso passage whose only cue is "however" is not selected; "However, later surveys found little evidence …" is.
2. **Relation.**
   - The dissent packet asks for each returned finding's relation to the statement it concerns: `disputes`, `qualifies` or `dates`.
   - Carry it as an optional `dissent_relation` field on the finding draft the packet returns, and on `Finding` (default None, so old snapshots load).
   - Only `disputes` sets `Finding.disputes = True`. A `qualifies` or `dates` finding is kept as an ordinary finding bound to the statement's targets, with no dispute mark.
   - Update the packet's two sentences so the model knows to set the relation. The model-read text goes to Fable's review.
   - Tests:
     - a `qualifies` reply is kept, with `disputes` False;
     - a `disputes` reply sets it;
     - a missing relation is treated as `qualifies`, the conservative default;
     - old snapshots load.

## Slice G3: writer (FixWriterR4), `src/deep_research/agents/report_writer.py`, `tests/test_agents/test_report_writer.py`

Fable's attribution for D3, D4 and D5, and fixes §3.1 (writer half), §3.2 and §3.3, apply here.
1. **An instrument reference credits (code).** A point credits a source when it names an instrument or provision reference that its cited finding's own text carries, e.g.:
   - "Article 3(63) defines …", "Section 4 of the Example Act requires …";
   - "Under Annex XIII …", "Regulation (EU) …", "the Act provides …".

   The reference is the subject of a verb, or sits in "under <ref>" or "<ref> of the <Instrument>". Accept these verbs: requires, defines, provides, sets, obliges, prohibits, states, lays down, applies, establishes, specifies, grants, allows. The reference must appear in a cited finding's content or snippet, compared casefolded with whitespace normalised.
   - Tests:
     - "Article 3(63) defines a model as …" is kept when the finding's content carries "Article 3(63)";
     - it is refused when no cited finding carries that reference and nothing else credits;
     - the run-7 probe still refuses S043: 46 statements, 45 kept, 1 refused.
2. **Polity exclusion (code).** `_MULTIWORD_NAME_EXCLUSION` currently strips "the <Capitalised> state(s)", which deletes "The Commission states …". Make it a fixed list of polity phrases: "United States", "Member States"/"member states", and lowercase "the state"/"the states" when not followed by "that".
   - Tests:
     - "The Commission states that …" credits;
     - "The United States added …" still refuses with no named source;
     - "The Roman state …" wording is replaced by a neutral equivalent ("The early state collapsed …"), which does not credit.
3. **Item coverage (prompt).** Add to the section coverage rule: "A listed finding that states an item the question asks for (a duty, condition, exception or deadline a rule places on the question's subject; a cause; a step) is stated even when its target's question names a narrower item or a different provision."
4. **Currency (prompt).** Add an exception to the housekeeping rule: "A reproduced text's own notice that it has been amended, superseded or not yet updated is stated with the provision it qualifies. A date a source gives in the future tense that is already past the report's as-of date is stated as past, or dated to its source."
5. Keep every earlier guard's behaviour: disputes, exclusive hop, outcome and floor.

## Slice G4: reviewer acceptance net (FixReviewerG, new), `src/deep_research/agents/report_reviewer.py`, `tests/test_agents/test_report_reviewer.py`

Fable's CODE 1 and fix §3.1 (acceptance) apply here.
1. **Packet.** The review packet gains a section, `# Findings no statement cites`. It lists every finding that meets all four conditions:
   - (a) it is `verified` or kept by the Evidence Verifier;
   - (b) it is bound to a **required** target;
   - (c) its source's authority is above `agents.writer_authority_floor`;
   - (d) no reader statement cites it.

   Order the list by authority, descending, and bound it at 20 as a runaway guard. Each entry shows its label, source line and content. Read what the packet builder already has: composition, findings, sources and targets.
2. **Rule.** Add to the reviewer instruction: "When a finding under 'Findings no statement cites' states an item the question asks for that no reader statement states (a duty, condition, deadline, cause, step or figure), raise a material `coverage` defect naming that finding and its target. A finding that restates an item a reader statement already states is not a defect."
3. **Routing.** The existing defect routing sends a target-scoped defect to its part's redraft. Verify, and test, that such a defect reaches the part's redraft and that the redraft request lists the finding.
4. **Version.** Bump `REPORT_REVIEW_PROMPT_VERSION` to `report-review-8`. Main updates the pin in `tests/test_evaluation/test_config.py`.
5. **Tests:**
   - the packet lists an uncited, above-floor, required-target finding;
   - it excludes cited, below-floor, optional-target and unverified findings;
   - the bound holds;
   - a defect naming such a finding reaches the redraft.

## Slice G5: HTML in the document reader (FixTitlesR4), `src/deep_research/tools/document_reader.py` and, only if needed, `web_scraper.py`, plus tests

Fable's D6 row applies here.
- When the remote response's media type is `text/html` or `application/xhtml+xml`, the document reader must not fail with "unsupported document format".
- Hand the body to the web scraper's own HTML extraction (the same title, text and passage logic a scrape uses) and return a normal read, with the served URL and the title fallback. That way an official register that serves HTML to a suffix-less document URL is read.
- Keep every existing format path identical.
- Tests: an HTML response to the document reader yields a read with its text and title; a PDF, CSV or JSON response is unchanged; a truly unsupported type (for example `application/zip`) still fails as before.
