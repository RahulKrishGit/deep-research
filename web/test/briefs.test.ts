// @vitest-environment node — rowBrief over synthesized events and the live captures. Each step's own
// body is tested in step-briefs.test.ts; this file tests what a row shows and when.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import type { ResearchEvent } from "../lib/api";
import {
  SENTENCES, STATIC_META, notRunText, rowBrief, stoppedSubtitle, subtitleText, topicFact, type BriefBody, type RowBrief, type Subtitle,
} from "../lib/briefs";
import { AGENT_ORDER, applyEvent, newRunState, replayRun, toRunEvent, type NodeId, type RunEvent, type RunState } from "../lib/run-state";

interface Capture { case_id: string; events: ResearchEvent[] }
const load = (caseId: string): Capture =>
  JSON.parse(readFileSync(fileURLToPath(new URL(`./fixtures/events/${caseId}.json`, import.meta.url)), "utf8"));
const captures = [load("missing-target-triggers-one-extra-pass"), load("scoped-redraft-after-a-named-defect")];
function snapshots(events: ResearchEvent[]): RunState[] {
  const run = newRunState();
  return events.map((e) => { applyEvent(run, toRunEvent(e)); return structuredClone(run); });
}
/* A fixed clock, ten minutes after every synthesized event's base time, so elapsed times are exact. */
const NOW = Date.UTC(2026, 8, 30, 12, 10, 0);
const at = (s: number) => new Date(Date.UTC(2026, 8, 30, 12, 0, s)).toISOString();
const ev = (type: string, metadata: Record<string, unknown> = {}, timestamp?: string): RunEvent => ({ type, metadata, timestamp });
function play(events: RunEvent[]): RunState { const run = newRunState(); for (const e of events) applyEvent(run, e); return run; }
function body<K extends BriefBody["kind"]>(brief: RowBrief, kind: K): Extract<BriefBody, { kind: K }> {
  expect(brief.body.kind).toBe(kind);
  return brief.body as Extract<BriefBody, { kind: K }>;
}
const text = (brief: RowBrief) => subtitleText(brief.subtitle);

describe("the Researching brief (live-briefs spec §4.3, AC5; kept by notes-progress-report D12)", () => {
  it("reads its static meta until topics are known, then '{n} topics · researching' until one is done", () => {
    const run = newRunState();
    expect(text(rowBrief(run, "researcher", "active", NOW))).toBe("search · scrape · read · memory");
    applyEvent(run, ev("planner.planning.completed", { sub_topic_count: 2, sub_topics: [{ coverage_id: "topic-01", title: "Alpha" }, { coverage_id: "topic-02", title: "Beta" }] }));
    expect(text(rowBrief(run, "researcher", "active", NOW))).toBe("2 topics · researching");
    applyEvent(run, ev("researcher.sub_topic.started", { coverage_id: "topic-02", sub_topic: "Beta", index: 2 }));
    applyEvent(run, ev("researcher.sub_topic.completed", { coverage_id: "topic-02", sub_topic: "Beta", index: 2, successful_reads: 0, findings_retained: 0 }));
    expect(text(rowBrief(run, "researcher", "active", NOW))).toBe("1 of 2 topics done · no pages read · no findings");
    expect(body(rowBrief(run, "researcher", "active", NOW), "research").topics.map((t) => [t.n, t.title, t.state, t.fact]))
      .toEqual([[1, "Alpha", "waiting", "not yet"], [2, "Beta", "done", "no findings"]]);
    applyEvent(run, ev("researcher.sub_topic.started", { coverage_id: "topic-01", sub_topic: "Alpha", index: 1 }));
    expect(body(rowBrief(run, "researcher", "active", NOW), "research").topics[0].fact).toBe("reading");
    applyEvent(run, ev("researcher.sub_topic.completed", { coverage_id: "topic-01", sub_topic: "Alpha", index: 1, successful_reads: 1, findings_retained: 1 }));
    expect(text(rowBrief(run, "researcher", "active", NOW))).toBe("2 of 2 topics done · 1 page read · 1 finding");
  });
  it("never renders a bare 0 or a dash, at any point of either capture", () => {
    for (const capture of captures) {
      for (const run of snapshots(capture.events)) {
        const brief = rowBrief(run, "researcher", "active", NOW);
        const texts = [text(brief), rowBrief(run, "researcher", "done", NOW).outcome, ...body(brief, "research").topics.map((t) => t.fact)];
        for (const t of texts) { expect(t).not.toMatch(/(^|\D)0(\D|$)/); expect(t).not.toContain("—"); }
      }
    }
  });
  it("prints only the facts it measured: a null count is left out, a measured 0 reads in words (D19)", () => {
    const research = (done: number, pages: number | null, findings: number | null): Subtitle => ({ kind: "research", topics: 3, done, pages, findings });
    // Before the first topic is done the running line is "{n} topics · researching", whatever is measured.
    expect(subtitleText(research(0, null, null))).toBe("3 topics · researching");
    expect(subtitleText(research(0, 0, 0))).toBe("3 topics · researching");
    // Unmeasured counts are left out, never "no pages read" or "0".
    expect(subtitleText(research(1, null, null))).toBe("1 of 3 topics done");
    expect(subtitleText(research(2, 7, null))).toBe("2 of 3 topics done · 7 pages read");
    expect(subtitleText(research(2, null, 1))).toBe("2 of 3 topics done · 1 finding");
    // Measured zeros still print, in words.
    expect(subtitleText(research(1, 0, 0))).toBe("1 of 3 topics done · no pages read · no findings");
    expect(subtitleText(research(1, 0, null))).toBe("1 of 3 topics done · no pages read");
    expect(subtitleText(research(1, null, 0))).toBe("1 of 3 topics done · no findings");
    expect(subtitleText(research(3, 41, 212))).toBe("3 of 3 topics done · 41 pages read · 212 findings");
  });
  it("topicFact follows the spec's table", () => {
    expect(topicFact({ coverageId: "a", title: "A", state: "waiting", findings: null })).toBe("not yet");
    expect(topicFact({ coverageId: "a", title: "A", state: "running", findings: null })).toBe("reading");
    expect(topicFact({ coverageId: "a", title: "A", state: "done", findings: 1 })).toBe("1 finding");
    expect(topicFact({ coverageId: "a", title: "A", state: "done", findings: 9 })).toBe("9 findings");
  });
});

describe("every other row (notes-progress-report spec §6.3-§6.9)", () => {
  it("shows its static meta while pending and its live facts while active; Reviewing's meta is '5 checks', '· your notes' with a note held", () => {
    const run = newRunState();
    for (const id of AGENT_ORDER.filter((id) => id !== "researcher")) expect(text(rowBrief(run, id, "pending", NOW))).toBe(STATIC_META[id]);
    expect(STATIC_META.report_reviewer).toBe("5 checks");
    applyEvent(run, ev("graph.node.started", { node: "planner", iteration: 0 }, at(0)));
    expect(text(rowBrief(run, "planner", "active", NOW))).toBe("10m 00s");
    applyEvent(run, ev("session.note.received", { note_id: "n1", text: "more on safety" }));
    applyEvent(run, ev("session.note.interpreted", { note_id: "n1", restatement: "more on safety", kinds: ["emphasis"], replaces: null, fallback: false }));
    expect(text(rowBrief(run, "report_reviewer", "pending", NOW))).toBe("5 checks · your notes");
  });
  it("gives each step its own body, and Publishing its one sentence", () => {
    const run = newRunState();
    expect(AGENT_ORDER.map((id) => rowBrief(run, id, "active", NOW).body.kind))
      .toEqual(["planning", "research", "evaluating", "verifying", "writing", "reviewing", "sentence"]);
    expect(body(rowBrief(run, "finalize_report", "active", NOW), "sentence").text).toBe("Saving the report and evidence log");
    expect(SENTENCES).toEqual({ finalize_report: "Saving the report and evidence log" });
  });
  it("Planning, once done, keeps its slots: the plan's titles and a research note's own topic (spec §5.7, §6.3)", () => {
    const run = newRunState();
    applyEvent(run, ev("planner.planning.completed", { sub_topic_count: 2, note_topic_count: 1, sub_topics: [
      { coverage_id: "topic-01", title: "Published picks", state: "passed" },
      { coverage_id: "note-n1", title: "Your note: pastries in the cafe", note_id: "n1", state: "planned" },
    ] }));
    expect(body(rowBrief(run, "planner", "done", NOW), "planning").slots.filter((s) => !s.gone).map((s) => [s.n, s.title, s.fact]))
      .toEqual([[1, "Published picks", ""], [2, "Your note: pastries in the cafe", "from your note"]]);
    expect(run.topics.map((t) => t.coverageId)).toEqual(["topic-01", "note-n1"]);
  });
  it("ends Planning's and Reviewing's outcomes with their durations", () => {
    const run = play([
      ev("graph.node.started", { node: "planner", iteration: 0 }, at(0)),
      ev("planner.planning.completed", { sub_topic_count: 5, note_topic_count: 1, sub_topics: [] }),
      ev("graph.node.completed", { node: "planner" }, at(442)),
      ev("graph.node.started", { node: "report_reviewer", iteration: 0 }, at(500)),
      ev("graph.report.reviewed", { review_status: "scored", mean_score: 0.9, material_defects: 0, criteria: [], notes: [] }, at(595)),
      ev("graph.route.decided", { destination: "finalize", reason: "report_accepted", missing_required_target_ids: [] }),
      ev("graph.node.completed", { node: "report_reviewer" }, at(601)),
    ]);
    expect(rowBrief(run, "planner", "done", NOW).outcome).toBe("5 sub-topics · 1 from your note · 7m 22s");
    expect(rowBrief(run, "report_reviewer", "done", NOW).outcome).toBe("Accepted · all 5 met · 1m 41s");
  });
  it("every done row of a capture shows its outcome, with a duration on Planning's and Reviewing's", () => {
    const run = snapshots(captures[0].events).at(-1)!;
    for (const id of AGENT_ORDER) {
      const outcome = rowBrief(run, id, "done", NOW).outcome;
      if (id === "planner" || id === "report_reviewer") expect(outcome.startsWith(run.outcomes[id]! + " · ") && /\d+m \d\ds$/.test(outcome)).toBe(true);
      else expect(outcome).toBe(run.outcomes[id]);
    }
  });
  it("a looped row's first line is why it reopened", () => {
    const run = play([ev("graph.extra_pass.started", { iteration: 1, max_extra_passes: 1, targets: ["topic-01-target-01", "topic-02-target-01"] })]);
    expect(rowBrief(run, "researcher", "active", NOW).why).toEqual({ kind: "extra_pass", text: "Going back to research 2 gaps the review found" });
    applyEvent(run, ev("graph.report.redraft_requested", { iteration: 1, redrafts: 1, material_defects: 3 }));
    expect(rowBrief(run, "report_writer", "active", NOW).why).toEqual({ kind: "redraft", text: "Rewriting to fix 3 issues the review found" });
  });
});

describe("the stopped row and the rows after it (notes-progress-report spec §8.5, with §6's live facts)", () => {
  it("Researching counts its topics after 'Stopped' — 'none of' before one is done, never a bare 0", () => {
    const run = newRunState();
    expect(stoppedSubtitle(run, "researcher")).toBe("Stopped");
    applyEvent(run, ev("planner.planning.completed", { sub_topic_count: 3, sub_topics: [{ coverage_id: "topic-01", title: "A" }, { coverage_id: "topic-02", title: "B" }, { coverage_id: "topic-03", title: "C" }] }));
    expect(stoppedSubtitle(run, "researcher")).toBe("Stopped · none of 3 topics done");
    applyEvent(run, ev("researcher.sub_topic.completed", { coverage_id: "topic-02", sub_topic: "B", index: 2, successful_reads: 41, findings_retained: 212 }));
    expect(stoppedSubtitle(run, "researcher")).toBe("Stopped · 1 of 3 topics done · 41 pages read · 212 findings");
  });
  it("leaves out a count the run has not measured and prints one it measured, a measured 0 in words (D19)", () => {
    const run = newRunState();
    applyEvent(run, ev("planner.planning.completed", { sub_topic_count: 3, sub_topics: [{ coverage_id: "topic-01", title: "A" }, { coverage_id: "topic-02", title: "B" }, { coverage_id: "topic-03", title: "C" }] }));
    expect([run.pagesRead, run.findingsSoFar]).toEqual([null, null]);
    // Neither count is measured yet: the stopped line holds the topics alone, never "no pages read · no findings".
    expect(stoppedSubtitle(run, "researcher")).toBe("Stopped · none of 3 topics done");
    expect(text(rowBrief(run, "researcher", "stopped", NOW))).toBe("Stopped · none of 3 topics done");
    expect(rowBrief(run, "researcher", "active", NOW).subtitle).toMatchObject({ kind: "research", done: 0, pages: null, findings: null });
    expect(text(rowBrief(run, "researcher", "active", NOW))).toBe("3 topics · researching");
    // Only the findings are measured (the pass reported its total): that phrase prints, as "no findings".
    applyEvent(run, ev("researcher.research.completed", { sub_topics_researched: 0, sub_topics_skipped: 3, findings: 0 }));
    expect([run.pagesRead, run.findingsSoFar]).toEqual([null, 0]);
    expect(stoppedSubtitle(run, "researcher")).toBe("Stopped · none of 3 topics done · no findings");
    // A completed topic measures both: its zeros print in words.
    applyEvent(run, ev("researcher.sub_topic.completed", { coverage_id: "topic-01", sub_topic: "A", index: 1, successful_reads: 0, findings_retained: 0 }));
    expect(stoppedSubtitle(run, "researcher")).toBe("Stopped · 1 of 3 topics done · no pages read · no findings");
  });
  it("every other row reads 'Stopped · {its live facts}', frozen at the stop; with none, 'Stopped'", () => {
    const run = play([
      ev("graph.node.started", { node: "planner", iteration: 0 }, at(0)),
      ev("planner.progress", { step: "checking", check_round: 1, sub_topics: [{ coverage_id: "topic-01", title: "Alpha", state: "checking" }] }),
      ev("session.stopped", { step: "planner", stopped_at: at(125), elapsed_seconds: 125 }),
    ]);
    expect(stoppedSubtitle(run, "planner")).toBe("Stopped · 2m 05s");
    expect(text(rowBrief(run, "planner", "stopped", NOW))).toBe("Stopped · 2m 05s");
    expect(body(rowBrief(run, "planner", "stopped", NOW), "planning").slots[0]).toMatchObject({ mark: "stopped", fact: "stopped" });
    expect(stoppedSubtitle(newRunState(), "planner")).toBe("Stopped");
    expect((["source_evaluator", "evidence_verifier", "report_writer", "report_reviewer"] as NodeId[]).map((id) => stoppedSubtitle(newRunState(), id))).toEqual([
      "Stopped · starting · not yet rated", "Stopped · starting · not yet checked", "Stopped · starting · not yet written", "Stopped · reading the draft",
    ]);
  });
  it("freezes Reviewing's checks on 'stopped' when the stop came first", () => {
    const run = play([ev("graph.node.started", { node: "report_reviewer", iteration: 0 }, at(0)), ev("session.stopped", { step: "report_reviewer", stopped_at: at(30), elapsed_seconds: 30 })]);
    const r = body(rowBrief(run, "report_reviewer", "stopped", NOW), "reviewing");
    expect(r.waiting).toBe(false);
    expect(r.criteria[0]).toMatchObject({ mark: "waiting", before: "stopped", landed: false });
    expect(text(rowBrief(run, "report_reviewer", "stopped", NOW))).toBe("Stopped · reading the draft · 0m 30s");
  });
  // owner decision O2 (2026-10-01): the stopped Reviewing row keeps the review's own tense: reading while the call
  // ran when the reader stopped it, "read the draft in {elapsed}" once the review had landed.
  it("freezes Reviewing's subtitle in the past tense when the review had landed before the stop", () => {
    const run = play([
      ev("graph.node.started", { node: "report_reviewer", iteration: 0 }, at(0)),
      ev("graph.report.reviewed", { review_status: "scored", mean_score: 0.9, material_defects: 0, criteria: [], notes: [] }, at(95)),
      ev("session.stopped", { step: "report_reviewer", stopped_at: at(125), elapsed_seconds: 125 }),
    ]);
    expect(stoppedSubtitle(run, "report_reviewer")).toBe("Stopped · read the draft in 1m 35s");
    expect(text(rowBrief(run, "report_reviewer", "stopped", NOW))).toBe("Stopped · read the draft in 1m 35s");
  });
  it("a later row reads 'not run', or 'not run again' once a loop has re-armed it", () => {
    const run = newRunState();
    expect(notRunText(run, "source_evaluator")).toBe("not run");
    expect(text(rowBrief(run, "source_evaluator", "off", NOW))).toBe("not run");
    applyEvent(run, ev("graph.route.decided", { destination: "extra_pass", reason: "extra_pass_requested", iteration: 0 }));
    expect((["source_evaluator", "report_reviewer", "finalize_report"] as NodeId[]).map((id) => notRunText(run, id))).toEqual(["not run again", "not run again", "not run"]);
  });
});

describe("burst safety over the live captures (notes-progress-report AC19)", () => {
  for (const capture of captures) {
    it(`${capture.case_id}: every row's brief from a replay of events 1..k equals the live stream's`, () => {
      const snaps = snapshots(capture.events);
      capture.events.forEach((_, k) => {
        const replayed = replayRun(capture.events.slice(0, k + 1));
        for (const id of AGENT_ORDER) for (const state of ["active", "done"] as const) expect(rowBrief(replayed, id, state, NOW)).toEqual(rowBrief(snaps[k], id, state, NOW));
      });
    });
    it(`${capture.case_id}: Reviewing's brief never shows a score`, () => {
      for (const run of snapshots(capture.events)) expect(JSON.stringify(rowBrief(run, "report_reviewer", "active", NOW).body)).not.toMatch(/\d\.\d\d/);
    });
  }
});
