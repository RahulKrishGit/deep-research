// @vitest-environment node
// notes-progress-report spec §8.4-§8.5: the stopped session's words — the step's label, the time,
// how far in. Times are built in local time, so the clock reads the same in any time zone.
import { describe, expect, it } from "vitest";
import { localClock, minutesIn, secondsBetween, stoppedLine, stoppedStepLabel } from "../lib/stop";

describe("the stopped session's words (notes-progress-report spec §8.4-§8.5)", () => {
  it("labels the step a stop records: a row's label, or the questions for the one-time check", () => {
    expect(["check", "planner", "researcher", "report_reviewer", "finalize_report", null, "nonsense"].map((step) => stoppedStepLabel(step)))
      .toEqual(["the questions", "Planning", "Researching", "Reviewing", "Publishing", null, null]);
  });
  it("counts whole minutes in: less than a minute, 1 minute, then n minutes", () => {
    expect([0, 59, 60, 119, 120, 391].map(minutesIn)).toEqual([
      "less than a minute in", "less than a minute in", "1 minute in", "1 minute in", "2 minutes in", "6 minutes in",
    ]);
  });
  it("reads the time in the reader's own zone, 24-hour, and nothing for a missing or unreadable one", () => {
    expect(localClock(new Date(2026, 8, 30, 20, 41, 7).toISOString())).toBe("20:41");
    expect(localClock(new Date(2026, 8, 30, 7, 5, 0).toISOString())).toBe("07:05");
    expect([localClock(null), localClock("not a time")]).toEqual([null, null]);
  });
  it("says when the reader stopped and how far in — or that the run had not started", () => {
    const at = new Date(2026, 8, 30, 20, 41, 7).toISOString();
    expect(stoppedLine("researcher", at, 391)).toBe("You stopped this research at 20:41, 6 minutes in.");
    expect(stoppedLine("planner", at, 12)).toBe("You stopped this research at 20:41, less than a minute in.");
    expect(stoppedLine("check", at, 30)).toBe("You stopped this research at 20:41, before it started.");
    expect(stoppedLine("researcher", null, null)).toBe("You stopped this research.");
  });
  it("measures a run's whole seconds, and nothing for a missing or reversed pair", () => {
    expect(secondsBetween("2026-09-30T20:34:36+00:00", "2026-09-30T20:41:07+00:00")).toBe(391);
    expect([secondsBetween(null, "2026-09-30T20:41:07+00:00"), secondsBetween("2026-09-30T20:41:07+00:00", "2026-09-30T20:34:36+00:00")]).toEqual([null, null]);
  });
});
