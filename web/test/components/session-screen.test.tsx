import { act, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AppShell } from "../../components/AppShell";
import { ConsoleProvider } from "../../components/ConsoleProvider";
import { SessionScreen } from "../../components/SessionScreen";
import { Topbar } from "../../components/Topbar";
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
const RUNNING: ResearchSessionResponse = { ...STOPPED, finished_at: null };
const COMPLETED: ResearchSessionResponse = {
  ...STOPPED, status: "completed", report_path: "api-output/report.md", finished_at: "2026-09-27T00:05:00+00:00",
};
const unreachableBody = { error: { code: "api_unreachable", message: "Research service not reachable.", reason: null, issues: [], target: "http://127.0.0.1:8010" } };

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

describe("SessionScreen — service stopped (K7)", () => {
  it("streams the recorded events once, freezes the pipeline with no chip, and never reconnects even past the backoff ladder", async () => {
    // Installed before render so the component's own sleep() timer is fake from the moment it is
    // created; shouldAdvanceTime keeps it ticking with real time too, so RTL's waitFor below still
    // works, while advanceTimersByTimeAsync can still jump it forward explicitly at the end.
    vi.useFakeTimers({ shouldAdvanceTime: true });
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
    render(<ConsoleProvider><Topbar /><SessionScreen sessionId="s1" /></ConsoleProvider>);
    await waitFor(() => expect(document.getElementById("stage-stopped")).toBeTruthy());
    expect(document.querySelector("#stage-stopped .note.bad")!.textContent).toContain("The service stopped while this run was in progress. Nothing was published.");
    await waitFor(() => expect(document.querySelector('li[data-stage="planner"]')?.getAttribute("data-state")).toBe("done"));
    // M6: the halting row is the node that actually started (openNode) — the next row in the
    // pipeline never started and must stay pending, never read as falsely "active".
    expect(document.querySelector('li[data-stage="researcher"]')?.getAttribute("data-state")).toBe("pending");
    // M3: a stopped session shows no topbar chip — the sentence above the frozen pipeline already
    // says what happened, and a green "Running" chip would contradict it.
    expect(document.querySelector("#topbarStatus .chip")).toBeNull();
    // M5: 50 ms proves nothing — the first backoff delay is 1000 ms. Fake-forward well past the
    // ladder's steady 30 s cadence: if the K7 "stop reconnecting" guard were ever removed, the
    // effect would sleep and reconnect, and streamCalls would become 2 well within this window.
    await act(async () => { await vi.advanceTimersByTimeAsync(31_000); });
    expect(streamCalls).toBe(1);
  });
});

describe("SessionScreen — C1: the ladder retries getStatus before any status has ever loaded", () => {
  it("recovers on its own, with no click, once the service answers again", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let statusCalls = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/status")) {
        statusCalls++;
        return statusCalls < 3 ? json(502, unreachableBody) : json(200, RUNNING);
      }
      if (url.includes("/stream")) return sse("");
      return json(200, { sessions: [] });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ConsoleProvider><AppShell><SessionScreen sessionId="s1" /></AppShell></ConsoleProvider>);
    await waitFor(() => expect(document.getElementById("stage-loading")).toBeTruthy());
    // The banner follows the first 502 — waited for, not assumed to have landed in the same tick.
    await waitFor(() => expect(document.querySelector('[role="alert"]')).toBeTruthy());
    // No click anywhere in this test: the ladder itself must keep retrying (1 s, then 2 s, …)
    // until the service answers.
    await act(async () => { await vi.advanceTimersByTimeAsync(3_500); });
    await waitFor(() => expect(statusCalls).toBeGreaterThanOrEqual(3));
    await waitFor(() => expect(document.getElementById("stage-loading")).toBeNull());
    expect(document.querySelector('[role="alert"]')).toBeNull();
  });
});

describe("SessionScreen — C2: no false Planning state during an outage", () => {
  it("a failed reconnect leaves the last-rendered stage and active row untouched under the banner", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let statusCalls = 0;
    let streamCalls = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/stream")) {
        streamCalls++;
        if (streamCalls === 1) {
          return sse(
            frame(1, "graph.session.started", { max_extra_passes: 1 })
            + frame(2, "graph.node.started", { node: "planner", iteration: 0 })
            + frame(3, "graph.node.completed", { node: "planner" }),
          );
        }
        throw new Error("network down"); // every reconnect attempt fails outright
      }
      if (url.includes("/status")) {
        statusCalls++;
        return statusCalls === 1 ? json(200, RUNNING) : json(502, unreachableBody);
      }
      return json(200, { sessions: [] });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ConsoleProvider><AppShell><SessionScreen sessionId="s1" /></AppShell></ConsoleProvider>);
    await waitFor(() => expect(document.querySelector('#spine li[data-stage="planner"]')?.getAttribute("data-state")).toBe("done"));
    expect(document.querySelector('#spine li[data-stage="researcher"]')?.getAttribute("data-state")).toBe("active");
    // live-briefs spec §4.3: the active row is open on its brief; the done row is closed on its outcome.
    expect(document.querySelector('#spine li[data-stage="researcher"]')?.getAttribute("data-open")).toBe("1");
    expect(document.querySelector('#spine li[data-stage="planner"]')?.getAttribute("data-open")).toBe("0");
    // Drive the loop through the outage: the stream ends, the follow-up /status answers 502
    // (raising the banner), the ladder sleeps, and the reconnect attempt itself also fails. Before
    // C2, the loop reset `run.current` to a fresh RunState before *every* attempt — including this
    // doomed one — which painted a false "Planning" pipeline under the banner.
    await act(async () => { await vi.advanceTimersByTimeAsync(2_500); });
    await waitFor(() => expect(document.querySelector('[role="alert"]')).toBeTruthy());
    expect(document.querySelector('#spine li[data-stage="planner"]')?.getAttribute("data-state")).toBe("done");
    expect(document.querySelector('#spine li[data-stage="researcher"]')?.getAttribute("data-state")).toBe("active");
    expect(document.querySelector('#spine li[data-stage="researcher"]')?.getAttribute("data-open")).toBe("1");
  });
});

describe("SessionScreen — live-briefs final review M2: a reconnect keeps the rows the reader reopened", () => {
  it("replays from event 0 into a fresh run state without folding a done row the reader reopened", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let streamCalls = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/stream")) {
        streamCalls++;
        const head = frame(1, "graph.session.started", { max_extra_passes: 1 })
          + frame(2, "graph.node.started", { node: "planner", iteration: 0 })
          + frame(3, "graph.node.completed", { node: "planner" });
        // The reconnect replays from event 0 and then carries on past where the first stream ended,
        // so a settled Researching row proves the whole replay reached the page.
        return sse(streamCalls === 1 ? head : head + frame(4, "graph.node.started", { node: "researcher", iteration: 0 }) + frame(5, "graph.node.completed", { node: "researcher" }));
      }
      if (url.includes("/status")) return json(200, RUNNING);
      return json(200, { sessions: [] });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ConsoleProvider><AppShell><SessionScreen sessionId="s1" /></AppShell></ConsoleProvider>);
    const row = (id: string) => document.querySelector(`#spine li[data-stage="${id}"]`);
    await waitFor(() => expect(row("planner")?.getAttribute("data-state")).toBe("done"));
    expect(row("planner")?.getAttribute("data-open")).toBe("0");
    // The reader reopens Planning to read its titles.
    fireEvent.click(row("planner")!.querySelector("button.ps-toggle")!);
    expect(row("planner")?.getAttribute("data-open")).toBe("1");
    // The stream ends, /status still says running, the ladder sleeps 1 s and reconnects.
    await act(async () => { await vi.advanceTimersByTimeAsync(1_500); });
    await waitFor(() => expect(streamCalls).toBeGreaterThanOrEqual(2));
    await waitFor(() => expect(row("researcher")?.getAttribute("data-state")).toBe("done"));
    expect(row("planner")?.getAttribute("data-state")).toBe("done");
    expect(row("planner")?.getAttribute("data-open")).toBe("1");
    // Only what the reader opened carries over: Researching stays folded on its outcome.
    expect(row("researcher")?.getAttribute("data-open")).toBe("0");
  });
  it("does not carry a row the reader had closed again", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let streamCalls = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/stream")) {
        streamCalls++;
        const head = frame(1, "graph.session.started", { max_extra_passes: 1 })
          + frame(2, "graph.node.started", { node: "planner", iteration: 0 })
          + frame(3, "graph.node.completed", { node: "planner" });
        return sse(streamCalls === 1 ? head : head + frame(4, "graph.node.started", { node: "researcher", iteration: 0 }) + frame(5, "graph.node.completed", { node: "researcher" }));
      }
      if (url.includes("/status")) return json(200, RUNNING);
      return json(200, { sessions: [] });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ConsoleProvider><AppShell><SessionScreen sessionId="s1" /></AppShell></ConsoleProvider>);
    const row = (id: string) => document.querySelector(`#spine li[data-stage="${id}"]`);
    await waitFor(() => expect(row("planner")?.getAttribute("data-state")).toBe("done"));
    const toggle = () => fireEvent.click(row("planner")!.querySelector("button.ps-toggle")!);
    toggle(); toggle(); // opened, then closed again
    expect(row("planner")?.getAttribute("data-open")).toBe("0");
    await act(async () => { await vi.advanceTimersByTimeAsync(1_500); });
    await waitFor(() => expect(row("researcher")?.getAttribute("data-state")).toBe("done"));
    expect(row("planner")?.getAttribute("data-open")).toBe("0");
  });
});

describe("SessionScreen — final-wave item 2: refreshSessions on reaching a terminal status", () => {
  it("refreshes the sidebar's session list once this tab's run leaves \"running\", not only via the sidebar's own poll", async () => {
    let listCalls = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/stream")) return sse(""); // ends at once, no events
      if (url.includes("/status")) return json(200, COMPLETED); // terminal from the very first load
      // A terminal status renders ReportStage, which fetches its own report and evidence log —
      // neither is the sidebar's own list read, so only "?limit=" (listSessions' own query
      // string) may count toward listCalls.
      if (url.includes("?limit=")) { listCalls++; return json(200, { sessions: [] }); }
      if (url.includes("/report")) return new Response("# R\n", { status: 200, headers: { "content-type": "text/markdown" } });
      return json(200, { session_id: "s1", iteration: 0, findings: [], not_found: [], refused: [] });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ConsoleProvider><SessionScreen sessionId="s1" /></ConsoleProvider>);
    await waitFor(() => expect(document.getElementById("stage-report")).toBeTruthy());
    // ConsoleProvider's own mount already contributes one list call; SessionScreen's own
    // terminal-status effect must add at least one more beyond that baseline, immediately rather
    // than waiting for the sidebar's separate 5 s poll (which only runs while a session is
    // "running" in the first place).
    await waitFor(() => expect(listCalls).toBeGreaterThanOrEqual(2));
  });
});

describe("SessionScreen — re-review item 4: no false Running chip on the not-in-memory page", () => {
  it("blanks the topbar chip once a stale running status resolves to not-in-memory", async () => {
    let statusCalls = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/stream")) return sse(""); // ends at once, no events
      if (url.includes("/status")) {
        statusCalls++;
        // A mid-run API restart: the first /status still shows the old "running" record; the
        // ladder's next /status (after the stream's own clean close) lands a 404 — the session
        // is gone from the restarted process's memory.
        return statusCalls === 1
          ? json(200, RUNNING)
          : json(404, { error: { code: "session_not_found", message: "Unknown session.", reason: null, issues: [] } });
      }
      return json(200, { sessions: [] });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ConsoleProvider><AppShell><SessionScreen sessionId="s1" /></AppShell></ConsoleProvider>);
    await waitFor(() => expect(document.querySelector("#topbarStatus .chip")).toBeTruthy());
    await waitFor(() => expect(document.getElementById("stage-not-found")).toBeTruthy());
    // Before this fix: `stopped` is false (finished_at is null on the stale RUNNING record), so
    // the chip kept reading the old status — "Running · Planning" — over a page that itself
    // says the session isn't in memory.
    expect(document.querySelector("#topbarStatus .chip")).toBeNull();
  });
});
