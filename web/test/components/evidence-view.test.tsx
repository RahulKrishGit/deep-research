import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { EvidenceView, evidenceRows } from "../../components/EvidenceView";
import fixture from "../fixtures/evidence-default.json";
import type { EvidenceResponse } from "../../lib/api";

const evidence = fixture as EvidenceResponse;

describe("EvidenceView", () => {
  it("lists every row under All with counted filter chips", () => {
    expect(evidenceRows(evidence).map((r) => r.status)).toEqual(["verified", "verified_corrected", "quoted", "dropped", "not_checked", "verified", "not_found", "refused"]);
    const { container } = render(<EvidenceView evidence={evidence} />);
    expect(container.querySelectorAll(".ev-row").length).toBe(8);
    const chip = (id: string) => container.querySelector(`.chip-filter[data-filter="${id}"] .n`)!.textContent;
    expect([chip("all"), chip("verified"), chip("verified_corrected"), chip("quoted"), chip("dropped"), chip("not_found"), chip("refused")]).toEqual(["8", "2", "1", "1", "1", "1", "1"]);
    expect(container.querySelector('.ev-row[aria-selected="true"] .lbl')!.textContent).toBe("F01");
    expect(container.querySelector(".ev-title")!.textContent).toBe("F01 — Resolving the Interconnection Queue Bottleneck");
  });
  it("shows a not-checked finding only under All", () => {
    const { container } = render(<EvidenceView evidence={evidence} />);
    expect(container.querySelector('.ev-row[data-status="not_checked"]')).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /^Verified/ }));
    expect(container.querySelectorAll(".ev-row").length).toBe(2);
    expect(container.querySelector('.ev-row[data-status="not_checked"]')).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /^Dropped/ }));
    expect(container.querySelector(".ev-status")!.textContent).toBe("dropped (snippet_not_on_page)");
  });
  it("renders a not-found row and a refused row with their details", () => {
    const { container } = render(<EvidenceView evidence={evidence} />);
    fireEvent.click(screen.getByRole("button", { name: /^Not found/ }));
    expect(container.querySelector(".ev-row .txt")!.textContent).toBe("How do non-U.S. grid connection regimes constrain battery deployment?");
    expect(container.querySelector(".ev-row .tag.soft")!.textContent).toBe("2 queries · 2 pages read");
    fireEvent.click(screen.getByRole("button", { name: /^Refused/ }));
    expect(container.querySelector(".ev-row .tag.soft")!.textContent).toBe("cited F03");
    expect(container.querySelector(".ev-title")!.textContent).toBe("R01 — refused sentence");
  });
});

describe("evidenceRows ordering", () => {
  it("sorts findings F-labels numerically first, then X-labels numerically, ahead of not-found and refused rows", () => {
    const data: EvidenceResponse = {
      session_id: "s", iteration: 0,
      findings: [
        { label: "F02", status: "verified", dropped_reason: null, context_unchecked: false, cited: true, target_ids: [], content: "", snippet: null, passage: null,
          source: { url: "", title: "", organisation: "", evaluation_status: null, low_confidence: false, authority_score: null, recency_score: null, relevance_score: null, overall_score: null }, figures: [] },
        { label: "F01", status: "verified", dropped_reason: null, context_unchecked: false, cited: true, target_ids: [], content: "", snippet: null, passage: null,
          source: { url: "", title: "", organisation: "", evaluation_status: null, low_confidence: false, authority_score: null, recency_score: null, relevance_score: null, overall_score: null }, figures: [] },
        { label: "X01", status: "dropped", dropped_reason: "snippet_not_on_page", context_unchecked: false, cited: false, target_ids: [], content: "", snippet: null, passage: null,
          source: { url: "", title: "", organisation: "", evaluation_status: null, low_confidence: false, authority_score: null, recency_score: null, relevance_score: null, overall_score: null }, figures: [] },
      ],
      not_found: [{ target_id: "topic-01-target-01", question: "q", queries: [], pages_read: [], searched: true }],
      refused: [{ where: "w", text: "t", reason: "r", finding_labels: [] }],
    };
    expect(evidenceRows(data).map((r) => r.id)).toEqual(["F01", "F02", "X01", "topic-01-target-01", "R01"]);
  });
});
