import { act, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { BriefSpine, HANDOFF_HOLD_MS } from "../../components/BriefSpine";
import { applyEvent, marksFor, newRunState, toggleOpen, type NodeId, type RunState } from "../../lib/run-state";

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

const TITLES = [{ coverage_id: "topic-01", title: "Adoption rate" }, { coverage_id: "topic-02", title: "Widget funding" }, { coverage_id: "topic-03", title: "Widget exports" }];
/* Planning done, Researching active with topic 2 done, topic 1 running, topic 3 waiting. */
function researching(): RunState {
  const run = newRunState();
  for (const [type, metadata] of [
    ["graph.node.started", { node: "planner", iteration: 0 }],
    ["planner.planning.completed", { sub_topic_count: 3, sub_topics: TITLES }],
    ["graph.node.completed", { node: "planner" }],
    ["graph.node.started", { node: "researcher", iteration: 0 }],
    ["researcher.sub_topic.started", { coverage_id: "topic-01", sub_topic: "Adoption rate", index: 1 }],
    ["researcher.sub_topic.started", { coverage_id: "topic-02", sub_topic: "Widget funding", index: 2 }],
    ["researcher.sub_topic.completed", { coverage_id: "topic-02", sub_topic: "Widget funding", index: 2, successful_reads: 2, findings_retained: 2 }],
  ] as const) applyEvent(run, { type, metadata: { ...metadata } });
  return run;
}
const row = (c: HTMLElement, id: NodeId) => c.querySelector<HTMLElement>(`#spine > li[data-stage="${id}"]`)!;
const show = (run: RunState, onToggle = vi.fn()) => render(<BriefSpine marks={marksFor(run, run.active)} run={run} onToggle={onToggle} />);

describe("BriefSpine — row anatomy (live-briefs spec §4.3, AC4, AC5)", () => {
  it("keeps the seven li.spine-row[data-stage] rows in ol#spine inside #spineWrap", () => {
    const { container } = show(researching());
    expect([...container.querySelectorAll("#spineWrap > ol#spine.spine-lg.briefs > li.spine-row[data-stage]")].map((li) => li.getAttribute("data-stage")))
      .toEqual(["planner", "researcher", "source_evaluator", "evidence_verifier", "report_writer", "report_reviewer", "finalize_report"]);
  });
  it("opens the active row, marks it aria-current=step, and lists every sub-topic with its mark and fact", () => {
    const { container } = show(researching());
    const li = row(container, "researcher");
    expect(li.getAttribute("data-state")).toBe("active");
    expect(li.getAttribute("data-open")).toBe("1");
    expect(li.getAttribute("aria-current")).toBe("step");
    expect(li.querySelector("button.ps-toggle")).toBeNull();
    expect(li.querySelector(".m-live")!.textContent).toBe("1 of 3 topics done · 2 pages read · 2 findings");
    const topics = [...li.querySelectorAll(".ps-topics > [data-topic]")];
    expect(topics.map((t) => [t.getAttribute("data-topic"), t.querySelector(".tn")!.textContent, t.querySelector(".tt")!.textContent, t.querySelector(".tf")!.textContent]))
      .toEqual([["running", "1", "1Adoption rate", "reading"], ["done", "2", "2Widget funding", "2 findings"], ["waiting", "3", "3Widget exports", "not yet"]]);
    expect(topics.map((t) => t.getAttribute("style"))).toEqual(["--i: 0;", "--i: 1;", "--i: 2;"]);
  });
  it("closes a done row on its outcome line behind a toggle button that reopens it", () => {
    const onToggle = vi.fn();
    const run = researching();
    const { container, rerender } = show(run, onToggle);
    const planning = row(container, "planner");
    expect(planning.getAttribute("data-open")).toBe("0");
    expect(planning.querySelector(".m-out")!.textContent).toBe("3 sub-topics");
    const head = planning.querySelector<HTMLButtonElement>("button.ps-toggle")!;
    expect(head.getAttribute("aria-expanded")).toBe("false");
    expect(head.getAttribute("aria-controls")).toBe("brief-planner");
    expect(planning.querySelector("#brief-planner")!.getAttribute("aria-hidden")).toBe("true");
    fireEvent.click(head);
    expect(onToggle).toHaveBeenCalledWith("planner");
    toggleOpen(run, "planner");
    rerender(<BriefSpine marks={marksFor(run, run.active)} run={run} onToggle={onToggle} />);
    expect(planning.getAttribute("data-open")).toBe("1");
    expect(head.getAttribute("aria-expanded")).toBe("true");
    expect(planning.querySelector("#brief-planner")!.hasAttribute("aria-hidden")).toBe(false);
    expect([...planning.querySelectorAll(".ps-slots > .ln:not([data-gone]) .tt")].map((t) => t.textContent)).toEqual(["1Adoption rate", "2Widget funding", "3Widget exports"]);
  });
  it("never opens a pending row, even if asked", () => {
    const run = researching();
    toggleOpen(run, "source_evaluator");
    const { container } = show(run);
    const li = row(container, "source_evaluator");
    expect(li.getAttribute("data-state")).toBe("pending");
    expect(li.getAttribute("data-open")).toBe("0");
    expect(li.querySelector("button")).toBeNull();
    expect(li.querySelector(".m-live")!.textContent).toBe("authority · recency · relevance");
  });
  it("draws the connector from the upper row: data-fed stays on the lower row", () => {
    const { container } = show(researching());
    expect(row(container, "researcher").getAttribute("data-fed")).toBe("1");
    expect(row(container, "source_evaluator").getAttribute("data-fed")).toBe("0");
    expect(row(container, "planner").hasAttribute("data-fed")).toBe(false);
  });
  it("opens a looped Researching on why it reopened", () => {
    const run = researching();
    for (const [type, metadata] of [
      ["researcher.research.completed", { sub_topics_researched: 3, sub_topics_skipped: 0, findings: 4 }],
      ["graph.route.decided", { destination: "extra_pass", reason: "extra_pass_requested", missing_required_target_ids: ["topic-01-target-01"] }],
      ["graph.extra_pass.started", { iteration: 1, max_extra_passes: 1, targets: ["topic-01-target-01"] }],
    ] as const) applyEvent(run, { type, metadata: { ...metadata } });
    const { container } = show(run);
    const why = row(container, "researcher").querySelector(".b-why")!;
    expect(why.textContent).toBe("Going back to research 1 gap the review found");
    expect(why.getAttribute("data-kind")).toBe("extra_pass");
    expect(why.getAttribute("style")).toBe("--i: 0;");
    expect(row(container, "researcher").querySelectorAll(".ps-topics > [data-topic]")).toHaveLength(0);
  });
});

describe("BriefSpine — the hand-off roles (spec §4.3 motion table, pick 3B)", () => {
  it("marks the row that finished 'from' and the next 'to' for HANDOFF_HOLD_MS, then clears both", () => {
    vi.useFakeTimers();
    const run = researching();
    const { container, rerender } = show(run);
    expect(row(container, "researcher").hasAttribute("data-handoff")).toBe(false);
    const head = row(container, "researcher").querySelector(".ps-head");
    const subtitle = row(container, "researcher").querySelector(".stage-meta.xf");
    applyEvent(run, { type: "researcher.research.completed", metadata: { sub_topics_researched: 3, sub_topics_skipped: 0, findings: 4 } });
    applyEvent(run, { type: "graph.node.completed", metadata: { node: "researcher" } });
    rerender(<BriefSpine marks={marksFor(run, run.active)} run={run} onToggle={() => {}} />);
    // The same head and subtitle elements, now showing the outcome: a remounted one could not cross-fade.
    expect(row(container, "researcher").querySelector(".ps-head")).toBe(head);
    expect(row(container, "researcher").querySelector(".stage-meta.xf")).toBe(subtitle);
    expect(row(container, "researcher").querySelector(".m-out")!.textContent).toBe("3 topics · 2 pages read · 4 findings");
    expect(row(container, "researcher").querySelector("button.ps-toggle")!.getAttribute("aria-labelledby")).toBe("name-researcher meta-researcher");
    expect(row(container, "researcher").getAttribute("data-handoff")).toBe("from");
    expect(row(container, "source_evaluator").getAttribute("data-handoff")).toBe("to");
    expect(row(container, "source_evaluator").getAttribute("data-open")).toBe("1");
    expect(row(container, "researcher").getAttribute("data-open")).toBe("0");
    act(() => { vi.advanceTimersByTime(HANDOFF_HOLD_MS); });
    expect(container.querySelector("[data-handoff]")).toBeNull();
  });
  it("awaits the row a route decision leaves until its own completion, then hands it off (Reviewing → Publishing)", () => {
    vi.useFakeTimers();
    const run = newRunState();
    for (const node of ["planner", "researcher", "source_evaluator", "evidence_verifier", "report_writer"] as const) {
      applyEvent(run, { type: "graph.node.started", metadata: { node, iteration: 0 } });
      applyEvent(run, { type: "graph.node.completed", metadata: { node } });
    }
    applyEvent(run, { type: "graph.node.started", metadata: { node: "report_reviewer", iteration: 0 } });
    const { container, rerender } = show(run);
    const again = () => rerender(<BriefSpine marks={marksFor(run, run.active)} run={run} onToggle={() => {}} />);
    applyEvent(run, { type: "graph.report.reviewed", metadata: { mean_score: 0.9 } });
    applyEvent(run, { type: "graph.route.decided", metadata: { destination: "finalize", reason: "report_accepted" } });
    again();
    // The route decision moved the active row one event before Reviewing's own completion.
    expect(run.active).toBe("finalize_report");
    expect(row(container, "report_reviewer").getAttribute("data-state")).toBe("active");
    expect(row(container, "report_reviewer").getAttribute("data-open")).toBe("1");
    expect(row(container, "report_reviewer").hasAttribute("aria-current")).toBe(false);
    expect(row(container, "report_reviewer").hasAttribute("data-handoff")).toBe(false);
    expect(row(container, "finalize_report").getAttribute("data-handoff")).toBe("to");
    expect(row(container, "finalize_report").getAttribute("aria-current")).toBe("step");
    applyEvent(run, { type: "graph.node.completed", metadata: { node: "report_reviewer" } });
    again();
    expect(row(container, "report_reviewer").getAttribute("data-state")).toBe("done");
    expect(row(container, "report_reviewer").getAttribute("data-handoff")).toBe("from");
    expect(row(container, "report_reviewer").getAttribute("data-open")).toBe("0");
    expect(row(container, "report_reviewer").querySelector(".m-out")!.textContent).toBe("Accepted · all 5 met");
    expect(row(container, "finalize_report").getAttribute("data-handoff")).toBe("to");
    act(() => { vi.advanceTimersByTime(HANDOFF_HOLD_MS); });
    expect(container.querySelector("[data-handoff]")).toBeNull();
  });
  it("never awaits a row a loop sends the run back from: the hold's own timer hands it over (D39)", () => {
    vi.useFakeTimers();
    const run = newRunState();
    for (const node of ["planner", "researcher", "source_evaluator", "evidence_verifier", "report_writer"] as const) {
      applyEvent(run, { type: "graph.node.started", metadata: { node, iteration: 0 } });
      applyEvent(run, { type: "graph.node.completed", metadata: { node } });
    }
    applyEvent(run, { type: "graph.node.started", metadata: { node: "report_reviewer", iteration: 0 } });
    const { container, rerender } = show(run);
    applyEvent(run, { type: "graph.route.decided", metadata: { destination: "extra_pass", reason: "extra_pass_requested", missing_required_target_ids: ["topic-01-target-01"] } });
    rerender(<BriefSpine marks={marksFor(run, run.active)} run={run} onToggle={() => {}} />);
    expect([row(container, "report_reviewer").getAttribute("data-state"), row(container, "report_reviewer").getAttribute("data-open")]).toEqual(["active", "1"]);
    expect(row(container, "researcher").getAttribute("data-state")).toBe("pending");
    // A loop's completion of the reviewer is inert, so none ever releases the row: the timer does.
    act(() => { vi.advanceTimersByTime(HANDOFF_HOLD_MS); });
    expect(row(container, "report_reviewer").getAttribute("data-state")).toBe("pending");
    expect(row(container, "report_reviewer").getAttribute("data-open")).toBe("0");
    expect(row(container, "researcher").getAttribute("data-state")).toBe("active");
  });
  it("a first render is not a hand-off", () => {
    const { container } = show(researching());
    expect(container.querySelector("[data-handoff]")).toBeNull();
  });
});

describe("BriefSpine — the arcs stay attached while rows change height", () => {
  it("observes the wrap's size", () => {
    const observe = vi.fn();
    vi.stubGlobal("ResizeObserver", class { observe = observe; unobserve() {} disconnect() {} });
    const { container } = show(researching());
    expect(observe).toHaveBeenCalledWith(container.querySelector("#spineWrap"));
  });
});

describe("BriefSpine — frozen at the stopped row (notes-progress-report spec §8.5)", () => {
  const frozenAt = (id: NodeId) => {
    const run = researching();
    return render(<BriefSpine marks={marksFor(run, run.active)} run={run} onToggle={vi.fn()} frozen={id} />);
  };
  it("keeps the outcome out of the frozen and not-run rows, so a row is as tall as its own line (phase review P3-1)", () => {
    const { container } = frozenAt("researcher");
    const states = ["planner", "researcher", "source_evaluator", "evidence_verifier", "report_writer", "report_reviewer", "finalize_report"]
      .map((id) => [row(container, id as NodeId).getAttribute("data-state"), row(container, id as NodeId).querySelector(".m-out")!.textContent]);
    expect(states).toEqual([
      ["done", "3 sub-topics"], ["stopped", ""],
      ["off", ""], ["off", ""], ["off", ""], ["off", ""], ["off", ""],
    ]);
  });
  it("reads a topic that never started 'not run', and one that was running 'stopped' (phase review P3-2)", () => {
    const { container } = frozenAt("researcher");
    const topics = [...row(container, "researcher").querySelectorAll(".ps-topics > [data-topic]")];
    expect(topics.map((t) => [t.getAttribute("data-topic"), t.querySelector(".tf")!.textContent])).toEqual([
      ["stopped", "stopped"], ["done", "2 findings"], ["waiting", "not run"],
    ]);
  });
  it("leaves a waiting topic reading 'not yet' on the running stage", () => {
    const { container } = show(researching());
    const topics = [...row(container, "researcher").querySelectorAll(".ps-topics > [data-topic]")];
    expect(topics.map((t) => [t.getAttribute("data-topic"), t.querySelector(".tf")!.textContent])).toEqual([
      ["running", "reading"], ["done", "2 findings"], ["waiting", "not yet"],
    ]);
  });
});

describe("BriefSpine — a loop route holds Reviewing on its checks and verdict (decision D39)", () => {
  /* Writing done and Reviewing's call running; then the review lands with one criterion not met and the
     route sends the draft back to the writer, with `also` applied in the same render. */
  function sentBack(also: Parameters<typeof applyEvent>[1][] = []) {
    vi.useFakeTimers();
    const run = newRunState();
    for (const node of ["planner", "researcher", "source_evaluator", "evidence_verifier", "report_writer"] as const) {
      applyEvent(run, { type: "graph.node.started", metadata: { node, iteration: 0 } });
      applyEvent(run, { type: "graph.node.completed", metadata: { node } });
    }
    applyEvent(run, { type: "graph.node.started", metadata: { node: "report_reviewer", iteration: 0 } });
    const onToggle = (id: NodeId) => toggleOpen(run, id);
    const { container, rerender } = render(<BriefSpine marks={marksFor(run, run.active)} run={run} onToggle={onToggle} />);
    const again = () => rerender(<BriefSpine marks={marksFor(run, run.active)} run={run} onToggle={onToggle} />);
    const criteria = ["completeness", "evidence_quality", "attribution", "uncertainty", "readability"]
      .map((dimension) => ({ dimension, met: dimension !== "completeness", kinds: dimension === "completeness" ? ["coverage"] : [] }));
    applyEvent(run, { type: "graph.report.reviewed", metadata: { review_status: "scored", mean_score: 0.6, material_defects: 1, criteria, notes: [] } });
    applyEvent(run, { type: "graph.route.decided", metadata: { destination: "redraft", reason: "redraft_requested", missing_required_target_ids: [] } });
    for (const event of also) applyEvent(run, event);
    again();
    return { run, container, again };
  }
  const VERDICT = "1 thing to fix · sending the draft back to the writer";

  it("keeps Reviewing open on its five checks and its verdict while Writing waits closed; then hands over, and Reviewing reopens", () => {
    const { run, container, again } = sentBack();
    // The reviewer's own completion after a loop route is inert; the redraft's start says why Writing reopens.
    applyEvent(run, { type: "graph.node.completed", metadata: { node: "report_reviewer" } });
    applyEvent(run, { type: "graph.report.redraft_requested", metadata: { iteration: 0, redrafts: 1, material_defects: 1 } });
    again();
    const reviewing = row(container, "report_reviewer"), writing = row(container, "report_writer");
    expect([reviewing.getAttribute("data-state"), reviewing.getAttribute("data-open"), reviewing.hasAttribute("aria-current"), reviewing.hasAttribute("data-handoff")])
      .toEqual(["active", "1", false, false]);
    expect(reviewing.querySelectorAll(".rv-list > .ln")).toHaveLength(5);
    expect([...reviewing.querySelectorAll(".rv-list > .ln[data-topic='fail'] .tt")].map((t) => t.textContent)).toEqual(["Covers your whole question"]);
    expect(reviewing.querySelector(".b-now.xf > [data-on='1']")!.textContent).toBe(VERDICT);
    expect([writing.getAttribute("data-state"), writing.getAttribute("data-open"), writing.hasAttribute("data-handoff"), writing.hasAttribute("aria-current")])
      .toEqual(["pending", "0", false, false]);

    act(() => { vi.advanceTimersByTime(HANDOFF_HOLD_MS); });
    expect([reviewing.getAttribute("data-state"), reviewing.getAttribute("data-open"), reviewing.getAttribute("data-handoff")]).toEqual(["pending", "0", "from"]);
    expect([writing.getAttribute("data-state"), writing.getAttribute("data-open"), writing.getAttribute("data-handoff"), writing.getAttribute("aria-current")])
      .toEqual(["active", "1", "to", "step"]);
    expect(writing.querySelector(".b-why")!.textContent).toBe("Rewriting to fix 1 issue the review found");

    // The hollow row reopens from its head, on the same checks and verdict.
    const toggle = reviewing.querySelector<HTMLButtonElement>("button.ps-toggle")!;
    expect([reviewing.getAttribute("data-toggle"), toggle.getAttribute("aria-expanded")]).toEqual(["1", "false"]);
    fireEvent.click(toggle);
    again();
    expect([reviewing.getAttribute("data-state"), reviewing.getAttribute("data-open")]).toEqual(["pending", "1"]);
    expect(reviewing.querySelector(".b-now.xf > [data-on='1']")!.textContent).toBe(VERDICT);
    expect(reviewing.querySelectorAll(".rv-list > .ln[data-topic='fail']")).toHaveLength(1);

    // Reviewing running again starts its block clean: nothing to reopen.
    applyEvent(run, { type: "graph.node.started", metadata: { node: "report_reviewer", iteration: 1 } });
    again();
    expect(reviewing.querySelector("button.ps-toggle")).toBeNull();
  });

  it("holds Reviewing even when the loop's own start event lands in the same render as the route decision", () => {
    // The redraft's start settles the loop (run.loop "settled"); the lit arc still names Writing.
    const { container } = sentBack([
      { type: "graph.node.completed", metadata: { node: "report_reviewer" } },
      { type: "graph.report.redraft_requested", metadata: { iteration: 0, redrafts: 1, material_defects: 1 } },
    ]);
    expect([row(container, "report_reviewer").getAttribute("data-state"), row(container, "report_reviewer").getAttribute("data-open")]).toEqual(["active", "1"]);
    expect(row(container, "report_writer").getAttribute("data-state")).toBe("pending");
    act(() => { vi.advanceTimersByTime(HANDOFF_HOLD_MS); });
    expect(row(container, "report_writer").getAttribute("data-handoff")).toBe("to");
  });

  it("a Stop during the hold ends it: Reviewing paints pending and no row takes a hand-off role", () => {
    const { run, container, again } = sentBack();
    act(() => { vi.advanceTimersByTime(500); });
    applyEvent(run, { type: "session.stopped", metadata: { step: "report_writer", stopped_at: "2026-09-30T12:00:00+00:00", elapsed_seconds: 60 } });
    again();
    expect(row(container, "report_reviewer").getAttribute("data-state")).toBe("pending");
    expect(container.querySelector("[data-handoff]")).toBeNull();
    act(() => { vi.advanceTimersByTime(HANDOFF_HOLD_MS); });
    expect(container.querySelector("[data-handoff]")).toBeNull();
  });
});
