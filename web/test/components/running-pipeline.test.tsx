import { act, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RunningPipeline } from "../../components/RunningPipeline";
import { applyEvent, newRunState } from "../../lib/run-state";

afterEach(() => { vi.useRealTimers(); });

describe("RunningPipeline — the removals", () => {
  it("has no Now header, no counters block and no pass text; the card holds the spine", () => {
    const run = newRunState();
    run.active = "researcher";
    run.marks = { planner: "done" };
    const { container } = render(<RunningPipeline sessionId="s1" run={run} question="q" strip={null} startedAt="2026-09-27T00:00:00+00:00" onToggleRow={() => {}} />);
    for (const gone of [".pipe-now", "#runNow", "#runPasses", "#runLoopTag", "#runTrack", "#runCounters", ".counters"]) expect(container.querySelector(gone)).toBeNull();
    expect(container.querySelector("#stage-running")!.textContent).not.toMatch(/\bpass\b/i);
    expect(container.querySelectorAll("#spine li[data-stage]")).toHaveLength(7);
    expect(container.querySelector('#spine li[data-stage="researcher"]')!.getAttribute("data-state")).toBe("active");
  });
});

describe("RunningPipeline — the steps' clock", () => {
  it("ticks the active step's elapsed time with the page's one-second clock", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-30T12:00:05Z"));
    const run = newRunState();
    applyEvent(run, { type: "graph.node.started", metadata: { node: "planner", iteration: 0 }, timestamp: "2026-09-30T12:00:00+00:00" });
    const { container } = render(<RunningPipeline sessionId="s1" run={run} question="q" strip={null} startedAt="2026-09-30T12:00:00+00:00" onToggleRow={() => {}} />);
    const live = () => container.querySelector('#spine li[data-stage="planner"] .m-live')!.textContent;
    expect(live()).toBe("0m 05s");
    act(() => { vi.advanceTimersByTime(1000); });
    expect(live()).toBe("0m 06s");
  });
});
