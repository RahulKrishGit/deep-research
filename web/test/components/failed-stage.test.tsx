import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { FailedStage } from "../../components/FailedStage";
import type { ResearchError, ResearchSessionResponse } from "../../lib/api";
import { applyEvent, newRunState } from "../../lib/run-state";

const failed = (error: ResearchError): ResearchSessionResponse => ({
  session_id: "s", query: "q", status: "failed", current_agent: null, iteration: 0, started_at: "2026-09-27T10:00:00Z",
  finished_at: "2026-09-27T10:00:04Z", report_path: null, trace_url: null, errors: [error], evidence_path: null, quality_path: null,
  quality_contract_version: null, semantic_review_status: null, semantic_review_score: null, duration_seconds: null, coverage: null, evidence_counts: null,
});

describe("FailedStage", () => {
  it("headlines an API-level configuration failure with its enumerated reason and no download", () => {
    const status = failed({ error_type: "api.research.configuration_error", source: "api", message: "Research service configuration is unavailable.", recoverable: false, timestamp: "", details: { reason: "config_invalid" } });
    const run = newRunState(2);
    const { container } = render(<FailedStage status={status} run={run} strip={null} />);
    expect(container.querySelector("#failedType")!.textContent).toBe("Service configuration error");
    expect(container.querySelector("#failedMessage")!.textContent).toBe("Research service configuration is unavailable.");
    expect(container.querySelector("#failFactType")!.textContent).toBe("api.research.configuration_error");
    expect(container.querySelector("#failFactReason")!.textContent).toBe("config_invalid");
    expect(container.querySelector("#failedHaltedAt")!.textContent).toBe("halted before the first stage");
    expect(container.querySelector('#spineFailed li[data-stage="finalize_report"]')!.getAttribute("data-state")).toBe("skipped");
    expect(container.querySelectorAll('#spineFailed li[data-state="done"]').length).toBe(0);
    for (const dd of container.querySelectorAll("#failedCounters dd")) expect(dd.textContent).toBe("not reached");
    expect(container.querySelector("#downloadBtn")).toBeNull();
    expect(container.querySelector("button[disabled], a[aria-disabled]")).toBeNull();
  });
  it("headlines a graph halt in plain words and marks the halting row", () => {
    const status = failed({ error_type: "graph_provider_configuration_error", source: "graph", message: "The model provider is not configured, so the research run stopped.", recoverable: false, timestamp: "", details: { exception_type: "ValidationError" } });
    const run = newRunState(2);
    applyEvent(run, { type: "graph.session.started", metadata: { max_extra_passes: 1 } });
    applyEvent(run, { type: "graph.node.started", metadata: { node: "planner", iteration: 0 } });
    const { container } = render(<FailedStage status={status} run={run} strip={null} />);
    expect(container.querySelector("#failedType")!.textContent).toBe("Model provider misconfigured");
    expect(container.querySelector("#failedHaltedAt")!.textContent).toBe("halted at stage 1");
    expect(container.querySelector('#spineFailed li[data-stage="planner"]')!.getAttribute("data-state")).toBe("active");
  });
  it("K19: omits the source fact row rather than inventing 'graph' when the record carries none", () => {
    const status = failed({ error_type: "graph_invalid_route", source: "", message: "The graph reached an unregistered route.", recoverable: false, timestamp: "", details: {} });
    const run = newRunState(2);
    const { container } = render(<FailedStage status={status} run={run} strip={null} />);
    expect(container.querySelector("#failFactSource")).toBeNull();
    expect([...container.querySelectorAll("dd")].some((dd) => dd.textContent === "graph")).toBe(false);
  });
});
