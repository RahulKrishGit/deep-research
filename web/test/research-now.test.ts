// @vitest-environment node — a plain data/logic test (see run-state.test.ts).
// Phase A end-of-phase review: a research note's acknowledgement says "now" only while its own
// thread runs in THIS researcher run (notes-progress-report spec §5.7). A loop that re-opens
// Researching (an extra pass, a note pass) starts a new run of the researcher, so an earlier run's
// thread no longer counts; the note's own started event sets it again.
import { describe, expect, it } from "vitest";
import { RESEARCH_NOW, ackFor } from "../lib/notes";
import { applyEvent, newRunState, type RunEvent, type RunState } from "../lib/run-state";

const ev = (type: string, metadata: Record<string, unknown> = {}): RunEvent => ({ type, metadata });
const started = (node: string, iteration = 0) => ev("graph.node.started", { node, iteration });
const completed = (node: string, iteration = 0) => ev("graph.node.completed", { node, iteration, event_count: 1, error_count: 0 });
const received = (id: string, text: string) => ev("session.note.received", { note_id: id, text });
const angle = (id: string, restatement: string) =>
  ev("session.note.interpreted", { note_id: id, restatement, kinds: ["new_angle"], replaces: null, fallback: false });
const ownThread = (id: string) =>
  ev("researcher.sub_topic.started", { coverage_id: "note-" + id, note_id: id, sub_topic: "Your note: pastries in the cafe", index: 2 });
const AS_ITS_OWN_TOPIC = ", as its own topic";

function play(events: RunEvent[]): RunState {
  const run = newRunState();
  for (const e of events) applyEvent(run, e);
  return run;
}
const restOf = (run: RunState) => ackFor(run.notes[0], run.notes, run.active).rest;

/* A research note read while Researching ran, whose own thread started in the main round; the
   researcher then finished and the run reached Reviewing. */
const toReviewing: RunEvent[] = [
  ev("graph.session.started", { max_extra_passes: 1 }),
  started("planner"), ev("planner.planning.completed", { sub_topic_count: 1, sub_topics: [{ coverage_id: "topic-01", title: "Grid storage costs" }] }), completed("planner"),
  started("researcher"), received("n1", "Pastries too"), angle("n1", "pastries in the cafe"), ownThread("n1"), completed("researcher"),
  started("source_evaluator"), completed("source_evaluator"), started("evidence_verifier"), completed("evidence_verifier"),
  started("report_writer"), completed("report_writer"), started("report_reviewer"),
];

describe("a research note says 'now' only while its own thread runs in this researcher run (spec §5.7)", () => {
  it("does not say 'now' in an extra pass the note's earlier thread does not belong to", () => {
    const run = play(toReviewing);
    expect(run.notes[0]).toMatchObject({ where: "researcher", threadStarted: true });
    applyEvent(run, ev("graph.route.decided", { destination: "extra_pass", reason: "gaps_found", iteration: 0 }));
    expect(run.active).toBe("researcher");
    expect(run.notes[0].threadStarted).toBe(false);
    expect(restOf(run)).toBe(AS_ITS_OWN_TOPIC);
    expect(restOf(run)).not.toBe(RESEARCH_NOW);
  });

  it("says 'now' in a note pass only once the note's own thread has started in that pass", () => {
    const run = play(toReviewing);
    expect(run.notes[0].threadStarted).toBe(true);
    for (const e of [
      ev("graph.route.decided", { destination: "note_pass", reason: "note_pass_requested", iteration: 0 }), started("note_pass"),
      ev("graph.note_pass.started", { iteration: 0, note_passes: 1, note_ids: ["n1"], targets: ["note-n1-target-01"] }),
      completed("note_pass"), started("researcher"),
    ]) applyEvent(run, e);
    expect(run.active).toBe("researcher");
    expect(run.notes[0].threadStarted).toBe(false);
    expect(restOf(run)).toBe(AS_ITS_OWN_TOPIC);
    applyEvent(run, ownThread("n1"));
    expect(run.notes[0].threadStarted).toBe(true);
    expect(restOf(run)).toBe(RESEARCH_NOW);
  });
});
