// @vitest-environment node — a plain data/logic test; jsdom's client-consumer transform
// otherwise rewrites `new URL(`./dir/${expr}`, import.meta.url)` into an import.meta.glob
// lookup meant for bundled browser assets, which mis-resolves this fixture path.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import type { ResearchEvent, ResearchSessionResponse } from "../lib/api";
import { AGENT_ORDER, EVENT_HANDLERS, STAGES, applyEvent, chipStep, failedMarks, newRunState, replayRun, stepLabel, toRunEvent, type RunState } from "../lib/run-state";

interface Capture { case_id: string; status: ResearchSessionResponse; events: ResearchEvent[] }
const load = (caseId: string): Capture =>
  JSON.parse(readFileSync(fileURLToPath(new URL(`./fixtures/events/${caseId}.json`, import.meta.url)), "utf8"));
const extraPass = load("missing-target-triggers-one-extra-pass");
const redraft = load("scoped-redraft-after-a-named-defect");

/* One snapshot per frame: the state after frames 1..k, as a late subscriber replaying k frames sees it. */
function snapshots(events: ResearchEvent[], passes: number): RunState[] {
  const run = newRunState(passes);
  return events.map((e) => { applyEvent(run, toRunEvent(e)); return structuredClone(run); });
}
const at = (events: ResearchEvent[], pred: (e: ResearchEvent) => boolean, from = 0) => {
  const i = events.findIndex((e, k) => k >= from && pred(e));
  if (i < 0) throw new Error("event not found");
  return i;
};
const P = (c: Capture) => (c.events[0].metadata.max_extra_passes as number) + 1;

describe("the port is the prototype's core", () => {
  it("has the seven rows and the sixteen handlers", () => {
    expect(STAGES.map((s) => s.id)).toEqual(["planner", "researcher", "source_evaluator", "evidence_verifier", "report_writer", "report_reviewer", "finalize_report"]);
    expect(AGENT_ORDER).toEqual(STAGES.map((s) => s.id));
    expect(Object.keys(EVENT_HANDLERS).sort()).toEqual([
      "evidence_verifier.verification.completed", "graph.extra_pass.started", "graph.node.completed", "graph.node.skipped",
      "graph.node.started", "graph.report.redraft_requested", "graph.report.reviewed", "graph.route.decided",
      "graph.session.completed", "graph.session.started", "planner.planning.completed", "report_writer.report.written",
      "researcher.research.completed", "researcher.sub_topic.completed", "researcher.tool_call", "source_evaluator.evaluation.completed",
    ]);
  });
});

describe("(a) terminal agreement with the server after the last frame", () => {
  for (const capture of [extraPass, redraft]) {
    it(capture.case_id, () => {
      const run = replayRun(capture.events, P(capture));
      expect(run.finalStatus).toBe(capture.status.status);
      expect(run.pass).toBe(capture.status.iteration + 1);
      expect(run.maxPasses).toBe(P(capture));
      expect(run.active).toBeNull();
      expect(run.loop).toBe("off");
      expect(run.arc).toBeNull();
      expect(run.tag).toBeNull();
    });
  }
});

describe("(b) the extra pass", () => {
  const snaps = snapshots(extraPass.events, P(extraPass));
  const events = extraPass.events;
  const decided = at(events, (e) => e.event_type === "graph.route.decided" && e.metadata.destination === "extra_pass");
  const reviewerDone = at(events, (e) => e.event_type === "graph.node.completed" && e.metadata.node === "report_reviewer", decided);
  const hop = at(events, (e) => e.event_type === "graph.extra_pass.started", decided);
  it("the route decision re-arms rows 2–6, moves the active row and lights the arc", () => {
    const s = snaps[decided];
    expect(s.marks.planner).toBe("done");
    expect(s.active).toBe("researcher");
    for (const id of ["researcher", "source_evaluator", "evidence_verifier", "report_writer", "report_reviewer", "finalize_report"]) expect(s.marks).not.toHaveProperty(id);
    expect(s.loop).toBe("flowing");
    expect(s.arc).toBe("extra_pass");
  });
  it("the reviewer's own completion is inert after the decision", () => {
    const s = snaps[reviewerDone];
    expect(s.marks).not.toHaveProperty("report_reviewer");
    expect(s.active).toBe("researcher");
  });
  it("the hop settles the loop, advances the pass, sets the tag and resets this-pass counters", () => {
    const s = snaps[hop];
    expect(s.loop).toBe("settled");
    expect(s.pass).toBe(2);
    expect(s.tag?.kind).toBe("extra_pass");
    expect(s.tag?.text).toBe("1 required target had no verified finding");
    expect(s.captions.researcher).toBe("1 missing target only");
    expect(s.countersPass).toBe(2);
    for (const key of ["subTopicsDone", "subTopicsResearched", "subTopicsTotal", "findings", "verified", "corrected", "dropped"] as const) expect(s.counters[key]).toBeNull();
  });
  it("(d) counters come from the stream", () => {
    const last = snaps[snaps.length - 1];
    expect(last.counters.toolCalls).toBe(events.filter((e) => e.event_type === "researcher.tool_call").length);
    const evaluations = events.filter((e) => e.event_type === "source_evaluator.evaluation.completed");
    expect(last.counters.sources).toBe(evaluations[evaluations.length - 1].metadata.source_count);
  });
});

describe("(c) the redraft", () => {
  const snaps = snapshots(redraft.events, P(redraft));
  const events = redraft.events;
  const decided = at(events, (e) => e.event_type === "graph.route.decided" && e.metadata.destination === "redraft");
  const requested = at(events, (e) => e.event_type === "graph.report.redraft_requested", decided);
  it("re-arms rows 5–6 only and lights the redraft arc", () => {
    const s = snaps[decided];
    for (const id of ["planner", "researcher", "source_evaluator", "evidence_verifier"]) expect(s.marks[id as keyof typeof s.marks]).toBe("done");
    expect(s.active).toBe("report_writer");
    expect(s.marks).not.toHaveProperty("report_reviewer");
    expect(s.arc).toBe("redraft");
    expect(s.loop).toBe("flowing");
  });
  it("does not advance the pass and names the defect", () => {
    const s = snaps[requested];
    expect(s.pass).toBe(1);
    expect(s.tag).toEqual({ kind: "redraft", label: "redraft", text: "Reviewer named 1 material defect" });
  });
});

describe("(e) the halted run (the prototype's HALTED_EVENTS, index.html:2829-2838)", () => {
  const md = (m: Record<string, unknown>) => m;
  const halted = [
    { type: "graph.session.started", metadata: md({ session_id: "2ad900b1", max_extra_passes: 1, checkpointing: false }) },
    { type: "graph.node.started", metadata: md({ node: "planner", iteration: 0 }) },
    ...["researcher", "source_evaluator", "evidence_verifier", "report_writer", "report_reviewer"].map((node) => ({ type: "graph.node.skipped", metadata: md({ node, iteration: 0, reason: "halted" }) })),
    { type: "graph.session.completed", metadata: md({ status: "failed", iteration: 0, error_count: 1, has_report: false }) },
  ];
  it("marks the halting row active, the rest skipped, Publishing skipped, counters unreached", () => {
    const run = newRunState(2);
    for (const ev of halted) applyEvent(run, ev);
    const marks = failedMarks(run, "failed");
    expect(marks.planner).toBe("active");
    for (const id of ["researcher", "source_evaluator", "evidence_verifier", "report_writer", "report_reviewer", "finalize_report"]) expect(marks[id as keyof typeof marks]).toBe("skipped");
    expect(Object.values(run.counters).filter((v) => v !== null && v !== false)).toEqual([]);
  });
  it("an API-level failure (no graph.session.completed) still skips Publishing by the session status", () => {
    const run = newRunState(2);
    applyEvent(run, halted[0]);
    applyEvent(run, halted[1]);
    expect(failedMarks(run, "failed").finalize_report).toBe("skipped");
    expect(failedMarks(run, "running").finalize_report).toBeUndefined();
  });
});

describe("the chip's step (live-briefs spec §4.2)", () => {
  it("stepLabel names the row a node runs on; hops read as the row they lead back to", () => {
    expect(stepLabel("researcher")).toBe("Researching");
    expect(stepLabel("finalize_report")).toBe("Publishing");
    expect(stepLabel("extra_pass")).toBe("Researching");
    expect(stepLabel("writer_redraft")).toBe("Writing report");
    expect(stepLabel("graph")).toBeNull();
    expect(stepLabel(null)).toBeNull();
  });
  it("chipStep follows the active row, then the row the run ended on", () => {
    expect(chipStep(newRunState(2))).toBe("planner");
    const ended = replayRun(extraPass.events, P(extraPass));
    expect(ended.active).toBeNull();
    expect(chipStep(ended)).toBe("finalize_report");
    const halted = newRunState(2);
    applyEvent(halted, { type: "graph.node.started", metadata: { node: "researcher", iteration: 0 } });
    applyEvent(halted, { type: "graph.node.skipped", metadata: { node: "researcher", iteration: 0, reason: "halted" } });
    applyEvent(halted, { type: "graph.session.completed", metadata: { status: "failed", iteration: 0, error_count: 1, has_report: false } });
    expect(chipStep(halted)).toBe("researcher");
  });
});
