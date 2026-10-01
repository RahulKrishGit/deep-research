// @vitest-environment node — pure derivations over the live captures (same reason as run-state.test.ts).
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import type { ResearchEvent } from "../lib/api";
import { SENTENCES, STATIC_META, notRunText, rowBrief, stoppedSubtitle, subtitleText, topicFact, verifyingSentence } from "../lib/briefs";
import { AGENT_ORDER, applyEvent, newRunState, toRunEvent, type NodeId, type RunState } from "../lib/run-state";

interface Capture { case_id: string; events: ResearchEvent[] }
const load = (caseId: string): Capture =>
  JSON.parse(readFileSync(fileURLToPath(new URL(`./fixtures/events/${caseId}.json`, import.meta.url)), "utf8"));
const captures = [load("missing-target-triggers-one-extra-pass"), load("scoped-redraft-after-a-named-defect")];
function snapshots(events: ResearchEvent[]): RunState[] {
  const run = newRunState();
  return events.map((e) => { applyEvent(run, toRunEvent(e)); return structuredClone(run); });
}

describe("the Researching brief (spec §4.3, AC5)", () => {
  it("reads its static meta until topics are known, then '{n} topics · researching' until one is done", () => {
    const run = newRunState();
    expect(subtitleText(rowBrief(run, "researcher", "active").subtitle)).toBe("search · scrape · read · memory");
    applyEvent(run, { type: "planner.planning.completed", metadata: { sub_topic_count: 2, sub_topics: [{ coverage_id: "topic-01", title: "Alpha" }, { coverage_id: "topic-02", title: "Beta" }] } });
    expect(subtitleText(rowBrief(run, "researcher", "active").subtitle)).toBe("2 topics · researching");
    applyEvent(run, { type: "researcher.sub_topic.started", metadata: { coverage_id: "topic-02", sub_topic: "Beta", index: 2 } });
    applyEvent(run, { type: "researcher.sub_topic.completed", metadata: { coverage_id: "topic-02", sub_topic: "Beta", index: 2, successful_reads: 0, findings_retained: 0 } });
    expect(subtitleText(rowBrief(run, "researcher", "active").subtitle)).toBe("1 of 2 topics done · no pages read · no findings");
    const topics = rowBrief(run, "researcher", "active").topics!;
    expect(topics.map((t) => [t.n, t.title, t.state, t.fact])).toEqual([[1, "Alpha", "waiting", "not yet"], [2, "Beta", "done", "no findings"]]);
    applyEvent(run, { type: "researcher.sub_topic.started", metadata: { coverage_id: "topic-01", sub_topic: "Alpha", index: 1 } });
    expect(rowBrief(run, "researcher", "active").topics![0].fact).toBe("reading");
    applyEvent(run, { type: "researcher.sub_topic.completed", metadata: { coverage_id: "topic-01", sub_topic: "Alpha", index: 1, successful_reads: 1, findings_retained: 1 } });
    expect(subtitleText(rowBrief(run, "researcher", "active").subtitle)).toBe("2 of 2 topics done · 1 page read · 1 finding");
  });
  it("never renders a bare 0 or a dash, at any point of either capture", () => {
    for (const capture of captures) {
      for (const run of snapshots(capture.events)) {
        const brief = rowBrief(run, "researcher", "active");
        const texts = [subtitleText(brief.subtitle), rowBrief(run, "researcher", "done").outcome, ...brief.topics!.map((t) => t.fact)];
        for (const text of texts) { expect(text).not.toMatch(/(^|\D)0(\D|$)/); expect(text).not.toContain("—"); }
      }
    }
  });
});

describe("every other row", () => {
  it("shows its static meta as the subtitle and one plain sentence", () => {
    const run = newRunState();
    for (const id of ["source_evaluator", "report_writer", "report_reviewer", "finalize_report"] as const) {
      const brief = rowBrief(run, id, "active");
      expect(brief.subtitle).toEqual({ kind: "text", text: STATIC_META[id] });
      expect(brief.sentence).toBe(SENTENCES[id]);
      expect(brief.topics).toBeNull();
    }
    expect(rowBrief(run, "planner", "active").sentence).toBe("Breaking your question into sub-topics…");
  });
  it("Verifying counts the findings of the pass it verifies", () => {
    expect(verifyingSentence(null)).toBe("Checking findings against their pages");
    expect(verifyingSentence(0)).toBe("No findings to check");
    expect(verifyingSentence(1)).toBe("Checking 1 finding against its page");
    expect(verifyingSentence(4)).toBe("Checking 4 findings against their pages");
    const run = newRunState();
    applyEvent(run, { type: "researcher.research.completed", metadata: { sub_topics_researched: 3, sub_topics_skipped: 0, findings: 4 } });
    expect(rowBrief(run, "evidence_verifier", "active").sentence).toBe("Checking 4 findings against their pages");
  });
  it("Planning, once done, lists the sub-topic titles; every done row shows its outcome", () => {
    const [capture] = captures;
    const run = snapshots(capture.events).at(-1)!;
    const planning = rowBrief(run, "planner", "done");
    expect(planning.titles).toEqual(run.plan.map((p) => p.title));
    expect(planning.sentence).toBeNull();
    for (const id of AGENT_ORDER) expect(rowBrief(run, id, "done").outcome).toBe(run.outcomes[id]);
  });
  it("a looped row's first line is why it reopened", () => {
    const run = newRunState();
    applyEvent(run, { type: "graph.extra_pass.started", metadata: { iteration: 1, max_extra_passes: 1, targets: ["topic-01-target-01", "topic-02-target-01"] } });
    expect(rowBrief(run, "researcher", "active").why).toEqual({ kind: "extra_pass", text: "Going back to research 2 gaps the review found" });
    applyEvent(run, { type: "graph.report.redraft_requested", metadata: { iteration: 1, redrafts: 1, material_defects: 3 } });
    expect(rowBrief(run, "report_writer", "active").why).toEqual({ kind: "redraft", text: "Rewriting to fix 3 issues the review found" });
  });
  it("topicFact follows the spec's table", () => {
    expect(topicFact({ coverageId: "a", title: "A", state: "waiting", findings: null })).toBe("not yet");
    expect(topicFact({ coverageId: "a", title: "A", state: "running", findings: null })).toBe("reading");
    expect(topicFact({ coverageId: "a", title: "A", state: "done", findings: 1 })).toBe("1 finding");
    expect(topicFact({ coverageId: "a", title: "A", state: "done", findings: 9 })).toBe("9 findings");
  });
});

describe("the stopped row and the rows after it (notes-progress-report spec §8.5)", () => {
  it("Researching counts its topics after 'Stopped' — 'none of' before one is done, never a bare 0 — and a row with no live facts reads 'Stopped'", () => {
    const run = newRunState();
    expect(stoppedSubtitle(run, "researcher")).toBe("Stopped");
    applyEvent(run, { type: "planner.planning.completed", metadata: { sub_topic_count: 3, sub_topics: [{ coverage_id: "topic-01", title: "A" }, { coverage_id: "topic-02", title: "B" }, { coverage_id: "topic-03", title: "C" }] } });
    expect(stoppedSubtitle(run, "researcher")).toBe("Stopped · none of 3 topics done · no pages read · no findings");
    applyEvent(run, { type: "researcher.sub_topic.completed", metadata: { coverage_id: "topic-02", sub_topic: "B", index: 2, successful_reads: 41, findings_retained: 212 } });
    expect(stoppedSubtitle(run, "researcher")).toBe("Stopped · 1 of 3 topics done · 41 pages read · 212 findings");
    expect((["planner", "source_evaluator", "evidence_verifier", "report_writer", "report_reviewer"] as NodeId[]).map((id) => stoppedSubtitle(run, id))).toEqual(Array(5).fill("Stopped"));
  });
  it("a later row reads 'not run', or 'not run again' once a loop has re-armed it", () => {
    const run = newRunState();
    expect(notRunText(run, "source_evaluator")).toBe("not run");
    applyEvent(run, { type: "graph.route.decided", metadata: { destination: "extra_pass", reason: "extra_pass_requested", iteration: 0 } });
    expect((["source_evaluator", "report_reviewer", "finalize_report"] as NodeId[]).map((id) => notRunText(run, id))).toEqual(["not run again", "not run again", "not run"]);
  });
});
