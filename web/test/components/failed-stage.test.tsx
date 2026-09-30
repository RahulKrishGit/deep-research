import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { FailedStage } from "../../components/FailedStage";
import type { ResearchError, ResearchSessionResponse } from "../../lib/api";
import { applyEvent, newRunState } from "../../lib/run-state";

const failed = (...errors: ResearchError[]): ResearchSessionResponse => ({
  session_id: "s", query: "q", status: "failed", current_agent: null, iteration: 0, started_at: "2026-09-27T10:00:00Z",
  finished_at: "2026-09-27T10:00:04Z", report_path: null, trace_url: null, errors, evidence_path: null, quality_path: null,
  quality_contract_version: null, semantic_review_status: null, semantic_review_score: null, duration_seconds: null, coverage: null, evidence_counts: null,
});

describe("FailedStage", () => {
  it("headlines an API-level configuration failure with its enumerated reason and no download", () => {
    const status = failed({ error_type: "api.research.configuration_error", source: "api", message: "Research service configuration is unavailable.", recoverable: false, timestamp: "", details: { reason: "config_invalid" } });
    const run = newRunState();
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
    const run = newRunState();
    applyEvent(run, { type: "graph.session.started", metadata: { max_extra_passes: 1 } });
    applyEvent(run, { type: "graph.node.started", metadata: { node: "planner", iteration: 0 } });
    const { container } = render(<FailedStage status={status} run={run} strip={null} />);
    expect(container.querySelector("#failedType")!.textContent).toBe("Model provider misconfigured");
    expect(container.querySelector("#failedHaltedAt")!.textContent).toBe("halted at stage 1");
    expect(container.querySelector('#spineFailed li[data-stage="planner"]')!.getAttribute("data-state")).toBe("active");
  });
  it("K19: omits the source fact row rather than inventing 'graph' when the record carries none", () => {
    const status = failed({ error_type: "graph_invalid_route", source: "", message: "The graph reached an unregistered route.", recoverable: false, timestamp: "", details: {} });
    const run = newRunState();
    const { container } = render(<FailedStage status={status} run={run} strip={null} />);
    expect(container.querySelector("#failFactSource")).toBeNull();
    expect([...container.querySelectorAll("dd")].some((dd) => dd.textContent === "graph")).toBe(false);
  });

  it("fix round 1 #1: prefers the halting error over an earlier non-recoverable agent error the run survived", () => {
    // graph/state.py:137-150 — agents record non-recoverable provider failures a pass is expected
    // to survive; the halt that actually ended the run may arrive later in `errors`. The headline
    // and the facts must come from the halt (a HALT_HEADLINES key), never the earlier survived one.
    const survived: ResearchError = { error_type: "researcher_extraction_provider_error", source: "agent.researcher", message: "The extraction call could not reach the provider.", recoverable: false, timestamp: "", details: {} };
    const halt: ResearchError = { error_type: "graph_request_attempt_limit_exceeded", source: "graph.report_writer", message: "The request attempt limit was reached, so the research run stopped.", recoverable: false, timestamp: "", details: {} };
    const status = failed(survived, halt);
    const run = newRunState();
    const { container } = render(<FailedStage status={status} run={run} strip={null} />);
    expect(container.querySelector("#failedType")!.textContent).toBe("Request attempt limit reached");
    expect(container.querySelector("#failedMessage")!.textContent).toBe("The request attempt limit was reached, so the research run stopped.");
    expect(container.querySelector("#failFactType")!.textContent).toBe("graph_request_attempt_limit_exceeded");
    expect(container.querySelector("#failFactSource")!.textContent).toBe("graph.report_writer");
  });

  it("fix round 1 #2: falls back to 'Research run failed' when no record's error_type is a recognised halt, keeping the raw type in the facts", () => {
    const status = failed({ error_type: "some_unmapped_error_type", source: "agent.report_writer", message: "", recoverable: false, timestamp: "", details: {} });
    const run = newRunState();
    const { container } = render(<FailedStage status={status} run={run} strip={null} />);
    expect(container.querySelector("#failedType")!.textContent).toBe("Research run failed");
    expect(container.querySelector("#failFactType")!.textContent).toBe("some_unmapped_error_type");
  });

  it("fix round 1 #3: with no error record at all, shows only the true report facts — no invented error_type, source or recoverable", () => {
    const status = failed();
    const run = newRunState();
    const { container } = render(<FailedStage status={status} run={run} strip={null} />);
    expect(container.querySelector("#failedType")!.textContent).toBe("Research run failed");
    expect(container.querySelector("#failedMessage")!.textContent).toBe("The run stopped on a non-recoverable error.");
    expect(container.querySelector("#failFactType")).toBeNull();
    expect(container.querySelector("#failFactSource")).toBeNull();
    expect(container.querySelector("#failFactRecoverable")).toBeNull();
    const dl = container.querySelector("dl.kv")!;
    expect(dl.textContent).toContain("report_path");
    expect(dl.textContent).toContain("Not published");
    expect(dl.textContent).toContain("GET /report");
    expect(dl.textContent).toContain("409 report_unavailable");
  });

  it("M1: an API-level failure (source \"api\") reports the real 409 session_not_complete code, never the graph-halt report_unavailable", () => {
    const status = failed({ error_type: "api.research.failed", source: "api", message: "Research run failed unexpectedly.", recoverable: false, timestamp: "", details: {} });
    const run = newRunState();
    const { container } = render(<FailedStage status={status} run={run} strip={null} />);
    expect(container.querySelector("#failFactReportCode")!.textContent).toBe("409 session_not_complete");
    expect(container.querySelector("p.avail")!.textContent).toContain("409 session_not_complete");
    expect(container.querySelector("p.avail")!.textContent).not.toContain("report_unavailable");
  });
  it("a graph halt still reports the real 409 report_unavailable code", () => {
    const status = failed({ error_type: "graph_provider_configuration_error", source: "graph", message: "m", recoverable: false, timestamp: "", details: {} });
    const run = newRunState();
    const { container } = render(<FailedStage status={status} run={run} strip={null} />);
    expect(container.querySelector("#failFactReportCode")!.textContent).toBe("409 report_unavailable");
  });
});
