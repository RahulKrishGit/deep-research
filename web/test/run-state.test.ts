// @vitest-environment node — a plain data/logic test; jsdom's client-consumer transform
// otherwise rewrites `new URL(`./dir/${expr}`, import.meta.url)` into an import.meta.glob
// lookup meant for bundled browser assets, which mis-resolves this fixture path.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import type { ResearchEvent, ResearchSessionResponse } from "../lib/api";
import { AGENT_ORDER, EVENT_HANDLERS, STAGES, applyEvent, chipStep, countPhrase, failedMarks, newRunState, plural, replayRun, stepLabel, toRunEvent, toggleOpen, type RunState } from "../lib/run-state";
import * as runStateModule from "../lib/run-state";

interface Capture { case_id: string; status: ResearchSessionResponse; events: ResearchEvent[] }
const load = (caseId: string): Capture =>
  JSON.parse(readFileSync(fileURLToPath(new URL(`./fixtures/events/${caseId}.json`, import.meta.url)), "utf8"));
const extraPass = load("missing-target-triggers-one-extra-pass");
const redraft = load("scoped-redraft-after-a-named-defect");

/* One snapshot per frame: the state after frames 1..k, as a late subscriber replaying k frames sees it. */
function snapshots(events: ResearchEvent[]): RunState[] {
  const run = newRunState();
  return events.map((e) => { applyEvent(run, toRunEvent(e)); return structuredClone(run); });
}
const at = (events: ResearchEvent[], pred: (e: ResearchEvent) => boolean, from = 0) => {
  const i = events.findIndex((e, k) => k >= from && pred(e));
  if (i < 0) throw new Error("event not found");
  return i;
};

describe("the port is the prototype's core", () => {
  it("has the seven rows and the twenty-three handlers", () => {
    expect(STAGES.map((s) => s.id)).toEqual(["planner", "researcher", "source_evaluator", "evidence_verifier", "report_writer", "report_reviewer", "finalize_report"]);
    expect(AGENT_ORDER).toEqual(STAGES.map((s) => s.id));
    expect(Object.keys(EVENT_HANDLERS).sort()).toEqual([
      "evidence_verifier.verification.completed", "graph.extra_pass.started", "graph.node.completed", "graph.node.skipped",
      "graph.node.started", "graph.note_pass.started", "graph.note_redraft.requested", "graph.report.redraft_requested",
      "graph.report.reviewed", "graph.route.decided", "graph.session.completed",
      "planner.planning.completed", "report_writer.report.written", "researcher.research.completed",
      "researcher.sub_topic.completed", "researcher.sub_topic.started", "researcher.tool_call",
      "session.clarification.answered", "session.clarification.requested", "session.note.interpreted", "session.note.received",
      "session.stopped", "source_evaluator.evaluation.completed",
    ]);
  });
});

describe("the run state holds only what the page reads (Phase 2 final review R6)", () => {
  it("has no pass cap, loop tag or blurbs, exports no BLURB, and graph.session.started changes nothing", () => {
    const run = newRunState();
    expect(Object.keys(run).sort()).toEqual([
      "active", "arc", "captions", "clarify", "counters", "countersPass", "finalStatus", "findingsSoFar",
      "loop", "loopPending", "marks", "notes", "open", "openNode", "outcomes", "pagesRead", "pass",
      "passFindings", "plan", "rearmed", "rearmedFirst", "reopen", "stopped", "topics",
    ]);
    const before = structuredClone(run);
    applyEvent(run, { type: "graph.session.started", metadata: { max_extra_passes: 1 } });
    expect(run).toEqual(before);
    expect(Object.keys(runStateModule)).not.toContain("BLURB");
  });
});

describe("(a) terminal agreement with the server after the last frame", () => {
  for (const capture of [extraPass, redraft]) {
    it(capture.case_id, () => {
      const run = replayRun(capture.events);
      expect(run.finalStatus).toBe(capture.status.status);
      expect(run.pass).toBe(capture.status.iteration + 1);
      expect(run.active).toBeNull();
      expect(run.loop).toBe("off");
      expect(run.arc).toBeNull();
    });
  }
});

describe("(b) the extra pass", () => {
  const snaps = snapshots(extraPass.events);
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
  it("the hop settles the loop, advances the pass, sets the reopen line and resets this-pass counters", () => {
    const s = snaps[hop];
    expect(s.loop).toBe("settled");
    expect(s.pass).toBe(2);
    expect(s.reopen.researcher).toEqual({ kind: "extra_pass", text: "Going back to research 1 gap the review found" });
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
  const snaps = snapshots(redraft.events);
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
    expect(s.reopen.report_writer).toEqual({ kind: "redraft", text: "Rewriting to fix 1 issue the review found" });
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
    const run = newRunState();
    for (const ev of halted) applyEvent(run, ev);
    const marks = failedMarks(run, "failed");
    expect(marks.planner).toBe("active");
    for (const id of ["researcher", "source_evaluator", "evidence_verifier", "report_writer", "report_reviewer", "finalize_report"]) expect(marks[id as keyof typeof marks]).toBe("skipped");
    expect(Object.values(run.counters).filter((v) => v !== null && v !== false)).toEqual([]);
  });
  it("an API-level failure (no graph.session.completed) still skips Publishing by the session status", () => {
    const run = newRunState();
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
    expect(chipStep(newRunState())).toBe("planner");
    const ended = replayRun(extraPass.events);
    expect(ended.active).toBeNull();
    expect(chipStep(ended)).toBe("finalize_report");
    const halted = newRunState();
    applyEvent(halted, { type: "graph.node.started", metadata: { node: "researcher", iteration: 0 } });
    applyEvent(halted, { type: "graph.node.skipped", metadata: { node: "researcher", iteration: 0, reason: "halted" } });
    applyEvent(halted, { type: "graph.session.completed", metadata: { status: "failed", iteration: 0, error_count: 1, has_report: false } });
    expect(chipStep(halted)).toBe("researcher");
  });
  it("chipStep keeps naming Publishing between its completion and graph.session.completed (no stale /status fallback)", () => {
    const events = extraPass.events;
    const published = at(events, (e) => e.event_type === "graph.node.completed" && e.metadata.node === "finalize_report");
    const run = newRunState();
    for (let k = 0; k <= published; k++) applyEvent(run, toRunEvent(events[k]));
    // Publishing's completion has been applied; graph.session.completed has not. The row is done, so
    // there is no active row and no open node — the chip must still read Publishing, never fall
    // through to whatever /status last said.
    expect(run.finalStatus).toBeNull();
    expect(run.active).toBeNull();
    expect(run.openNode).toBeNull();
    expect(chipStep(run)).toBe("finalize_report");
    // a run that ended without ever finishing Publishing (the reviewer routed to "end") has nothing to name
    const ended = newRunState();
    applyEvent(ended, { type: "graph.route.decided", metadata: { destination: "end", reason: "no_report" } });
    expect(chipStep(ended)).toBeNull();
  });
});

/* live-briefs spec §4.3: the step briefs' state, proven on the regenerated live captures. */
type Planned = { coverage_id: string; title: string };
const md = <T,>(e: ResearchEvent, key: string) => e.metadata[key] as T;

describe("(f) the Researching checklist follows the live topic events", () => {
  for (const capture of [extraPass, redraft]) {
    const events = capture.events;
    const snaps = snapshots(events);
    const planned = at(events, (e) => e.event_type === "planner.planning.completed");
    it(`${capture.case_id}: the plan lists every sub-topic title, each waiting`, () => {
      const titles = md<Planned[]>(events[planned], "sub_topics");
      expect(titles.length).toBeGreaterThan(0);
      const s = snaps[planned];
      expect(s.plan).toEqual(titles.map((t) => ({ coverageId: t.coverage_id, title: t.title })));
      expect(s.topics.map((t) => [t.coverageId, t.title, t.state, t.findings])).toEqual(titles.map((t) => [t.coverage_id, t.title, "waiting", null]));
      expect(s.outcomes.planner).toBe(plural(titles.length, "sub-topic", "sub-topics"));
    });
    it(`${capture.case_id}: a topic runs from its started event and is done, with its findings, from its completed event`, () => {
      events.forEach((e, k) => {
        const topic = () => snaps[k].topics.find((t) => t.coverageId === md<string>(e, "coverage_id"));
        if (e.event_type === "researcher.sub_topic.started") expect(topic()?.state).toBe("running");
        if (e.event_type === "researcher.sub_topic.completed") {
          expect(topic()?.state).toBe("done");
          expect(topic()?.findings).toBe(md<number>(e, "findings_retained"));
        }
      });
    });
    it(`${capture.case_id}: pages read and findings sum the completed topics, then take the research total`, () => {
      let pages = 0, kept = 0;
      events.forEach((e, k) => {
        if (e.event_type === "graph.route.decided" && md<string>(e, "destination") === "extra_pass") { pages = 0; kept = 0; }
        if (e.event_type === "researcher.sub_topic.completed") {
          pages += md<number>(e, "successful_reads"); kept += md<number>(e, "findings_retained");
          expect(snaps[k].pagesRead).toBe(pages);
          expect(snaps[k].findingsSoFar).toBe(kept);
        }
        if (e.event_type === "researcher.research.completed") {
          expect(kept).toBe(md<number>(e, "findings")); // the spec's topic-findings-sum inference, on the stream
          expect(snaps[k].findingsSoFar).toBe(md<number>(e, "findings"));
          expect(snaps[k].passFindings).toBe(md<number>(e, "findings"));
          expect(snaps[k].outcomes.researcher).toBe([
            countPhrase(md<number>(e, "sub_topics_researched"), "topic", "topics"),
            countPhrase(pages, "page read", "pages read"),
            countPhrase(md<number>(e, "findings"), "finding", "findings"),
          ].join(" · "));
        }
      });
    });
  }
});

describe("(g) loops reopen rows with the reason", () => {
  it("the extra pass empties the checklist at the route decision, names the gaps, and lists only the topics it re-runs", () => {
    const events = extraPass.events;
    const snaps = snapshots(events);
    const decided = at(events, (e) => e.event_type === "graph.route.decided" && e.metadata.destination === "extra_pass");
    const hop = at(events, (e) => e.event_type === "graph.extra_pass.started", decided);
    const rerun = at(events, (e) => e.event_type === "researcher.sub_topic.started", hop);
    expect(snaps[decided].topics).toEqual([]);
    expect(snaps[decided].outcomes.report_reviewer).toBe("Sent back to fill 1 gap");
    expect(snaps[hop].reopen.researcher).toEqual({ kind: "extra_pass", text: "Going back to research 1 gap the review found" });
    const title = md<Planned[]>(events[at(events, (e) => e.event_type === "planner.planning.completed")], "sub_topics")
      .find((t) => t.coverage_id === md<string>(events[rerun], "coverage_id"))!.title;
    expect(snaps[rerun].topics).toEqual([{ coverageId: md<string>(events[rerun], "coverage_id"), title, state: "running", findings: null }]);
  });
  it("the redraft reopens Writing with the number of issues", () => {
    const events = redraft.events;
    const snaps = snapshots(events);
    const requested = at(events, (e) => e.event_type === "graph.report.redraft_requested");
    expect(snaps[requested].reopen.report_writer).toEqual({ kind: "redraft", text: "Rewriting to fix 1 issue the review found" });
    expect(snaps[requested].reopen.researcher).toBeUndefined();
  });
  it("re-armed rows lose a reader's reopen", () => {
    const run = newRunState();
    run.marks = { planner: "done", researcher: "done", source_evaluator: "done" };
    toggleOpen(run, "researcher");
    toggleOpen(run, "planner");
    applyEvent(run, { type: "graph.route.decided", metadata: { destination: "extra_pass", reason: "extra_pass_requested", missing_required_target_ids: ["topic-01-target-01"] } });
    expect([...run.open]).toEqual(["planner"]);
  });
});

describe("(h) every row's outcome line", () => {
  it("reads the spec's templates at the end of each capture", () => {
    for (const capture of [extraPass, redraft]) {
      const events = capture.events;
      const run = replayRun(events);
      const last = (type: string) => events.filter((e) => e.event_type === type).at(-1)!;
      const reviewed = last("graph.report.reviewed");
      expect(run.outcomes.source_evaluator).toBe(plural(md<number>(last("source_evaluator.evaluation.completed"), "source_count"), "source rated", "sources rated"));
      const v = last("evidence_verifier.verification.completed");
      expect(run.outcomes.evidence_verifier).toBe(`${md<number>(v, "verified")} verified · ${md<number>(v, "verified_corrected")} corrected · ${md<number>(v, "dropped")} dropped`);
      const w = last("report_writer.report.written");
      expect(run.outcomes.report_writer).toBe(`Report drafted · ${md<number>(w, "statements")} sentences · ${md<number>(w, "citations")} citations`);
      expect(run.outcomes.report_reviewer).toBe(`Accepted · ${md<number>(reviewed, "mean_score").toFixed(2)}`);
      expect(run.outcomes.finalize_report).toBe("Published");
    }
  });
});

describe("(i) burst-safety: a late subscriber paints the same briefs", () => {
  for (const capture of [extraPass, redraft]) {
    it(capture.case_id, () => {
      const snaps = snapshots(capture.events);
      capture.events.forEach((_, k) => expect(replayRun(capture.events.slice(0, k + 1))).toEqual(snaps[k]));
    });
  }
});

describe("(j) the one-time check (live-briefs spec §4.4-§4.5)", () => {
  const questions = [
    { id: "q1", dimension: "geography", text: "Which region should this cover?", short: "Region", options: ["United States", "European Union", "Global"], best_guess: "Global" },
    { id: "q2", dimension: "period", text: "How recent should the sources be?", short: "Period", options: ["Last 12 months", "Since 2023", "Any time"], best_guess: "Since 2023" },
  ];
  const requested = { type: "session.clarification.requested", metadata: { questions, deadline_at: "2026-09-29T10:01:00.000Z" } };
  const answered = { type: "session.clarification.answered", metadata: { reason: "skipped", answers: [
    { question_id: "q1", value: "Global", source: "chosen" }, { question_id: "q2", value: "Since 2023", source: "best_guess" },
  ] } };
  it("holds the questions and the deadline, then the answers and why; no row moves", () => {
    const run = newRunState();
    expect(run.clarify).toBeNull();
    applyEvent(run, requested);
    expect(run.clarify).toEqual({
      questions: [
        { id: "q1", dimension: "geography", text: "Which region should this cover?", short: "Region", options: ["United States", "European Union", "Global"], bestGuess: "Global" },
        { id: "q2", dimension: "period", text: "How recent should the sources be?", short: "Period", options: ["Last 12 months", "Since 2023", "Any time"], bestGuess: "Since 2023" },
      ],
      deadlineAt: "2026-09-29T10:01:00.000Z",
      answered: null,
    });
    applyEvent(run, answered);
    expect(run.clarify!.answered).toEqual({ reason: "skipped", answers: [
      { questionId: "q1", value: "Global", source: "chosen" }, { questionId: "q2", value: "Since 2023", source: "best_guess" },
    ] });
    expect(run.clarify!.questions).toHaveLength(2);
    expect(run.active).toBe("planner");
    expect(run.marks).toEqual({});
  });
  it("drops a malformed question or answer rather than inventing one", () => {
    const run = newRunState();
    applyEvent(run, { type: "session.clarification.requested", metadata: { questions: [questions[0], { id: "q2", text: "no options" }, null], deadline_at: 5 } });
    expect(run.clarify!.questions.map((q) => q.id)).toEqual(["q1"]);
    expect(run.clarify!.deadlineAt).toBe("");
    applyEvent(run, { type: "session.clarification.answered", metadata: { answers: [{ question_id: "q1", value: "Global", source: "guessed" }] } });
    expect(run.clarify!.answered).toEqual({ answers: [], reason: "" });
  });
  it("is burst-safe: a late subscriber paints the same check", () => {
    const events: ResearchEvent[] = [requested, answered, { type: "graph.session.started", metadata: { max_extra_passes: 1 } }, { type: "graph.node.started", metadata: { node: "planner", iteration: 0 } }]
      .map((e, i) => ({ event_type: e.type, source: "api", message: "m", timestamp: "2026-09-29T10:00:00+00:00", metadata: e.metadata, event_id: `e${i}` }));
    const snaps = snapshots(events);
    events.forEach((_, k) => expect(replayRun(events.slice(0, k + 1))).toEqual(snaps[k]));
  });
});

describe("session.stopped (notes-progress-report spec §8.4)", () => {
  it("records the step, the time and the seconds; leaves no row active and no loop lit; keeps every mark", () => {
    const events = extraPass.events;
    const decided = at(events, (e) => e.event_type === "graph.route.decided" && e.metadata.destination === "extra_pass");
    const run = replayRun(events.slice(0, decided + 1));
    const before = structuredClone(run);
    expect(newRunState().stopped).toBeNull();
    applyEvent(run, { type: "session.stopped", metadata: { step: "researcher", stopped_at: "2026-09-30T20:41:07+00:00", elapsed_seconds: 391 } });
    expect(run.stopped).toEqual({ step: "researcher", at: "2026-09-30T20:41:07+00:00", elapsedSeconds: 391 });
    expect([run.active, run.loop, run.arc]).toEqual([null, "off", null]);
    expect([run.marks, run.rearmed, run.topics]).toEqual([before.marks, before.rearmed, before.topics]);
    applyEvent(run, { type: "session.stopped", metadata: {} });
    expect(run.stopped).toEqual({ step: null, at: null, elapsedSeconds: null });
  });
});
