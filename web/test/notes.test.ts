// @vitest-environment node — a plain data/logic test (see run-state.test.ts).
import { describe, expect, it } from "vitest";
import { rowBrief } from "../lib/briefs";
import { NOTE_LIMIT, OUTCOME_TEXT, WHERE, ackFor, earlierNotesText, notePassLine, noteRedraftLine, notesLeft, visibleAcks } from "../lib/notes";
import { applyEvent, marksFor, newRunState, stepLabel, type NodeId, type RunEvent, type RunState } from "../lib/run-state";

const ev = (type: string, metadata: Record<string, unknown> = {}): RunEvent => ({ type, metadata });
const received = (id: string, text: string) => ev("session.note.received", { note_id: id, text });
const interpreted = (id: string, restatement: string, extra: Record<string, unknown> = {}) =>
  ev("session.note.interpreted", { note_id: id, restatement, kinds: ["emphasis"], replaces: null, fallback: false, ...extra });
const started = (node: string, iteration = 0) => ev("graph.node.started", { node, iteration });
const completed = (node: string, iteration = 0) => ev("graph.node.completed", { node, iteration, event_count: 1, error_count: 0 });
function play(events: RunEvent[]): RunState {
  const run = newRunState(2);
  for (const e of events) applyEvent(run, e);
  return run;
}
/* A run that reached Reviewing: planner → … → report_writer done, the reviewer running. */
const toReviewing: RunEvent[] = [
  ev("graph.session.started", { max_extra_passes: 1 }),
  started("planner"), ev("planner.planning.completed", { sub_topic_count: 1, sub_topics: [{ coverage_id: "topic-01", title: "Grid storage costs" }] }), completed("planner"),
  started("researcher"), completed("researcher"), started("source_evaluator"), completed("source_evaluator"),
  started("evidence_verifier"), completed("evidence_verifier"), started("report_writer"), completed("report_writer"), started("report_reviewer"),
];

describe("lib/notes — the note line's copy and each note's acknowledgement (live-briefs spec §4.7)", () => {
  it("names where the note takes effect, by the step that was running when it was read", () => {
    expect(WHERE).toEqual({
      planner: ", shaping the plan",
      researcher: ", from each topic's next search",
      source_evaluator: ", in how sources are rated and in the report",
      evidence_verifier: ", in the report",
      report_writer: ", in this draft",
      report_reviewer: "; the review will check it",
    });
    expect(NOTE_LIMIT).toBe(10);
  });

  it("acknowledges a note as received, then as read, as replacing an earlier one, or as passed on as written", () => {
    const run = play([
      started("planner"), completed("planner"), started("researcher"),
      received("n1", "More on fire safety please"), received("n2", "Actually, only the US"), received("n3", "skip costs"),
    ]);
    expect(ackFor(run.notes[0], run.notes)).toEqual({ key: "n1", lead: "Reading your note…", said: null, rest: "" });
    applyEvent(run, interpreted("n1", "more weight on fire-safety standards"));
    applyEvent(run, interpreted("n2", "only the United States", { replaces: "n1" }));
    applyEvent(run, interpreted("n3", "skip costs", { fallback: true }));
    expect(run.notes.map((n) => n.where)).toEqual(["researcher", "researcher", "researcher"]);
    expect(ackFor(run.notes[0], run.notes)).toEqual({ key: "n1", lead: "Got it — ", said: "more weight on fire-safety standards", rest: ", from each topic's next search" });
    expect(ackFor(run.notes[1], run.notes)).toEqual({
      key: "n2", lead: "Got it — ", said: "only the United States",
      rest: ", from each topic's next search, replacing your earlier note about more weight on fire-safety standards",
    });
    expect(ackFor(run.notes[2], run.notes)).toEqual({ key: "n3", lead: "Got it — passed on as you wrote it", said: null, rest: "" });
  });

  it("shows the latest two, oldest first, and counts the earlier ones", () => {
    const run = play([started("planner"), ...[1, 2, 3, 4].map((k) => received("n" + k, "note " + k))]);
    expect(visibleAcks(run.notes.slice(0, 2)).earlier).toBe(0);
    const shown = visibleAcks(run.notes);
    expect(shown.acks.map((a) => a.key)).toEqual(["n3", "n4"]);
    expect(shown.earlier).toBe(2);
    expect(earlierNotesText(1)).toBe("and 1 earlier note");
    expect(earlierNotesText(2)).toBe("and 2 earlier notes");
  });

  it("words the loops' first lines, the report's outcomes and the notes left", () => {
    const run = play([started("planner"), received("n1", "a"), interpreted("n1", "fire safety"), received("n2", "b"), interpreted("n2", "recycling")]);
    expect(notePassLine(["n1"], run.notes)).toBe("Researching your note: fire safety");
    expect(notePassLine(["n1", "n2"], run.notes)).toBe("Researching your notes: fire safety; recycling");
    expect(noteRedraftLine(["n2"], run.notes)).toBe("Rewriting for your note: recycling");
    expect(OUTCOME_TEXT).toEqual({
      covered: "covered", not_found: "couldn't find evidence", not_addressed: "not addressed in the report",
      pending: "not checked", replaced: "replaced by a later note",
    });
    expect([notesLeft(undefined, 0), notesLeft(10, 3), notesLeft(7, 1), notesLeft(4, 9), notesLeft(0, 0)]).toEqual([10, 7, 7, 1, 0]);
  });
});

describe("run-state — the note events and the note routes (live-briefs spec §4.6-§4.7)", () => {
  it("counts a replayed note once, drops one with no id, and moves no row", () => {
    const run = play([started("planner"), received("n1", "x"), received("n1", "x"), received("", "y"), ev("session.note.interpreted", { restatement: "z" })]);
    expect(run.notes.map((n) => n.id)).toEqual(["n1"]);
    expect(run.active).toBe("planner");
    expect(run.marks).toEqual({});
  });

  it("a note pass re-arms Researching onward, draws the note arc and lists only the notes' sub-topics", () => {
    const run = play([...toReviewing, received("n1", "Fire safety"), interpreted("n1", "more weight on fire-safety standards"),
      ev("graph.report.reviewed", { mean_score: 0.9 }),
      ev("graph.route.decided", { destination: "note_pass", reason: "note_pass_requested", iteration: 0, missing_required_target_ids: [] })]);
    expect(run.active).toBe("researcher");
    expect(run.arc).toBe("note_pass");
    expect(run.loop).toBe("flowing");
    expect(run.marks).toEqual({ planner: "done" });
    expect(run.topics).toEqual([]);
    applyEvent(run, completed("report_reviewer"));
    expect(run.marks).toEqual({ planner: "done" });
    applyEvent(run, started("note_pass"));
    applyEvent(run, ev("graph.note_pass.started", { iteration: 0, note_passes: 1, note_ids: ["n1"], targets: ["note-n1-target-01"] }));
    applyEvent(run, completed("note_pass"));
    expect(run.loop).toBe("settled");
    expect(run.pass).toBe(1);
    expect(run.reopen.researcher).toEqual({ kind: "note_pass", text: "Researching your note: more weight on fire-safety standards" });
    expect(run.topics).toEqual([{ coverageId: "note-n1", title: "Your note: more weight on fire-safety standards", state: "waiting", findings: null }]);
    expect(run.marks).toEqual({ planner: "done" });
    expect(stepLabel("note_pass")).toBe("Researching");
    applyEvent(run, started("researcher"));
    applyEvent(run, ev("researcher.sub_topic.started", { coverage_id: "note-n1" }));
    applyEvent(run, ev("researcher.sub_topic.completed", { coverage_id: "note-n1", findings_retained: 2, successful_reads: 3 }));
    expect(run.topics.map((t) => [t.coverageId, t.state, t.findings])).toEqual([["note-n1", "done", 2]]);
    expect(marksFor(run, run.active).researcher).toBe("active");
  });

  it("a note redraft re-arms Writing onward with its own first line and the review's outcome", () => {
    const run = play([...toReviewing, received("n1", "US only"), interpreted("n1", "only the United States"),
      ev("graph.route.decided", { destination: "redraft", reason: "note_redraft_requested", iteration: 0, missing_required_target_ids: [] })]);
    expect(run.active).toBe("report_writer");
    expect(run.arc).toBe("redraft");
    expect(run.outcomes.report_reviewer).toBe("Sent back to the writer for your note");
    applyEvent(run, completed("report_reviewer"));
    applyEvent(run, started("writer_redraft"));
    applyEvent(run, ev("graph.note_redraft.requested", { iteration: 0, note_ids: ["n1"] }));
    applyEvent(run, completed("writer_redraft"));
    expect(run.reopen.report_writer).toEqual({ kind: "note_redraft", text: "Rewriting for your note: only the United States" });
    expect(run.loop).toBe("settled");
    expect(run.marks.report_writer).toBeUndefined();
  });

  it("the review's outcome names the note pass", () => {
    const run = play([...toReviewing, ev("graph.route.decided", { destination: "note_pass", reason: "note_pass_requested", iteration: 0 })]);
    expect(run.outcomes.report_reviewer).toBe("Sent back to research your note");
    applyEvent(run, ev("graph.note_pass.started", { note_ids: ["n1", "n2"], targets: [] }));
    expect(run.outcomes.report_reviewer).toBe("Sent back to research 2 of your notes");
  });

  it("is burst-safe: the same events paint the same notes in one go or one at a time", () => {
    const events = [...toReviewing, received("n1", "a"), interpreted("n1", "fire safety"), received("n2", "b")];
    const oneByOne = newRunState(2);
    const snaps = events.map((e) => { applyEvent(oneByOne, e); return structuredClone(oneByOne); });
    expect(snaps[snaps.length - 1].notes).toEqual(play(events).notes);
  });
});

describe("briefs — the active row acknowledges the notes (live-briefs spec §4.7)", () => {
  it("only the running row carries the acknowledgements, the latest two and the rest counted", () => {
    const run = play([started("planner"), completed("planner"), started("researcher"),
      ...[1, 2, 3].flatMap((k) => [received("n" + k, "note " + k), interpreted("n" + k, "reading " + k)])]);
    const active = rowBrief(run, "researcher", "active");
    expect(active.acks.map((a) => [a.said, a.rest])).toEqual([["reading 2", ", from each topic's next search"], ["reading 3", ", from each topic's next search"]]);
    expect(active.earlier).toBe("and 1 earlier note");
    for (const id of ["planner", "source_evaluator"] as NodeId[]) {
      const other = rowBrief(run, id, id === "planner" ? "done" : "pending");
      expect([other.acks, other.earlier]).toEqual([[], null]);
    }
  });
});
