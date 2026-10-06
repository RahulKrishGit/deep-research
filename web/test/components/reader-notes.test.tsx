import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { BriefSpine } from "../../components/BriefSpine";
import { ReportRail } from "../../components/ReportRail";
import { RunningPipeline } from "../../components/RunningPipeline";
import type { ResearchSessionResponse } from "../../lib/api";
import { applyEvent, marksFor, newRunState, type RunState } from "../../lib/run-state";

function researchingWithNotes(count: number): RunState {
  const run = newRunState();
  applyEvent(run, { type: "graph.node.started", metadata: { node: "planner", iteration: 0 } });
  applyEvent(run, { type: "graph.node.completed", metadata: { node: "planner" } });
  applyEvent(run, { type: "graph.node.started", metadata: { node: "researcher", iteration: 0 } });
  for (let k = 1; k <= count; k++) {
    applyEvent(run, { type: "session.note.received", metadata: { note_id: "n" + k, text: "note " + k } });
    if (k < count) applyEvent(run, { type: "session.note.interpreted", metadata: { note_id: "n" + k, restatement: "reading " + k, kinds: ["emphasis"], replaces: null, fallback: false } });
  }
  return run;
}
const pipeline = (run: RunState, notesRemaining?: number) =>
  render(<RunningPipeline sessionId="s1" run={run} question="q" strip={null} startedAt="2026-09-27T00:00:00+00:00" onToggleRow={() => {}} notesRemaining={notesRemaining} />);

describe("the running stage's notes", () => {
  it("acknowledges the notes at the top of the running row's brief: a dot, the reading, where it applies", () => {
    const run = researchingWithNotes(2);
    const { container } = render(<BriefSpine marks={marksFor(run, run.active)} run={run} onToggle={() => {}} />);
    const brief = container.querySelector('#spine > li[data-stage="researcher"] .ps-brief')!;
    const acks = [...brief.querySelectorAll(":scope > .ln.ack")];
    expect(acks.map((a) => a.textContent)).toEqual(["Got it — reading 1, from each topic's next search", "Reading your note…"]);
    expect(acks[0].querySelector(".said")!.textContent).toBe("reading 1");
    expect(acks[0].querySelector(".d")!.getAttribute("aria-hidden")).toBe("true");
    // Each acknowledgement is announced when it changes, without moving focus.
    expect(acks.map((a) => [a.getAttribute("aria-live"), a.getAttribute("aria-atomic")])).toEqual([["polite", "true"], ["polite", "true"]]);
    expect(brief.firstElementChild).toBe(acks[0]);
    expect(container.querySelectorAll(".ack")).toHaveLength(2);
  });

  it("past two notes, shows the latest two and counts the earlier ones", () => {
    const run = researchingWithNotes(4);
    const { container } = render(<BriefSpine marks={marksFor(run, run.active)} run={run} onToggle={() => {}} />);
    expect([...container.querySelectorAll(".ack")].map((a) => a.textContent)).toEqual([
      "Got it — reading 3, from each topic's next search", "Reading your note…", "and 2 earlier notes",
    ]);
    expect(container.querySelector(".ack:not([data-ack])")!.hasAttribute("aria-live")).toBe(false);
  });

  it("puts the note line last in the pipeline card, and disables it once the stream has seen the tenth note", () => {
    // One pipeline at a time: jsdom resolves an id selector document-wide, so two mounted copies
    // of #noteInput would hide the second.
    const open = pipeline(researchingWithNotes(1), 10);
    expect(open.container.querySelector("#stage-running .card")!.lastElementChild!.id).toBe("noteLine");
    expect(open.container.querySelector<HTMLInputElement>("#noteInput")!.disabled).toBe(false);
    open.unmount();
    const full = pipeline(researchingWithNotes(10), 9);
    expect(full.container.querySelector<HTMLInputElement>("#noteInput")!.disabled).toBe(true);
    expect(full.container.querySelector<HTMLButtonElement>("#noteSend")!.disabled).toBe(true);
    full.unmount();
    const stale = pipeline(researchingWithNotes(0), 0);
    expect(stale.container.querySelector<HTMLInputElement>("#noteInput")!.disabled).toBe(true);
  });
});

const NOTES = [
  { note_id: "n1", text: "More on fire safety", restatement: "more weight on fire-safety standards", outcome: "covered" as const },
];

/* The report does not list the reader's notes above its prose; each note's line is in the bottom line
   (test/components/report-body.test.tsx). The rail counts the note passes. */
describe("the report rail's pass fact", () => {
  it("names the note passes in the pass fact", () => {
    const status: ResearchSessionResponse = {
      session_id: "s", query: "q", status: "completed", current_agent: null, iteration: 1, started_at: "2026-09-16T14:02:11Z",
      finished_at: "2026-09-16T14:12:00Z", report_path: null, trace_url: null, errors: [], evidence_path: null, quality_path: null,
      quality_contract_version: null, semantic_review_status: "scored", semantic_review_score: 0.9, duration_seconds: null,
      coverage: null, evidence_counts: null, notes: NOTES, notes_remaining: 6, note_passes: 2, clarification: null,
    };
    const { container } = render(<ReportRail status={status} evidence={null} />);
    expect(container.querySelector("#repFactPass")!.textContent).toBe("Went back once to fill gaps · went back twice for your notes");
  });
});
