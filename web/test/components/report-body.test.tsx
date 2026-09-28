import { fireEvent, render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ReportBody } from "../../components/ReportBody";

const MARKDOWN = `# What is the current state of grid-scale battery storage?

Evidence as of 2026-09-16 · 3 sources

## Bottom line

Storage grew fast in 2024 [1][2]. Costs fell [3].

| Option | Energy density | Cycle life | Recommended by |
|---|---|---|---|
| LFP | lower | long | [1] |
| NMC | higher | shorter | [2] |

*Options compared on the parts the plan named; an empty cell reads not stated.*

## Grid connection

- Queues stretch to five years [1].
- Reform is under way [2][3].

## What we couldn't confirm

Nothing on non-U.S. regimes.

## Sources

1. Novogradac — [Resolving the Interconnection Queue Bottleneck](https://www.novoco.com/) (2024)
2. IEA — [Global Critical Minerals Outlook 2025](https://www.iea.org/) (2025)
3. CRS — [Critical Minerals and Materials](https://crsreports.congress.gov/) (2025)

How this was researched: [evidence log](report-abc-0-evidence.md)
`;

describe("ReportBody renders the server's Markdown as-is with the design's adaptations", () => {
  it("does not repeat the H1, mutes the evidence line, anchors [n], frames the options table, ids the sources", () => {
    const onOpen = vi.fn();
    const { container } = render(<ReportBody markdown={MARKDOWN} evidenceLoaded onOpenEvidence={onOpen} />);
    expect(container.querySelector("h1")).toBeNull();
    expect(container.querySelector("p.avail")!.textContent).toBe("Evidence as of 2026-09-16 · 3 sources");
    expect([...container.querySelectorAll("h2")].map((h) => h.textContent)).toEqual(["Bottom line", "Grid connection", "What we couldn't confirm", "Sources"]);
    const anchors = [...container.querySelectorAll('a[href^="#src-"]')].map((a) => [a.getAttribute("href"), a.textContent]);
    expect(anchors.slice(0, 3)).toEqual([["#src-1", "[1]"], ["#src-2", "[2]"], ["#src-3", "[3]"]]);
    // K9: the run "[1][2]" lives in the Bottom line paragraph, not the evidence line (the first <p>).
    const bottomLine = [...container.querySelectorAll("h2")].find((h) => h.textContent === "Bottom line")!.nextElementSibling!;
    expect(bottomLine.textContent).toContain("[1][2]"); // a run stays a run
    expect(bottomLine.textContent).not.toContain("[1] [2]");
    const frame = container.querySelector(".tbl-frame")!;
    expect(frame.hasAttribute("data-pinned")).toBe(true);
    expect(frame.querySelector("table")!.className).toContain("tbl");
    expect(frame.nextElementSibling!.className).toContain("tbl-foot");
    expect([...container.querySelectorAll("ol li")].map((li) => li.id)).toEqual(["src-1", "src-2", "src-3"]);
    for (const a of container.querySelectorAll("ol li a")) { expect(a.getAttribute("target")).toBe("_blank"); expect(a.getAttribute("rel")).toBe("noopener"); }
    const link = container.querySelector('p.avail button.link')!;
    expect(link.textContent).toBe("evidence log");
    fireEvent.click(link);
    expect(onOpen).toHaveBeenCalledTimes(1);
    expect(container.textContent).not.toContain("Executive Summary");
  });
  it("shows the evidence-log words as muted text while E1 has not loaded", () => {
    const { container } = render(<ReportBody markdown={MARKDOWN} evidenceLoaded={false} onOpenEvidence={() => {}} />);
    expect(container.querySelector("button.link")).toBeNull();
    expect([...container.querySelectorAll("p.avail")].at(-1)!.textContent).toBe("How this was researched: evidence log");
  });
});
