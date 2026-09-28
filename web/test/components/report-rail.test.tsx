import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ReportRail } from "../../components/ReportRail";
import type { ResearchSessionResponse } from "../../lib/api";

const base: ResearchSessionResponse = {
  session_id: "s", query: "q", status: "incomplete", current_agent: null, iteration: 0, started_at: "2026-09-16T14:02:11Z",
  finished_at: "2026-09-16T14:12:00Z", report_path: null, trace_url: null, errors: [], evidence_path: null, quality_path: null,
  quality_contract_version: null, semantic_review_status: "provider_failed", semantic_review_score: null, duration_seconds: null,
  coverage: null, evidence_counts: null,
};

describe("ReportRail", () => {
  it("reads muted words for every absent value and never a zero", () => {
    const { container } = render(<ReportRail status={base} evidence={null} passes={2} />);
    const text = container.textContent!;
    expect(container.querySelector("#repReviewN")!.textContent).toBe("not scored");
    expect(container.querySelector("#repReviewStatus")!.textContent).toBe("review unavailable");
    expect(container.querySelector("#repCitedN")!.textContent).toBe("not measured");
    expect(container.querySelector("#repEvidenceNone")!.hasAttribute("hidden")).toBe(false);
    expect(container.querySelector("#repCovAnswered")!.textContent).toBe("not measured");
    expect(container.querySelector("#repFactPath")!.textContent).toBe("Not published");
    expect(container.querySelector("#repFactDuration")!.textContent).toBe("not recorded");
    expect(text).toContain("Not recorded");
    expect(text).not.toMatch(/NaN/);
    expect(container.querySelector("#repFactPass")!.textContent).toBe("pass 1 of 2");
  });
  it("paints a 0.80 meter yellow and lists not-found targets with their question once E1 loaded", () => {
    const status = { ...base, status: "max_iterations" as const, semantic_review_status: "scored", semantic_review_score: 0.8,
      coverage: { required_targets: 3, answered_targets: 2, missing_required_target_ids: [], not_found_target_ids: ["topic-01-target-01"] },
      evidence_counts: { read_records: 5, network_reads: 4, cache_reads: 1, unique_works: 4, publishers: 3, source_urls: 4, findings: 6, assessed_sources: 5, cited_assessed_sources: 4, verified_findings: 3, corrected_findings: 1, quoted_findings: 1, dropped_findings: 1, context_unchecked_findings: 1, cited_findings: 4 } };
    const evidence = { session_id: "s", iteration: 1, findings: [], refused: [], not_found: [{ target_id: "topic-01-target-01", question: "How much storage is planned for 2025?", queries: [], pages_read: [], searched: true }] };
    const { container } = render(<ReportRail status={status} evidence={evidence} passes={2} />);
    expect(container.querySelector("#repReviewFill")!.className).toContain("warn");
    expect(container.querySelector("#repCitedN")!.textContent).toBe("0.80");
    expect(container.querySelector("#repCovNotFound")!.textContent).toBe("topic-01-target-01 — How much storage is planned for 2025?");
    expect(container.querySelector("#repEvidenceCounts")!.textContent).toContain("4 network · 1 cache");
  });
});
