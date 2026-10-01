// @vitest-environment node — each step's own brief (notes-progress-report spec §6.3-§6.7), derived from
// synthesized events: pure functions of RunState and a clock.
import { describe, expect, it } from "vitest";
import {
  REVIEW_CRITERIA, elapsedText, evaluatingBody, issueText, liveSubtitle, notesClause, planningSlots, planningStatus,
  reviewVerdict, reviewingBody, sourceWords, verifierVerdict, verifyTallyText, verifyingBody, writerVerdict, writingBody,
  writingTallyText,
} from "../lib/briefs";
import { applyEvent, newRunState, type RunEvent, type RunState, type VerifierSample } from "../lib/run-state";

/* A fixed clock, ten minutes after every synthesized event's base time, so elapsed times are exact. */
const NOW = Date.UTC(2026, 8, 30, 12, 10, 0);
const at = (s: number) => new Date(Date.UTC(2026, 8, 30, 12, 0, s)).toISOString();
const ev = (type: string, metadata: Record<string, unknown> = {}, timestamp?: string): RunEvent => ({ type, metadata, timestamp });
function play(events: RunEvent[]): RunState { const run = newRunState(); for (const e of events) applyEvent(run, e); return run; }
const shown = (stack: { texts: string[]; on: number }) => stack.texts[stack.on];
const note = (id: string, restatement: string, kinds: string[], replaces: string | null = null) => [
  ev("session.note.received", { note_id: id, text: restatement }),
  ev("session.note.interpreted", { note_id: id, restatement, kinds, replaces, fallback: false }),
];

describe("elapsed times", () => {
  it("reads Xm SSs from a start to a clock, and nothing without either", () => {
    expect(elapsedText(at(0), Date.parse(at(442)))).toBe("7m 22s");
    expect(elapsedText(undefined, NOW)).toBeNull();
    expect(elapsedText(at(0), null)).toBeNull();
  });
});

describe("Planning (spec §6.3)", () => {
  const started = ev("graph.node.started", { node: "planner", iteration: 0 }, at(0));
  const slot = (coverage_id: string, title: string, state: string) => ({ coverage_id, title, state });
  it("reads the question, then shows four skeleton slots that say 'drafting' while the plan is drafted", () => {
    const run = play([started]);
    expect(shown(planningStatus(run))).toBe("Reading your question…");
    expect(liveSubtitle(run, "planner", NOW)).toBe("10m 00s");
    applyEvent(run, ev("planner.progress", { step: "drafting", check_round: 0, sub_topics: [] }));
    expect(shown(planningStatus(run))).toBe("Drafting a plan for your question…");
    expect(planningSlots(run, false).map((s) => [s.title, s.width, s.mark, s.fact, s.gone])).toEqual([
      [null, "78%", "waiting", "drafting", false], [null, "64%", "waiting", "drafting", false],
      [null, "72%", "waiting", "drafting", false], [null, "52%", "waiting", "drafting", false],
    ]);
  });
  it("says 'and your answers' when the one-time check gave answers", () => {
    const run = play([
      ev("session.clarification.answered", { reason: "answered", answers: [{ question_id: "q1", value: "Global", source: "chosen" }] }),
      started,
    ]);
    expect(shown(planningStatus(run))).toBe("Reading your question and your answers…");
  });
  it("fills slots with titles (a surplus skeleton fades out), checks, fixes, and reads the final states", () => {
    const run = play([started, ev("planner.progress", { step: "checking", check_round: 1, sub_topics: [slot("topic-01", "Alpha", "checking"), slot("topic-02", "Beta", "checking"), slot("topic-03", "Gamma", "checking")] })]);
    expect(shown(planningStatus(run))).toBe("Checking the plan covers everything you asked…");
    expect(planningSlots(run, false).map((s) => [s.n, s.title, s.mark, s.fact, s.gone])).toEqual([
      [1, "Alpha", "running", "checking", false], [2, "Beta", "running", "checking", false],
      [3, "Gamma", "running", "checking", false], [4, null, "waiting", "", true],
    ]);
    applyEvent(run, ev("planner.progress", { step: "fixing", check_round: 1, sub_topics: [slot("topic-01", "Alpha", "passed"), slot("topic-02", "Beta", "being_fixed"), slot("topic-03", "Gamma", "passed")] }));
    expect(shown(planningStatus(run))).toBe("Fixing 1 topic the check flagged…");
    expect(planningSlots(run, false).slice(0, 3).map((s) => [s.mark, s.fact])).toEqual([["done", ""], ["running", "being fixed"], ["done", ""]]);
    applyEvent(run, ev("planner.progress", { step: "checking", check_round: 2, sub_topics: [slot("topic-01", "Alpha", "checking")] }));
    expect(shown(planningStatus(run))).toBe("Checking the fixed plan…");
    applyEvent(run, ev("planner.planning.completed", { sub_topic_count: 3, sub_topics: [slot("topic-01", "Alpha", "passed"), slot("topic-02", "Beta", "fixed"), slot("topic-03", "Gamma", "flagged")] }));
    expect(shown(planningStatus(run))).toBe("Plan ready · research starts now");
    expect(planningSlots(run, false).slice(0, 3).map((s) => [s.mark, s.fact])).toEqual([["done", ""], ["done", "fixed"], ["fail", "still flagged"]]);
  });
  it("says 'Fixing what the check found…' when a repair names no topic, and 'not checked' with no verdict", () => {
    const run = play([started, ev("planner.progress", { step: "fixing", check_round: 0, sub_topics: [slot("topic-01", "Alpha", "drafted")] })]);
    expect(shown(planningStatus(run))).toBe("Fixing what the check found…");
    applyEvent(run, ev("planner.planning.completed", { sub_topic_count: 1, sub_topics: [slot("topic-01", "Alpha", "not_checked")] }));
    expect(planningSlots(run, false)[0]).toMatchObject({ mark: "waiting", fact: "not checked" });
  });
  it("gives a research note its own slot: 'joins the plan', then 'from your note' (D2)", () => {
    const run = play([started, ...note("n1", "pastries at the cafés", ["new_angle"])]);
    expect(planningSlots(run, false).at(-1)).toMatchObject({ key: "note-n1", n: 5, title: "Your note: pastries at the cafés", mark: "waiting", fact: "joins the plan", rise: true });
    applyEvent(run, ev("planner.planning.completed", { sub_topic_count: 2, note_topic_count: 1, sub_topics: [slot("topic-01", "Alpha", "passed"), { coverage_id: "note-n1", title: "Your note: pastries at the cafés", note_id: "n1", state: "planned" }] }));
    expect(planningSlots(run, false).at(-1)).toMatchObject({ n: 2, mark: "done", fact: "from your note" });
  });
  it("reads a slot still running at the stop 'stopped', with the ring (§8.5)", () => {
    const run = play([started, ev("planner.progress", { step: "checking", check_round: 1, sub_topics: [slot("topic-01", "Alpha", "checking")] })]);
    expect(planningSlots(run, true)[0]).toMatchObject({ mark: "stopped", fact: "stopped" });
  });
});

describe("Evaluating sources (spec §6.4)", () => {
  const progress = (md: Record<string, number>) => ev("source_evaluator.progress", { to_rate: 44, reused: 0, capped: 0, rated: 0, strong: 0, fair: 0, weak: 0, unrated: 0, batches: 4, batches_done: 0, ...md });
  it("says 'not yet' until the first batch lands, then the split, with a bar of (rated + unrated) / to_rate", () => {
    const run = play([progress({})]);
    expect(evaluatingBody(run)).toMatchObject({ lead: "Rating 44 sources for trustworthiness and relevance", stats: { rated: null, toRate: 44, strong: null, fair: null, weak: null } });
    expect(liveSubtitle(run, "source_evaluator", NOW)).toBe("starting · not yet rated");
    applyEvent(run, progress({ rated: 10, strong: 5, fair: 4, weak: 1, unrated: 2, batches_done: 1 }));
    const after = evaluatingBody(run);
    expect(after.stats).toEqual({ rated: 10, toRate: 44, strong: 5, fair: 4, weak: 1 });
    expect(after.bar).toBeCloseTo(12 / 44);
    expect(liveSubtitle(run, "source_evaluator", NOW)).toBe("10 of 44 rated");
  });
  it("names reused sources, and has no bar or split with nothing new to rate", () => {
    expect(evaluatingBody(play([progress({ to_rate: 1, reused: 3 })])).lead).toBe("Rating 1 new source · 3 already rated");
    const none = play([progress({ to_rate: 0, reused: 3, batches: 0 })]);
    expect(evaluatingBody(none)).toEqual({ kind: "evaluating", lead: "No new sources to rate", bar: null, stats: null });
    expect(liveSubtitle(none, "source_evaluator", NOW)).toBe("nothing new to rate");
  });
});

describe("Verifying evidence (spec §6.5)", () => {
  const s = (over: Partial<VerifierSample>): VerifierSample => ({ seq: 1, text: "T", verdict: "verified", correction: null, dropReason: null, role: null, host: "eia.gov", ...over });
  it("words every verdict, the corrected ones by what the page changed and the dropped ones by why", () => {
    expect(verifierVerdict(s({}))).toBe("verified");
    expect(verifierVerdict(s({ verdict: "quoted" }))).toBe("quoted as written");
    const corrected = (field: string, value: string | null) => verifierVerdict(s({ verdict: "verified_corrected", correction: { field, value } }));
    expect(corrected("period", "2025")).toBe("corrected — the page dates it 2025");
    expect(corrected("period_cleared", null)).toBe("corrected — the page states no period for it");
    expect(corrected("scope", "all segments")).toBe("corrected — the page says it covers all segments");
    expect(corrected("subject", "Model A")).toBe("corrected — the page says it is about Model A");
    expect(corrected("kind", "forecast")).toBe("corrected — the page states it as a forecast");
    expect(corrected("kind", "actual")).toBe("corrected — the page states it as an actual");
    expect(corrected("figure", null)).toBe("corrected — one of its figures was not on the page");
    expect(verifierVerdict(s({ verdict: "verified_corrected" }))).toBe("corrected");
    const dropped = (reason: string) => verifierVerdict(s({ verdict: "dropped", dropReason: reason }));
    expect([dropped("read_not_found"), dropped("snippet_not_on_page"), dropped("evidence_not_on_page"), dropped("correction_not_on_page"), dropped("context_rejected"), dropped("context_unavailable")]).toEqual([
      "dropped — the page could not be read again", "dropped — the page does not say this", "dropped — the page does not show this figure",
      "dropped — the page does not back its date or scope", "dropped — the page's context does not support it", "dropped — its figures could not be checked",
    ]);
  });
  it("names the source by its role, else its host", () => {
    expect([sourceWords(s({ role: "original_report" })), sourceWords(s({ role: "independent_research" })), sourceWords(s({ role: "derivative" })),
      sourceWords(s({ role: "company_statement" })), sourceWords(s({ role: "mixed" })), sourceWords(s({ role: null, host: null }))])
      .toEqual(["an original report", "independent research", "a round-up of other sources", "the business's own words", "eia.gov", null]);
  });
  it("shows the last two samples, quotes a quoted one, marks a dropped one, and tallies", () => {
    const sample = (t: string, verdict: string) => ({ text: t, verdict, correction: null, drop_reason: verdict === "dropped" ? "snippet_not_on_page" : null, source: { role: "derivative", host: "x.org" } });
    const tally = { total: 10, checked: 4, verified: 2, corrected: 1, quoted: 0, dropped: 1, batches: 2, batches_done: 1 };
    const run = play([ev("evidence_verifier.progress", { ...tally, sample: sample("A", "quoted") }), ev("evidence_verifier.progress", { ...tally, sample: sample("B", "dropped") })]);
    const v = verifyingBody(run);
    expect(v.samples.map((l) => [l.key, l.text, l.quoted, l.kept, l.where])).toEqual([["v1", "A", true, true, "a round-up of other sources"], ["v2", "B", false, false, "a round-up of other sources"]]);
    expect(v.bar).toBe(0.4);
    expect(verifyTallyText(v.tally!)).toBe("4 of 10 checked · 2 verified · 1 corrected · 1 dropped");
    expect(liveSubtitle(run, "evidence_verifier", NOW)).toBe("4 of 10 checked");
  });
  it("keys a sample by the pass that made it: sequence numbers restart when the step starts again", () => {
    const sample = { text: "A", verdict: "verified", correction: null, drop_reason: null, source: { role: null, host: "x.org" } };
    const tally = { total: 4, checked: 1, verified: 1, corrected: 0, quoted: 0, dropped: 0, batches: 1, batches_done: 1, sample };
    const run = play([ev("graph.node.started", { node: "evidence_verifier", iteration: 0 }, at(0)), ev("evidence_verifier.progress", tally)]);
    const first = verifyingBody(run).samples[0].key;
    applyEvent(run, ev("graph.node.started", { node: "evidence_verifier", iteration: 1 }, at(300)));
    expect(verifyingBody(run).samples).toEqual([]);
    applyEvent(run, ev("evidence_verifier.progress", tally));
    expect(verifyingBody(run).samples[0].key).not.toBe(first);
    const written = { phase: "sections", parts_total: 1, parts_returned: 1, sentences_drafted: 1, sentences_checked: 1, backed: 1, removed: 0, unchecked: 0, fraction: 0.5, sample: { text: "S.", verdict: "backed", findings: 1, section: "Where" } };
    const writer = play([ev("graph.node.started", { node: "report_writer", iteration: 0 }, at(0)), ev("report_writer.progress", written)]);
    const wfirst = writingBody(writer).samples[0].key;
    applyEvent(writer, ev("graph.node.started", { node: "report_writer", iteration: 1 }, at(300)));
    applyEvent(writer, ev("report_writer.progress", written));
    expect(writingBody(writer).samples[0].key).not.toBe(wfirst);
  });
  it("waits on its placeholder before the first event, and says 'No findings to check' with none", () => {
    expect(verifyingBody(newRunState())).toEqual({ kind: "verifying", empty: null, samples: [], bar: 0, tally: null });
    expect(liveSubtitle(newRunState(), "evidence_verifier", NOW)).toBe("starting · not yet checked");
    const none = play([ev("evidence_verifier.progress", { total: 0, checked: 0, verified: 0, corrected: 0, quoted: 0, dropped: 0, batches: 0, batches_done: 0, sample: null })]);
    expect(verifyingBody(none).empty).toBe("No findings to check");
  });
});

describe("Writing report (spec §6.6)", () => {
  it("words a backed and a removed sentence", () => {
    expect(writerVerdict({ seq: 1, text: "T", verdict: "backed", findings: 1, section: "S" })).toBe("✓ backed by 1 finding");
    expect(writerVerdict({ seq: 1, text: "T", verdict: "backed", findings: 3, section: "S" })).toBe("✓ backed by 3 findings");
    expect(writerVerdict({ seq: 1, text: "T", verdict: "removed", findings: 2, section: "S" })).toBe("✗ removed — no verified finding says this");
  });
  it("tallies sentences as sections return, then says it is writing the bottom line", () => {
    const counts = { parts_total: 5, parts_returned: 2, sentences_drafted: 12, sentences_checked: 9, backed: 8, removed: 1, unchecked: 0, fraction: 0.3 };
    const run = play([ev("report_writer.progress", { phase: "sections", ...counts, sample: { text: "S.", verdict: "backed", findings: 2, section: "Where" } })]);
    const w = writingBody(run);
    expect(writingTallyText(w.tally!)).toBe("9 of 12 sentences checked · ✓ 8 backed · ✗ 1 removed · section 2 of 5");
    expect(w.samples[0]).toMatchObject({ verdict: "✓ backed by 2 findings", kept: true, where: "Where" });
    expect(liveSubtitle(run, "report_writer", NOW)).toBe("2 of 5 sections written");
    applyEvent(run, ev("report_writer.progress", { phase: "bottom_line", ...counts, unchecked: 2, sample: null }));
    expect(liveSubtitle(run, "report_writer", NOW)).toBe("writing the bottom line");
    expect(writingTallyText(writingBody(run).tally!)).toBe("9 of 12 sentences checked · ✓ 8 backed · ✗ 1 removed · section 2 of 5 · 2 not checked");
  });
  it("waits on 'The first section is being drafted…', then, once a section has returned, on 'The first sentences are being checked…'", () => {
    expect(writingBody(newRunState()).placeholder).toBe("The first section is being drafted…");
    const counts = { parts_total: 2, sentences_checked: 0, backed: 0, removed: 0, unchecked: 0, fraction: 0, sample: null };
    const none = play([ev("report_writer.progress", { phase: "sections", parts_returned: 0, sentences_drafted: 0, ...counts })]);
    expect(writingBody(none).placeholder).toBe("The first section is being drafted…");
    const returned = play([ev("report_writer.progress", { phase: "sections", parts_returned: 2, sentences_drafted: 4, ...counts })]);
    expect(writingBody(returned).placeholder).toBe("The first sentences are being checked…");
  });
  it("shows no tally before a sentence is drafted, and no section clause with no section to rewrite", () => {
    const run = play([ev("report_writer.progress", { phase: "sections", parts_total: 0, parts_returned: 0, sentences_drafted: 0, sentences_checked: 0, backed: 0, removed: 0, unchecked: 0, fraction: 0, sample: null })]);
    expect(writingBody(run).tally).toBeNull();
    expect(liveSubtitle(run, "report_writer", NOW)).toBe("no section to rewrite");
    expect(writingTallyText({ checked: 1, drafted: 1, backed: 1, removed: 0, unchecked: 0, partsReturned: 0, partsTotal: 0 })).toBe("1 of 1 sentences checked · ✓ 1 backed · ✗ 0 removed");
  });
});

describe("Reviewing (spec §6.7)", () => {
  const crit = (failing: Record<string, string[]> = {}) => REVIEW_CRITERIA.map(({ dimension }) => ({ dimension, met: !failing[dimension], kinds: failing[dimension] ?? [] }));
  const start = ev("graph.node.started", { node: "report_reviewer", iteration: 0 }, at(0));
  const reviewed = (md: Record<string, unknown> = {}) => ev("graph.report.reviewed", { review_status: "scored", mean_score: 0.87, material_defects: 0, criteria: crit(), notes: [], ...md }, at(95));
  const decided = (reason: string, missing: string[] = []) => ev("graph.route.decided", { destination: "finalize", reason, missing_required_target_ids: missing });
  it("reads while the single call runs: five rings, the indeterminate bar, the elapsed time", () => {
    const run = play([start]);
    const r = reviewingBody(run, false);
    expect(shown(r.status)).toBe("Reading the draft as a critical reader would · usually 1–3 min");
    expect(r.waiting).toBe(true);
    expect(r.criteria.map((c) => [c.text, c.mark, c.before, c.landed])).toEqual(REVIEW_CRITERIA.map((c) => [c.label, "waiting", "reading", false]));
    expect(liveSubtitle(run, "report_reviewer", NOW)).toBe("reading the draft · 10m 00s");
  });
  it("lands the checks with the issue in plain words -- no score anywhere -- and freezes the elapsed time", () => {
    const run = play([start, reviewed({ material_defects: 3, criteria: crit({ completeness: ["coverage"], uncertainty: ["contradiction", "contradiction"] }) })]);
    const r = reviewingBody(run, false);
    expect(r.waiting).toBe(false);
    expect(shown(r.status)).toBe("Review done · deciding what happens next…");
    expect(r.criteria.map((c) => [c.mark, c.fact])).toEqual([
      ["fail", "a part of your question has no answer"], ["done", ""], ["done", ""],
      ["fail", "2 issues · sources disagree and the draft does not say so"], ["done", ""],
    ]);
    expect(JSON.stringify(r)).not.toMatch(/0\.87/);
    expect(liveSubtitle(run, "report_reviewer", NOW)).toBe("reading the draft · 1m 35s");
  });
  it("reads 'not checked' for every criterion of a review that was not scored", () => {
    const run = play([start, reviewed({ review_status: "provider_failed", criteria: REVIEW_CRITERIA.map(({ dimension }) => ({ dimension, met: null, kinds: [] })) })]);
    expect(reviewingBody(run, false).criteria.every((c) => c.mark === "waiting" && c.fact === "not checked")).toBe(true);
  });
  it("lists the notes with their results: a mixed note with both, an unnamed research note 'researched next'", () => {
    const run = play([
      ...note("n1", "pastries at the cafés", ["new_angle"]),
      ...note("n2", "pastries, but skip closed cafés", ["new_angle", "exclude"]),
      ...note("n3", "more on safety", ["emphasis"]),
      ...note("n4", "a later research note", ["new_angle"]),
      ...note("n5", "a later steering note", ["scope"]),
      ...note("n6", "replaced", ["emphasis"]), ...note("n7", "the replacement", ["emphasis"], "n6"),
      start,
      reviewed({ notes: [
        { note_id: "n1", result: "met", reason: "covered" },
        { note_id: "n2", result: "met", reason: "covered", steering: { result: "not_met", reason: "ignored_with_evidence" } },
        { note_id: "n3", result: "met", reason: "honoured" },
        { note_id: "n7", result: "not_met", reason: "no_evidence" },
      ] }),
    ]);
    expect(reviewingBody(run, false).notes.map((n) => [n.text, n.mark, n.fact])).toEqual([
      ["Pastries at the cafés", "done", "covered"],
      ["Pastries, but skip closed cafés", "fail", "covered · not followed"],
      ["More on safety", "done", "honoured"],
      ["A later research note", "waiting", "researched next"],
      ["A later steering note", "waiting", "not checked"],
      ["The replacement", "fail", "no evidence found"],
    ]);
  });
  it("words the verdict line per route, with the notes clause on acceptance", () => {
    const verdict = (events: RunEvent[]) => reviewVerdict(play(events));
    expect(verdict([start, reviewed()])).toBeNull();
    expect(verdict([start, reviewed(), decided("report_accepted")])).toBe("Accepted · all 5 criteria met");
    const n1 = note("n1", "a", ["emphasis"]);
    expect(verdict([...n1, start, reviewed({ notes: [{ note_id: "n1", result: "met", reason: "honoured" }] }), decided("report_accepted")])).toBe("Accepted · all 5 criteria met · your note met");
    expect(verdict([...n1, start, reviewed({ notes: [{ note_id: "n1", result: "not_met", reason: "no_evidence" }] }), decided("report_accepted")])).toBe("Accepted · all 5 criteria met · your note not met");
    expect(verdict([start, reviewed({ material_defects: 1 }), decided("redraft_requested")])).toBe("1 thing to fix · sending the draft back to the writer");
    expect(verdict([start, reviewed({ material_defects: 3 }), decided("redraft_requested")])).toBe("3 things to fix · sending the draft back to the writer");
    // A review that sent no defect count leaves the count out: an unknown value never reads as "0 things" (D19).
    expect(verdict([start, reviewed({ material_defects: null }), decided("redraft_requested")])).toBe("Things to fix · sending the draft back to the writer");
    expect(verdict([start, reviewed(), decided("extra_pass_requested", ["a", "b"])])).toBe("2 gaps to fill · going back to research");
    expect(verdict([...n1, start, reviewed(), decided("note_pass_requested")])).toBe("Going back to research your note");
    expect(verdict([...n1, start, reviewed(), decided("note_redraft_requested")])).toBe("Sending the draft back to the writer for your note");
    expect(verdict([start, reviewed({ review_status: "provider_failed" }), decided("review_unavailable")])).toBe("The review could not be completed · publishing as partial");
    expect(verdict([start, reviewed({ criteria: crit({ readability: ["presentation"] }) }), decided("report_not_accepted")])).toBe("Not accepted · 4 of 5 met");
    expect(verdict([ev("graph.quality.assessed", { hard_failures: ["missing_evidence_ledger"] }), start, reviewed(), decided("report_not_accepted")])).toBe("Not accepted · a check the run makes itself failed");
    expect(verdict([start, reviewed(), decided("report_not_accepted")])).toBe("Not accepted · the reviewer's overall judgement fell short");
    expect(verdict([start, reviewed(), decided("extra_passes_exhausted", ["a"])])).toBe("Not accepted · 1 gap still open");
    expect(shown(reviewingBody(play([start, reviewed(), decided("report_accepted")]), false).status)).toBe("Accepted · all 5 criteria met");
  });
  it("counts notes for the clause: both, all, some", () => {
    expect([notesClause(0, 0), notesClause(2, 2), notesClause(3, 3), notesClause(1, 3), notesClause(0, 2)])
      .toEqual(["", " · both your notes met", " · all 3 of your notes met", " · 1 of your 3 notes met", " · 0 of your 2 notes met"]);
    expect(issueText(["identity"])).toBe("a source is credited to the wrong publisher");
  });
  it("freezes on 'stopped' when the reader stopped the run before the review landed", () => {
    const r = reviewingBody(play([start]), true);
    expect(r.waiting).toBe(false);
    expect(r.criteria[0]).toMatchObject({ mark: "waiting", before: "stopped", landed: false });
  });
});
