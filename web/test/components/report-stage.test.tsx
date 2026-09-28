import { act, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ResearchSessionResponse } from "../../lib/api";

// K14: ReportStage must not swallow ApiUnreachableError into an endless "loading report" — it has
// to hand the failure to the console's own banner (spec §4.4 "API unreachable"), with a Retry that
// repeats the same read. useConsole() is mocked so this test proves ReportStage's own wiring
// without depending on ConsoleProvider's unrelated session-list poll (a real race: both mount at
// once and either could resolve first).
const mockConsole = vi.hoisted(() => ({ noteUnreachable: vi.fn(), clearUnreachable: vi.fn() }));
vi.mock("../../components/ConsoleProvider", () => ({ useConsole: () => mockConsole }));

import { ReportStage } from "../../components/ReportStage";

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const md = (text: string) => new Response(text, { status: 200, headers: { "content-type": "text/markdown" } });

const STATUS: ResearchSessionResponse = {
  session_id: "s1", query: "Q", status: "completed", current_agent: null, iteration: 1,
  started_at: "2026-09-27T00:00:00+00:00", finished_at: "2026-09-27T00:05:00+00:00",
  report_path: "api-output/report.md", trace_url: null, errors: [],
  evidence_path: "api-output/report-evidence.md", quality_path: null, quality_contract_version: null,
  semantic_review_status: "scored", semantic_review_score: 0.9, duration_seconds: 12,
  coverage: null, evidence_counts: null,
};

afterEach(() => {
  vi.unstubAllGlobals();
  mockConsole.noteUnreachable.mockClear();
  mockConsole.clearUnreachable.mockClear();
});

describe("ReportStage — K14: ApiUnreachableError on the report fetch raises the console banner", () => {
  it("calls noteUnreachable under its own \"report\" key with the failed request's target; its own retry re-fetches and then clears that key", async () => {
    let reportCalls = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/report")) {
        reportCalls++;
        if (reportCalls === 1) return json(502, { error: { code: "api_unreachable", message: "Research service not reachable.", reason: null, issues: [], target: "http://127.0.0.1:8010" } });
        return md("# Q\n\nBody text.\n");
      }
      return json(409, { error: { code: "evidence_unavailable", message: "no evidence", reason: null, issues: [] } });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ReportStage sessionId="s1" status={STATUS} strip={null} passes={2} />);
    await waitFor(() => expect(mockConsole.noteUnreachable).toHaveBeenCalledWith("report", "http://127.0.0.1:8010", expect.any(Function)));
    expect(mockConsole.clearUnreachable).not.toHaveBeenCalledWith("report");
    expect(document.querySelector("article.card p.avail")!.textContent).toBe("loading report"); // not stuck silently — the banner also fired above
    const retry = mockConsole.noteUnreachable.mock.calls[0][2] as () => void;
    await act(async () => { retry(); });
    await waitFor(() => expect(document.querySelector(".prose")).toBeTruthy());
    expect(mockConsole.clearUnreachable).toHaveBeenCalledWith("report");
  });

  it("C1: registers report and evidence under independent keys, so each can be retried on its own without touching the other", async () => {
    let reportCalls = 0;
    let evidenceCalls = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const unreachable = json(502, { error: { code: "api_unreachable", message: "Research service not reachable.", reason: null, issues: [], target: "http://127.0.0.1:8010" } });
      if (url.includes("/report")) {
        reportCalls++;
        return reportCalls === 1 ? unreachable : md("# Q\n\nBody text.\n");
      }
      evidenceCalls++;
      return evidenceCalls === 1 ? unreachable : json(200, { session_id: "s1", iteration: 0, findings: [], not_found: [], refused: [] });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ReportStage sessionId="s1" status={STATUS} strip={null} passes={2} />);
    await waitFor(() => expect(reportCalls).toBe(1));
    await waitFor(() => expect(evidenceCalls).toBe(1));
    await waitFor(() => expect(mockConsole.noteUnreachable).toHaveBeenCalledWith("report", "http://127.0.0.1:8010", expect.any(Function)));
    await waitFor(() => expect(mockConsole.noteUnreachable).toHaveBeenCalledWith("evidence", "http://127.0.0.1:8010", expect.any(Function)));
    expect(mockConsole.clearUnreachable).not.toHaveBeenCalled();
    // C1: the provider (not ReportStage) now runs "every registered retry" for a single Retry
    // click — this proves ReportStage's own half of the contract: each key's retry repeats only
    // that read, so calling the report retry alone must never touch the evidence read.
    const reportRetry = mockConsole.noteUnreachable.mock.calls.find((c) => c[0] === "report")!.at(-1) as () => void;
    await act(async () => { reportRetry(); });
    await waitFor(() => expect(reportCalls).toBe(2));
    expect(evidenceCalls).toBe(1);
    expect(mockConsole.clearUnreachable).toHaveBeenCalledWith("report");
    expect(mockConsole.clearUnreachable).not.toHaveBeenCalledWith("evidence");
    const evidenceRetry = mockConsole.noteUnreachable.mock.calls.find((c) => c[0] === "evidence")!.at(-1) as () => void;
    await act(async () => { evidenceRetry(); });
    await waitFor(() => expect(evidenceCalls).toBe(2));
    await waitFor(() => expect(document.querySelector(".prose")).toBeTruthy());
    expect(mockConsole.clearUnreachable).toHaveBeenCalledWith("evidence");
  });

  it("minor 4: any other ApiError (neither 409 nor unreachable) also shows Not published, never an endless loading state", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/report")) return json(500, { error: { code: "internal_error", message: "boom", reason: null, issues: [] } });
      return json(404, { error: { code: "session_not_found", message: "not found", reason: null, issues: [] } });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ReportStage sessionId="s1" status={STATUS} strip={null} passes={2} />);
    await waitFor(() => expect(document.querySelector("article.card p.avail")!.textContent).toBe("Not published"));
    expect(mockConsole.noteUnreachable).not.toHaveBeenCalled();
  });

  it("a 409 on the report fetch shows the muted 'Not published' line, never an endless loading state", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/report")) return json(409, { error: { code: "session_not_complete", message: "not complete", reason: null, issues: [] } });
      return json(409, { error: { code: "evidence_unavailable", message: "no evidence", reason: null, issues: [] } });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ReportStage sessionId="s1" status={STATUS} strip={null} passes={2} />);
    await waitFor(() => expect(document.querySelector("article.card p.avail")!.textContent).toBe("Not published"));
    expect(mockConsole.noteUnreachable).not.toHaveBeenCalled();
  });

  it("NB1: unmounting while report and evidence are both stuck unreachable clears both keys", async () => {
    const unreachable502 = () => json(502, { error: { code: "api_unreachable", message: "Research service not reachable.", reason: null, issues: [], target: "http://127.0.0.1:8010" } });
    vi.stubGlobal("fetch", vi.fn(async () => unreachable502()));
    const { unmount } = render(<ReportStage sessionId="s1" status={STATUS} strip={null} passes={2} />);
    await waitFor(() => expect(mockConsole.noteUnreachable).toHaveBeenCalledWith("report", "http://127.0.0.1:8010", expect.any(Function)));
    await waitFor(() => expect(mockConsole.noteUnreachable).toHaveBeenCalledWith("evidence", "http://127.0.0.1:8010", expect.any(Function)));
    mockConsole.clearUnreachable.mockClear();
    // Nothing is left to run either key's retry once ReportStage is gone — without an unmount
    // clear, both keys would stay registered forever and the banner would outlive the page.
    unmount();
    expect(mockConsole.clearUnreachable).toHaveBeenCalledWith("report");
    expect(mockConsole.clearUnreachable).toHaveBeenCalledWith("evidence");
  });
});

describe("ReportStage — q-center (controller ruling 1)", () => {
  it("centres #report-h for a question at or under 80 characters, following the report-q + qFitClass pattern", async () => {
    const fetchMock = vi.fn(async () => json(409, { error: { code: "session_not_complete", message: "not complete", reason: null, issues: [] } }));
    vi.stubGlobal("fetch", fetchMock);
    render(<ReportStage sessionId="s1" status={{ ...STATUS, query: "Short question" }} strip={null} passes={2} />);
    const h1 = document.getElementById("report-h")!;
    expect(h1.className).toBe("report-q q-center");
    expect(h1.className).not.toContain("ask-q");
  });
  it("does not centre #report-h past 80 characters", async () => {
    const fetchMock = vi.fn(async () => json(409, { error: { code: "session_not_complete", message: "not complete", reason: null, issues: [] } }));
    vi.stubGlobal("fetch", fetchMock);
    const long = "A".repeat(81);
    render(<ReportStage sessionId="s1" status={{ ...STATUS, query: long }} strip={null} passes={2} />);
    expect(document.getElementById("report-h")!.className).toBe("report-q");
  });
});
