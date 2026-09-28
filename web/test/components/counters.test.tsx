import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Counters } from "../../components/Counters";
import { emptyCounters } from "../../lib/run-state";

const dd = (c: HTMLElement, key: string) => c.querySelector(`dd[data-counter="${key}"]`)!;

describe("Counters", () => {
  it("reads muted text for a counter whose event has not arrived, never 0", () => {
    const { container } = render(<Counters counters={emptyCounters()} absentText="not yet" pass={1} />);
    expect(container.querySelectorAll("dd").length).toBe(7);
    for (const dd_ of container.querySelectorAll("dd")) { expect(dd_.textContent).toBe("not yet"); expect(dd_.className).toContain("avail"); }
    expect(container.textContent).not.toMatch(/\b0\b/);
    expect(container.querySelector("#runCountersPass")!.textContent).toBe("pass 1");
  });
  it("prints the rows the prototype prints", () => {
    const counters = { ...emptyCounters(), subTopicsResearched: 3, subTopicsTotal: 4, toolCalls: 12, findings: 18, sources: 14, verified: 9, corrected: 2, dropped: 1, statements: 11, refused: 1, reviewSeen: true, reviewScore: null };
    const { container } = render(<Counters counters={counters} absentText="not reached" pass={2} />);
    expect(dd(container, "subTopics").textContent).toBe("3 of 4");
    expect(dd(container, "verified").textContent).toBe("9 / 2 / 1");
    expect(dd(container, "statements").textContent).toBe("11 / 1");
    expect(dd(container, "review").textContent).toBe("not scored");
    expect(dd(container, "review").className).toContain("avail");
  });
});
