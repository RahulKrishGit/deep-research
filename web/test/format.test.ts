import { describe, expect, it } from "vitest";
import type { ResearchSessionResponse } from "../lib/api";
import { STATUS, fmtClock, fmtScore, fmtSeconds, isLive, meterClass, notFoundClause, passFact, qFitClass, statusNote, toSessionView } from "../lib/format";

const base: ResearchSessionResponse = {
  session_id: "s", query: "q", status: "running", current_agent: null, iteration: 0,
  started_at: "2026-09-16T14:02:11Z", finished_at: null, report_path: null, trace_url: null, errors: [],
  evidence_path: null, quality_path: null, quality_contract_version: null,
  semantic_review_status: null, semantic_review_score: null, duration_seconds: null, coverage: null, evidence_counts: null,
};
const coverage = (notFound: string[]) => ({ required_targets: 4, answered_targets: 4 - notFound.length, missing_required_target_ids: [], not_found_target_ids: notFound });

describe("statusNote — one rule per API status", () => {
  it("running names the active step and never a pass (live-briefs spec §4.2)", () => {
    expect(statusNote(toSessionView(base, "Researching"))).toBe("Researching");
    expect(statusNote(toSessionView(base))).toBe("starting");
    expect(statusNote(toSessionView({ ...base, iteration: 1 }, "Writing report"))).toBe("Writing report");
  });
  it("completed → review accepted · score + not-found clause", () => {
    const s = { ...base, status: "completed" as const, iteration: 1, semantic_review_status: "scored", semantic_review_score: 0.86, coverage: coverage(["topic-02-target-01"]) };
    expect(statusNote(toSessionView(s))).toBe("review accepted · 0.86 · 1 target not found");
  });
  it("max_iterations → extra passes used + clause", () => {
    const s = { ...base, status: "max_iterations" as const, coverage: coverage(["a"]) };
    expect(statusNote(toSessionView(s))).toBe("extra passes used · 1 target not found");
    expect(statusNote(toSessionView({ ...s, coverage: coverage(["a", "b", "c"]) }))).toBe("extra passes used · 3 targets not found");
    expect(statusNote(toSessionView({ ...s, coverage: coverage([]) }))).toBe("extra passes used");
  });
  it("incomplete with a scored review → not accepted · score", () => {
    const s = { ...base, status: "incomplete" as const, semantic_review_status: "scored", semantic_review_score: 0.71 };
    expect(statusNote(toSessionView(s))).toBe("not accepted · 0.71");
  });
  it("incomplete without a score → review unavailable", () => {
    const s = { ...base, status: "incomplete" as const, semantic_review_status: "provider_failed", semantic_review_score: null };
    expect(statusNote(toSessionView(s))).toBe("review unavailable");
  });
  it("failed → halted", () => {
    expect(statusNote(toSessionView({ ...base, status: "failed" }))).toBe("halted");
  });
  it("needs_input → Waiting for you · a few quick questions, on the warn dot (live-briefs spec §4.5)", () => {
    expect(statusNote(toSessionView({ ...base, status: "needs_input" }, "Planning"))).toBe("a few quick questions");
    expect(STATUS.needs_input).toEqual({ label: "Waiting for you", dot: "dot-warn" });
  });
});

describe("helpers", () => {
  it("isLive: a running session and one waiting for the reader are in progress; every other status is not", () => {
    expect((["running", "needs_input", "completed", "max_iterations", "incomplete", "failed"] as const).map(isLive)).toEqual([true, true, false, false, false, false]);
  });
  it("qFitClass centres the question at 80 characters, not 81", () => {
    expect(qFitClass("a".repeat(80))).toBe(" q-center");
    expect(qFitClass("a".repeat(81))).toBe("");
  });
  it("passFact says the passes in plain words (live-briefs spec §4.2 table)", () => {
    expect(passFact(0)).toBe("One research round");
    expect(passFact(1)).toBe("Went back once to fill gaps");
    expect(passFact(2)).toBe("Went back twice to fill gaps");
    expect(passFact(3)).toBe("Went back 3 times to fill gaps");
    expect(passFact(0, 1)).toBe("One research round · went back once for your notes");
    expect(passFact(1, 2)).toBe("Went back once to fill gaps · went back twice for your notes");
    expect(passFact(2, 4)).toBe("Went back twice to fill gaps · went back 4 times for your notes");
  });
  it("fmtScore prints two decimals and null for no score", () => {
    expect(fmtScore(0.8)).toBe("0.80");
    expect(fmtScore(null)).toBeNull();
    expect(fmtScore(undefined)).toBeNull();
  });
  it("notFoundClause pluralises", () => {
    expect(notFoundClause(toSessionView({ ...base, coverage: coverage([]) }))).toBe("");
    expect(notFoundClause(toSessionView({ ...base, coverage: coverage(["a"]) }))).toBe(" · 1 target not found");
    expect(notFoundClause(toSessionView({ ...base, coverage: coverage(["a", "b"]) }))).toBe(" · 2 targets not found");
  });
  it("meterClass paints exactly 0.80 yellow on purpose", () => {
    expect(meterClass(0.8)).toBe("warn");
    expect(meterClass(0.81)).toBe("ok");
    expect(meterClass(0.39)).toBe("danger");
    expect(meterClass(Number.NaN)).toBeNull();
  });
  it("time helpers", () => {
    expect(fmtSeconds(552)).toBe("9m 12s");
    expect(fmtSeconds(0.207)).toBe("0m 00s");
    expect(fmtSeconds(null)).toBeNull();
    expect(fmtClock("2026-09-16T14:02:11Z")).toBe("14:02Z");
    expect(fmtClock(null)).toBeNull();
  });
  it("fmtSeconds rounds the total once before splitting minutes and seconds", () => {
    expect(fmtSeconds(119.7)).toBe("2m 00s");
    expect(fmtSeconds(59.5)).toBe("1m 00s");
    expect(fmtSeconds(3599.9)).toBe("60m 00s");
    expect(fmtSeconds(125)).toBe("2m 05s");
  });
});
