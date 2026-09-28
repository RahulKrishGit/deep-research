import { describe, expect, it } from "vitest";
import { groupByDay } from "../../components/Sidebar";
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
