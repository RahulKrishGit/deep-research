import { render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ConsoleProvider } from "../../components/ConsoleProvider";
import { Sidebar, groupByDay } from "../../components/Sidebar";
import type { ResearchSessionResponse } from "../../lib/api";

const session = (id: string, started_at: string): ResearchSessionResponse => ({
  session_id: id, query: `q ${id}`, status: "completed", current_agent: null, iteration: 0, started_at, finished_at: null,
  report_path: null, trace_url: null, errors: [], evidence_path: null, quality_path: null, quality_contract_version: null,
  semantic_review_status: null, semantic_review_score: null, duration_seconds: null, coverage: null, evidence_counts: null,
});

describe("groupByDay", () => {
  it("groups Today, Yesterday, Earlier by the local date of started_at, keeping order", () => {
    const now = new Date(2026, 8, 27, 15, 0, 0);
    const groups = groupByDay([session("a", new Date(2026, 8, 27, 9).toISOString()), session("b", new Date(2026, 8, 26, 23).toISOString()), session("c", new Date(2026, 8, 20).toISOString())], now);
    expect(groups.map(([g, items]) => [g, items.map((s) => s.session_id)])).toEqual([["Today", ["a"]], ["Yesterday", ["b"]], ["Earlier", ["c"]]]);
    expect(groupByDay([], now)).toEqual([]);
  });
});

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }), useParams: () => ({}) }));
const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
afterEach(() => vi.unstubAllGlobals());

describe("Sidebar session count", () => {
  it("shows no count before the session list has loaded, then 0 once loaded empty (never an invented 0)", async () => {
    let release!: () => void;
    const gate = new Promise<void>((r) => { release = r; });
    vi.stubGlobal("fetch", vi.fn(async () => { await gate; return json(200, { sessions: [] }); }));
    render(<ConsoleProvider><Sidebar /></ConsoleProvider>);
    expect(document.getElementById("sbCount")!.textContent).toBe("");
    release();
    await waitFor(() => expect(document.getElementById("sbCount")!.textContent).toBe("0"));
  });
});
