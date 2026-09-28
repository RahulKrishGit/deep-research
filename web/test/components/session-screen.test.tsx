import { render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ConsoleProvider } from "../../components/ConsoleProvider";
import { SessionScreen } from "../../components/SessionScreen";
import type { ResearchSessionResponse } from "../../lib/api";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }), useParams: () => ({}) }));

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const sse = (frames: string) => new Response(frames, { status: 200, headers: { "content-type": "text/event-stream", "x-deep-research-mode": "replay" } });
const frame = (n: number, type: string, metadata: Record<string, unknown> = {}) =>
  `id: ${n}\ndata: ${JSON.stringify({ event_type: type, source: "graph", message: "m", timestamp: "2026-09-27T00:00:00+00:00", metadata })}\n\n`;

const STOPPED: ResearchSessionResponse = {
  session_id: "s1", query: "The question", status: "running", current_agent: null, iteration: 0,
  started_at: "2026-09-27T00:00:00+00:00", finished_at: "2026-09-27T00:05:00+00:00",
  report_path: null, trace_url: null, errors: [], evidence_path: null, quality_path: null, quality_contract_version: null,
  semantic_review_status: null, semantic_review_score: null, duration_seconds: null, coverage: null, evidence_counts: null,
};

afterEach(() => vi.unstubAllGlobals());

describe("SessionScreen — service stopped (K7)", () => {
  it("streams the recorded events once, freezes the pipeline with the stopped sentence, and never reconnects", async () => {
    let streamCalls = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/stream")) {
        streamCalls++;
        return sse(frame(1, "graph.node.started", { node: "planner", iteration: 0 }) + frame(2, "graph.node.completed", { node: "planner" }));
      }
      if (url.includes("/status")) return json(200, STOPPED);
      return json(200, { sessions: [] });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ConsoleProvider><SessionScreen sessionId="s1" /></ConsoleProvider>);
    await waitFor(() => expect(document.getElementById("stage-stopped")).toBeTruthy());
    expect(document.querySelector("#stage-stopped .note.bad")!.textContent).toContain("The service stopped while this run was in progress. Nothing was published.");
    await waitFor(() => expect(document.querySelector('li[data-stage="planner"]')?.getAttribute("data-state")).toBe("done"));
    const { promise, resolve } = Promise.withResolvers<void>();
    setTimeout(resolve, 50);
    await promise;
    expect(streamCalls).toBe(1);
  });
});
