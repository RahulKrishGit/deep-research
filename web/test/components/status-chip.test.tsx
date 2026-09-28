import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { StatusChip } from "../../components/StatusChip";
import type { SessionView } from "../../lib/format";

const view = (over: Partial<SessionView>): SessionView => ({ status: "running", iteration: 0, passes: 2, review: null, coverage: null, ...over });
const text = (el: HTMLElement) => el.textContent!.replace(/\s+/g, " ").trim();

describe("StatusChip", () => {
  it("reads label · note with the dot class per status", () => {
    const { container, rerender } = render(<StatusChip view={view({})} />);
    expect(text(container)).toBe("Running · pass 1 of 2");
    expect(container.querySelector(".dot")!.className).toContain("dot-live");
    rerender(<StatusChip view={view({ status: "completed", iteration: 1, review: { status: "scored", score: 0.86 }, coverage: { required_targets: 4, answered_targets: 3, missing_required_target_ids: [], not_found_target_ids: ["t"] } })} />);
    expect(text(container)).toBe("Completed · review accepted · 0.86 · 1 target not found");
    rerender(<StatusChip view={view({ status: "incomplete", review: { status: "provider_failed", score: null } })} />);
    expect(text(container)).toBe("Partially completed · review unavailable");
    expect(container.querySelector(".dot")!.className).toContain("dot-warn");
    rerender(<StatusChip view={view({ status: "failed" })} />);
    expect(text(container)).toBe("Failed · halted");
  });
});
