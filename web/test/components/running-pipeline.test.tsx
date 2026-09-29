import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { RunningPipeline } from "../../components/RunningPipeline";
import { newRunState } from "../../lib/run-state";

describe("RunningPipeline — the removals (live-briefs spec §4.2, AC3)", () => {
  it("has no Now header, no counters block and no pass text; the card holds the spine", () => {
    const run = newRunState(2);
    run.active = "researcher";
    run.marks = { planner: "done" };
    const { container } = render(<RunningPipeline sessionId="s1" run={run} question="q" strip={null} startedAt="2026-09-27T00:00:00+00:00" />);
    for (const gone of [".pipe-now", "#runNow", "#runPasses", "#runLoopTag", "#runTrack", "#runCounters", ".counters"]) expect(container.querySelector(gone)).toBeNull();
    expect(container.querySelector("#stage-running")!.textContent).not.toMatch(/\bpass\b/i);
    expect(container.querySelectorAll("#spine li[data-stage]")).toHaveLength(7);
    expect(container.querySelector('#spine li[data-stage="researcher"]')!.getAttribute("data-state")).toBe("active");
  });
});
