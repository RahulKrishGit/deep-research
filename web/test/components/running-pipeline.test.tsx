import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { RunningPipeline } from "../../components/RunningPipeline";
import { newRunState } from "../../lib/run-state";

describe("RunningPipeline — pass text (K15)", () => {
  it("shows pass N of the known ceiling", () => {
    const run = newRunState(2);
    const { container } = render(<RunningPipeline run={run} question="q" strip={null} startedAt="2026-09-27T00:00:00+00:00" ceiling={2} />);
    expect(container.querySelector("#runPasses")!.textContent).toBe("pass 1 of 2");
  });
  it("drops the 'of P' clause while the ceiling is unknown, never showing 'pass 1 of 1'", () => {
    // No settings recorded yet and no graph.session.started event processed: run.maxPasses
    // defaults to 1, but that is not a known ceiling of 1 — it must never read "pass 1 of 1".
    const run = newRunState(null);
    const { container } = render(<RunningPipeline run={run} question="q" strip={null} startedAt="2026-09-27T00:00:00+00:00" ceiling={null} />);
    expect(container.querySelector("#runPasses")!.textContent).toBe("pass 1");
  });
});
