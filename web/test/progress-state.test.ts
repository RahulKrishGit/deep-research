// @vitest-environment node — pure state over synthesized events (notes-progress-report spec §6.9).
import { describe, expect, it } from "vitest";
import { applyEvent, newRunState, notAcceptedLine, type RunEvent, type RunState } from "../lib/run-state";

const at = (s: number) => new Date(Date.UTC(2026, 8, 30, 12, 0, s)).toISOString();
const ev = (type: string, metadata: Record<string, unknown> = {}, timestamp?: string): RunEvent => ({ type, metadata, timestamp });
function play(events: RunEvent[]): RunState {
  const run = newRunState();
  for (const e of events) applyEvent(run, e);
  return run;
}
/* The state after each event equals a fresh replay of events 1..k (DESIGN.md §5.7). */
function burstSafe(events: RunEvent[]): void {
  const run = newRunState();
  events.forEach((e, k) => { applyEvent(run, e); expect(play(events.slice(0, k + 1))).toEqual(structuredClone(run)); });
}
const slot = (coverage_id: string, title: string, state: string) => ({ coverage_id, title, state });

describe("timestamps (spec §4 item 4)", () => {
  it("keeps each row's start and, at its completion, its duration; a restart clears the duration", () => {
    const run = play([
      ev("graph.node.started", { node: "planner", iteration: 0 }, at(0)),
      ev("graph.node.completed", { node: "planner" }, at(83)),
    ]);
    expect(run.startedAt.planner).toBe(at(0));
    expect(run.durations.planner).toBe(83);
    applyEvent(run, ev("graph.node.started", { node: "planner", iteration: 0 }, at(90)));
    expect(run.durations.planner).toBeUndefined();
    applyEvent(run, ev("graph.node.started", { node: "extra_pass", iteration: 1 }, at(91)));
    expect(run.startedAt).not.toHaveProperty("extra_pass");
  });
  it("records no time for an event that carries none", () => {
    const run = play([ev("graph.node.started", { node: "researcher", iteration: 0 }), ev("graph.node.completed", { node: "researcher" })]);
    expect(run.startedAt).toEqual({});
    expect(run.durations).toEqual({});
  });
  it("keeps a looped reviewer's duration although its completion moves no row", () => {
    const run = play([
      ev("graph.node.started", { node: "report_reviewer", iteration: 0 }, at(0)),
      ev("graph.route.decided", { destination: "extra_pass", reason: "extra_pass_requested", missing_required_target_ids: ["t"] }, at(40)),
      ev("graph.node.completed", { node: "report_reviewer" }, at(41)),
    ]);
    expect(run.durations.report_reviewer).toBe(41);
    expect(run.active).toBe("researcher");
  });
});

describe("Planning (spec §6.3)", () => {
  const plan = [slot("topic-01", "Alpha", "checking"), slot("topic-02", "Beta", "checking")];
  it("follows each planner.progress, then stamps the final states and the planned notes", () => {
    const run = play([
      ev("graph.node.started", { node: "planner", iteration: 0 }, at(0)),
      ev("planner.progress", { step: "drafting", check_round: 0, sub_topics: [] }),
    ]);
    expect(run.planning).toEqual({ step: "drafting", round: 0, slots: [], noteSlots: [] });
    applyEvent(run, ev("planner.progress", { step: "checking", check_round: 1, sub_topics: plan }));
    expect(run.planning.slots).toEqual([
      { coverageId: "topic-01", title: "Alpha", state: "checking" }, { coverageId: "topic-02", title: "Beta", state: "checking" },
    ]);
    applyEvent(run, ev("planner.planning.completed", {
      sub_topic_count: 3, note_topic_count: 1,
      sub_topics: [slot("topic-01", "Alpha", "fixed"), slot("topic-02", "Beta", "flagged"),
        { coverage_id: "note-n1", title: "Your note: pastries", note_id: "n1", state: "planned" }],
    }));
    expect(run.planning.step).toBe("ready");
    expect(run.planning.slots.map((s) => s.state)).toEqual(["fixed", "flagged"]);
    expect(run.planning.noteSlots).toEqual([{ noteId: "n1", title: "Your note: pastries", state: "planned" }]);
    expect(run.outcomes.planner).toBe("3 sub-topics · 1 from your note");
    expect(run.plan.map((p) => p.coverageId)).toEqual(["topic-01", "topic-02", "note-n1"]);
  });
  it("reads an unknown or missing slot state as drafted, and ignores a progress event with no known step", () => {
    const run = play([ev("planner.progress", { step: "checking", check_round: 1, sub_topics: [slot("topic-01", "Alpha", "pondering"), { coverage_id: "topic-02", title: "Beta" }] })]);
    expect(run.planning.slots.map((s) => s.state)).toEqual(["drafted", "drafted"]);
    applyEvent(run, ev("planner.progress", { step: "musing", sub_topics: [] }));
    expect(run.planning.step).toBe("checking");
  });
  it("gives a research note read while Planning runs its own slot, until the plan leaves it out", () => {
    const run = play([
      ev("graph.node.started", { node: "planner", iteration: 0 }, at(0)),
      ev("session.note.received", { note_id: "n1", text: "pastries too" }),
      ev("session.note.interpreted", { note_id: "n1", restatement: "pastries at the cafés", kinds: ["new_angle"], replaces: null, fallback: false }),
      ev("session.note.received", { note_id: "n2", text: "skip closed ones" }),
      ev("session.note.interpreted", { note_id: "n2", restatement: "leave out closed cafés", kinds: ["exclude"], replaces: null, fallback: false }),
    ]);
    expect(run.planning.noteSlots).toEqual([{ noteId: "n1", title: "Your note: pastries at the cafés", state: "pending" }]);
    applyEvent(run, ev("planner.planning.completed", { sub_topic_count: 1, sub_topics: [slot("topic-01", "Alpha", "passed")] }));
    expect(run.planning.noteSlots).toEqual([]);
  });
  it("gives no slot once Planning is not the running row, and drops a slot whose note is replaced", () => {
    const run = play([
      ev("graph.node.started", { node: "planner", iteration: 0 }, at(0)),
      ev("session.note.interpreted", { note_id: "n1", restatement: "pastries", kinds: ["new_angle"], replaces: null, fallback: false }),
      ev("session.note.interpreted", { note_id: "n2", restatement: "only pastries made in-house", kinds: ["new_angle"], replaces: "n1", fallback: false }),
    ]);
    expect(run.planning.noteSlots.map((s) => s.noteId)).toEqual(["n2"]);
    applyEvent(run, ev("graph.node.completed", { node: "planner" }, at(5)));
    applyEvent(run, ev("session.note.interpreted", { note_id: "n3", restatement: "seating", kinds: ["new_angle"], replaces: null, fallback: false }));
    expect(run.planning.noteSlots.map((s) => s.noteId)).toEqual(["n2"]);
  });
});

describe("a note's slot after the plan (Phase B Task 9, spec §6.3)", () => {
  const planned = [
    ev("graph.node.started", { node: "planner", iteration: 0 }, at(0)),
    ev("session.note.interpreted", { note_id: "n1", restatement: "pastries", kinds: ["new_angle"], replaces: null, fallback: false }),
    ev("planner.planning.completed", {
      sub_topic_count: 2, note_topic_count: 1,
      sub_topics: [slot("topic-01", "Alpha", "passed"), { coverage_id: "note-n1", title: "Your note: pastries", note_id: "n1", state: "planned" }],
    }),
  ];
  const replacement = ev("session.note.interpreted", { note_id: "n2", restatement: "only pastries made in-house", kinds: ["new_angle"], replaces: "n1", fallback: false });
  it("keeps a planned slot when a note replaces its note after planning, with Planning still the running row", () => {
    const run = play(planned);
    expect(run.active).toBe("planner");
    applyEvent(run, replacement);
    expect(run.planning.noteSlots).toEqual([{ noteId: "n1", title: "Your note: pastries", state: "planned" }]);
    // The replacing note adds no slot of its own: the plan is done.
    expect(run.notes.find((n) => n.id === "n2")!.replaces).toBe("n1");
  });
  it("keeps a planned slot when a note replaces its note after Planning has finished", () => {
    const run = play([...planned, ev("graph.node.completed", { node: "planner" }, at(60)), ev("graph.node.started", { node: "researcher", iteration: 0 }, at(61))]);
    expect(run.active).toBe("researcher");
    applyEvent(run, replacement);
    expect(run.planning.noteSlots).toEqual([{ noteId: "n1", title: "Your note: pastries", state: "planned" }]);
  });
});

describe("Evaluating, Verifying and Writing (spec §6.4-§6.6)", () => {
  it("keeps the latest evaluator counts and the split outcome", () => {
    const run = play([
      ev("graph.node.started", { node: "source_evaluator", iteration: 0 }, at(0)),
      ev("source_evaluator.progress", { to_rate: 5, reused: 1, capped: 0, rated: 2, strong: 1, fair: 1, weak: 0, unrated: 1, batches: 3, batches_done: 2 }),
      ev("source_evaluator.evaluation.completed", { source_count: 6, scored_count: 5, strong_count: 2, fair_count: 2, weak_count: 1, unscored_cap_count: 0, unscored_provider_count: 1, unscored_missing_count: 0 }),
    ]);
    expect(run.evaluating).toEqual({ toRate: 5, reused: 1, capped: 0, rated: 2, strong: 1, fair: 1, weak: 0, unrated: 1, batches: 3, batchesDone: 2 });
    expect(run.outcomes.source_evaluator).toBe("5 sources rated · 2 strong · 2 fair · 1 weak · 1 not rated");
    applyEvent(run, ev("graph.node.started", { node: "source_evaluator", iteration: 1 }, at(9)));
    expect(run.evaluating).toBeNull();
  });
  it("keeps the verifier's latest tally and its last two samples, numbered; a report with no sample keeps them", () => {
    const sample = (text: string) => ({ text, verdict: "dropped", correction: null, drop_reason: "snippet_not_on_page", source: { role: null, host: "eia.gov" } });
    const tally = { total: 4, checked: 3, verified: 0, corrected: 0, quoted: 1, dropped: 2, batches: 1, batches_done: 0 };
    const run = play([
      ev("evidence_verifier.progress", { ...tally, sample: sample("A") }),
      ev("evidence_verifier.progress", { ...tally, sample: sample("B") }),
      ev("evidence_verifier.progress", { ...tally, sample: sample("C") }),
      ev("evidence_verifier.progress", { ...tally, checked: 4, batches_done: 1, sample: null }),
    ]);
    expect(run.verifying!.checked).toBe(4);
    expect(run.verifying!.samples.map((s) => [s.seq, s.text])).toEqual([[2, "B"], [3, "C"]]);
    expect(run.verifying!.samples[1]).toEqual({ seq: 3, text: "C", verdict: "dropped", correction: null, dropReason: "snippet_not_on_page", role: null, host: "eia.gov" });
  });
  it("keeps the writer's latest counts, its phase and a fraction that never falls", () => {
    const counts = { parts_total: 2, parts_returned: 1, sentences_drafted: 2, sentences_checked: 1, backed: 1, removed: 0, unchecked: 0 };
    const run = play([
      ev("report_writer.progress", { ...counts, phase: "sections", fraction: 0.5, sample: { text: "S.", verdict: "backed", findings: 2, section: "Alpha" } }),
      ev("report_writer.progress", { ...counts, phase: "bottom_line", fraction: 0.4, sample: null }),
    ]);
    expect(run.writing!.phase).toBe("bottom_line");
    expect(run.writing!.fraction).toBe(0.5);
    expect(run.writing!.samples).toEqual([{ seq: 1, text: "S.", verdict: "backed", findings: 2, section: "Alpha" }]);
  });
});

describe("Reviewing (spec §6.7)", () => {
  const criteria = (failing: Record<string, string[]> = {}) => ["completeness", "evidence_quality", "attribution", "uncertainty", "readability"]
    .map((dimension) => ({ dimension, met: !failing[dimension], kinds: failing[dimension] ?? [] }));
  const review = (md: Record<string, unknown>) => [
    ev("graph.node.started", { node: "report_reviewer", iteration: 0 }, at(0)),
    ev("graph.report.reviewed", { review_status: "scored", mean_score: 0.9, material_defects: 0, criteria: criteria(), notes: [], ...md }, at(70)),
  ];
  it("keeps the criteria, the notes' results and when the review landed -- never the score", () => {
    const run = play(review({
      material_defects: 1, criteria: criteria({ uncertainty: ["contradiction"] }),
      notes: [{ note_id: "n1", result: "pending", reason: "to_research", steering: { result: "not_met", reason: "ignored_with_evidence" } }],
    }));
    expect(run.reviewing.landed).toBe(true);
    expect(run.reviewing.reviewedAt).toBe(at(70));
    expect(run.reviewing.defects).toBe(1);
    expect(run.reviewing.criteria![3]).toEqual({ dimension: "uncertainty", met: false, kinds: ["contradiction"] });
    expect(run.reviewing.notes).toEqual([{ noteId: "n1", result: "pending", reason: "to_research", steering: { result: "not_met", reason: "ignored_with_evidence" } }]);
  });
  it("words each route's outcome, with no score", () => {
    const decide = (reason: string, md: Record<string, unknown> = {}, extra: RunEvent[] = []) =>
      play([...extra, ...review(md), ev("graph.route.decided", { destination: "finalize", reason, missing_required_target_ids: ["a", "b"] })]).outcomes.report_reviewer;
    expect(decide("report_accepted")).toBe("Accepted · all 5 met");
    expect(decide("redraft_requested", { material_defects: 1 })).toBe("1 thing to fix · back to the writer");
    expect(decide("redraft_requested", { material_defects: 3 })).toBe("3 things to fix · back to the writer");
    expect(decide("redraft_requested", { material_defects: null })).toBe("Things to fix · back to the writer");
    expect(decide("extra_pass_requested")).toBe("Sent back to fill 2 gaps");
    expect(decide("review_unavailable")).toBe("Review unavailable");
    expect(decide("extra_passes_exhausted")).toBe("Not accepted · 2 gaps still open");
    expect(decide("report_not_accepted", { criteria: criteria({ completeness: ["coverage"], readability: ["presentation"] }) })).toBe("Not accepted · 3 of 5 met");
    expect(decide("report_not_accepted", {}, [ev("graph.quality.assessed", { hard_failures: ["missing_evidence_ledger"] })])).toBe("Not accepted · a check the run makes itself failed");
    expect(decide("report_not_accepted", {}, [ev("graph.quality.assessed", { hard_failures: [] })])).toBe("Not accepted · the reviewer's overall judgement fell short");
  });
  it("keeps the latest quality verdict's hard failures for the refusal line", () => {
    const run = play([ev("graph.quality.assessed", { hard_failures: ["a"] }), ev("graph.quality.assessed", { hard_failures: [] })]);
    expect(run.hardFailures).toEqual([]);
    expect(notAcceptedLine({ ...run, reviewing: { ...run.reviewing, criteria: [] } })).toBe("Not accepted · 0 of 5 met");
  });
  it("starts every review clean", () => {
    const run = play([...review({}), ev("graph.route.decided", { destination: "redraft", reason: "redraft_requested" }), ev("graph.node.started", { node: "report_reviewer", iteration: 0 }, at(99))]);
    expect(run.reviewing).toEqual({ landed: false, reviewedAt: null, criteria: null, notes: [], defects: null, reason: null, missing: 0 });
  });
});

describe("burst safety (AC19)", () => {
  it("paints the same state from any prefix of a synthesized run", () => {
    burstSafe([
      ev("graph.node.started", { node: "planner", iteration: 0 }, at(0)),
      ev("session.note.interpreted", { note_id: "n1", restatement: "pastries", kinds: ["new_angle"], replaces: null, fallback: false }),
      ev("planner.progress", { step: "checking", check_round: 1, sub_topics: [slot("topic-01", "Alpha", "checking")] }),
      ev("planner.planning.completed", { sub_topic_count: 2, note_topic_count: 1, sub_topics: [slot("topic-01", "Alpha", "passed"), { coverage_id: "note-n1", title: "Your note: pastries", note_id: "n1", state: "planned" }] }),
      ev("graph.node.completed", { node: "planner" }, at(60)),
      ev("evidence_verifier.progress", { total: 1, checked: 1, verified: 1, corrected: 0, quoted: 0, dropped: 0, batches: 1, batches_done: 1, sample: { text: "A", verdict: "verified", correction: null, drop_reason: null, source: { role: "derivative", host: "x.org" } } }),
      ev("graph.quality.assessed", { hard_failures: [] }),
      ev("graph.report.reviewed", { review_status: "scored", material_defects: 0, criteria: [], notes: [] }, at(90)),
    ]);
  });
});
