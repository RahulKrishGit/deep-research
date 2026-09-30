import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Spine } from "../../components/Spine";
import { failedMarks, marksFor, newRunState } from "../../lib/run-state";

describe("Spine", () => {
  it("paints data-state per row, data-fed from the previous row, and the caption", () => {
    const run = newRunState();
    run.marks = { planner: "done", researcher: "loop" };
    run.active = "source_evaluator";
    run.rearmedFirst = "researcher";
    run.captions.researcher = "1 missing target only";
    const { container } = render(<Spine marks={marksFor(run, run.active)} run={run} withArcs={false} />);
    const rows = [...container.querySelectorAll("li[data-stage]")];
    expect(rows.map((r) => r.getAttribute("data-stage"))).toEqual(["planner", "researcher", "source_evaluator", "evidence_verifier", "report_writer", "report_reviewer", "finalize_report"]);
    expect(rows.map((r) => r.getAttribute("data-state"))).toEqual(["done", "loop", "active", "pending", "pending", "pending", "pending"]);
    expect(rows[1].getAttribute("data-fed")).toBe("1");
    expect(rows[3].getAttribute("data-fed")).toBe("0");
    expect(rows[1].querySelector(".stage-meta")!.textContent).toBe("1 missing target only");
    expect(rows[1].querySelector(".loops")!.textContent).toBe("↺");
    expect(rows[2].querySelector(".stage-name .sr")!.textContent).toBe(" (in progress)");
  });
  it("marks a halted run's Publishing row skipped", () => {
    const run = newRunState();
    run.openNode = "planner";
    const { container } = render(<Spine marks={failedMarks(run, "failed")} run={run} withArcs={false} />);
    expect(container.querySelector('li[data-stage="finalize_report"]')!.getAttribute("data-state")).toBe("skipped");
    expect(container.querySelector('li[data-stage="planner"]')!.getAttribute("data-state")).toBe("active");
  });
});
