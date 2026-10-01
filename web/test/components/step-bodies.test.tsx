import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { EvaluatingLines, PlanningSlots, ReviewingLines, StatusLine, VerifyingLines, WritingLines } from "../../components/StepBodies";
import { VERIFY_PLACEHOLDER, verifyTallyText, writingTallyText, type CheckLine, type SlotLine, type TickerLine } from "../../lib/briefs";

const slot = (over: Partial<SlotLine>): SlotLine => ({ key: "slot-1", n: 1, title: null, width: "78%", mark: "waiting", fact: "", gone: false, rise: false, ...over });
const check = (over: Partial<CheckLine>): CheckLine => ({ key: "completeness", text: "Covers your whole question", mark: "waiting", before: "reading", fact: "", landed: false, ...over });
const tick = (over: Partial<TickerLine>): TickerLine => ({ key: "v1", text: "Revenue rose 4%", quoted: false, verdict: "verified", kept: true, where: "an original report", ...over });

describe("StepBodies (notes-progress-report spec §6.3-§6.7)", () => {
  it("StatusLine stacks every text in one cell and shows the `on`th", () => {
    const { container } = render(<StatusLine stack={{ texts: ["A", "B", "C"], on: 1 }} i={2} />);
    const line = container.querySelector(".ln.b-now.xf")!;
    expect([...line.children].map((p) => [p.textContent, p.getAttribute("data-on"), p.getAttribute("aria-hidden")]))
      .toEqual([["A", "0", "true"], ["B", "1", null], ["C", "0", "true"]]);
    expect(line.getAttribute("style")).toBe("--i: 2;");
  });
  it("PlanningSlots: a skeleton bar until the title, a gone row marked for its fade, a late row rising", () => {
    const { container } = render(<PlanningSlots first={1} slots={[
      slot({}),
      slot({ key: "slot-2", n: 2, title: "Beta", mark: "running", fact: "checking" }),
      slot({ key: "slot-3", n: 3, gone: true }),
      slot({ key: "note-n1", n: 3, title: "Your note: x", width: null, fact: "joins the plan", rise: true }),
    ]} />);
    const rows = [...container.querySelectorAll<HTMLElement>(".ps-topics.ps-slots > .ln")];
    expect(rows.map((r) => [r.getAttribute("data-topic"), r.getAttribute("data-gone"), r.getAttribute("data-rise"), r.querySelector(".tf")!.textContent, r.getAttribute("style")])).toEqual([
      ["waiting", null, null, "", "--i: 1;"], ["running", null, null, "checking", "--i: 2;"],
      ["waiting", "1", null, "", "--i: 3;"], ["waiting", null, "1", "joins the plan", "--i: 4;"],
    ]);
    expect(rows[0].querySelector(".sk")!.getAttribute("data-on")).toBe("1");
    expect(rows[0].querySelector<HTMLElement>(".sk")!.style.width).toBe("78%");
    expect(rows[1].querySelector(".sk")!.getAttribute("data-on")).toBe("0");
    expect(rows[1].querySelector(".tt")!.textContent).toBe("2Beta");
    expect(rows[2].getAttribute("aria-hidden")).toBe("true");
    expect(rows[0].querySelectorAll(".mk svg").length).toBe(2);
  });
  it("EvaluatingLines: 'not yet' until a batch lands, the bar as a scaleX, no bar or split with nothing to rate", () => {
    const { container, rerender } = render(<EvaluatingLines first={0} body={{ kind: "evaluating", lead: "Rating 44 sources for trustworthiness and relevance", bar: 0, stats: { rated: null, toRate: 44, strong: null, fair: null, weak: null } }} />);
    expect([...container.querySelectorAll(".stats > .stat")].map((s) => s.textContent)).toEqual(["Ratednot yet", "Strongnot yet", "Fairnot yet", "Weaknot yet"]);
    expect(container.querySelector<HTMLElement>(".pb > i")!.style.transform).toBe("scaleX(0)");
    rerender(<EvaluatingLines first={0} body={{ kind: "evaluating", lead: "Rating 44 sources for trustworthiness and relevance", bar: 0.25, stats: { rated: 10, toRate: 44, strong: 5, fair: 4, weak: 1 } }} />);
    expect([...container.querySelectorAll(".stats > .stat .v")].map((s) => s.textContent)).toEqual(["10 of 44", "5", "4", "1"]);
    expect(container.querySelector<HTMLElement>(".pb > i")!.style.transform).toBe("scaleX(0.25)");
    rerender(<EvaluatingLines first={0} body={{ kind: "evaluating", lead: "No new sources to rate", bar: null, stats: null }} />);
    expect(container.querySelector(".pb")).toBeNull();
    expect(container.querySelector(".stats")).toBeNull();
    expect(container.textContent).toBe("No new sources to rate");
  });
  it("VerifyingLines: the placeholder until a sample, then the sample and its verdict; the tally reads as verifyTallyText", () => {
    const tally = { checked: 4, total: 10, verified: 2, corrected: 1, dropped: 1 };
    const { container, rerender } = render(<VerifyingLines first={0} body={{ kind: "verifying", empty: null, samples: [], bar: 0, tally: null }} />);
    const placeholder = container.querySelector(".tickbox .b-sub")!;
    expect([placeholder.textContent, placeholder.getAttribute("data-on")]).toEqual([VERIFY_PLACEHOLDER, "1"]);
    expect(container.querySelector(".b-facts")).toBeNull();
    rerender(<VerifyingLines first={0} body={{ kind: "verifying", empty: null, samples: [tick({ quoted: true, verdict: "quoted as written" })], bar: 0.4, tally }} />);
    expect(container.querySelector(".tickbox .b-sub")!.getAttribute("data-on")).toBe("0");
    expect(container.querySelector(".tickbox [data-on='1'] .qt")!.textContent).toBe("“Revenue rose 4%”");
    expect(container.querySelector(".tickbox [data-on='1'] .vd")!.textContent).toBe("quoted as written · an original report");
    expect(container.querySelector(".b-facts")!.textContent).toBe(verifyTallyText(tally));
    rerender(<VerifyingLines first={0} body={{ kind: "verifying", empty: "No findings to check", samples: [], bar: 0, tally: null }} />);
    expect(container.textContent).toBe("No findings to check");
  });
  it("WritingLines: the body's placeholder until a sample, a removed sentence not kept, the tally as writingTallyText", () => {
    const tally = { checked: 9, drafted: 12, backed: 8, removed: 1, unchecked: 2, partsReturned: 2, partsTotal: 5 };
    const placeholder = "The first sentences are being checked…";
    const { container, rerender } = render(<WritingLines first={0} body={{ kind: "writing", placeholder, samples: [], bar: 0.1, tally }} />);
    expect([container.querySelector(".tickbox .b-sub")!.textContent, container.querySelector(".tickbox .b-sub")!.getAttribute("data-on")]).toEqual([placeholder, "1"]);
    rerender(<WritingLines first={0} body={{ kind: "writing", placeholder, samples: [tick({ key: "w1", verdict: "✗ removed — no verified finding says this", kept: false, where: "Where" })], bar: 0.3, tally }} />);
    expect(container.querySelector(".b-facts")!.textContent).toBe(writingTallyText(tally));
    expect(container.querySelector(".tickbox .vd")!.getAttribute("data-kept")).toBe("0");
  });
  it("ReviewingLines: the indeterminate bar while waiting, five checks revealed in order, the notes under their own heading", () => {
    const criteria = ["a", "b", "c", "d", "e"].map((key) => check({ key }));
    const status = (on: number) => ({ texts: ["Reading", "Done", ""], on });
    const { container, rerender } = render(<ReviewingLines first={0} body={{ kind: "reviewing", status: status(0), waiting: true, criteria, notes: [] }} />);
    expect(container.querySelector(".pb.ind")!.getAttribute("data-on")).toBe("1");
    expect([...container.querySelectorAll(".rv-list > .ln")].map((r) => r.getAttribute("style")))
      .toEqual(["--i: 2; --r: 0;", "--i: 3; --r: 1;", "--i: 4; --r: 2;", "--i: 5; --r: 3;", "--i: 6; --r: 4;"]);
    expect(container.querySelector(".rv-notes-h")).toBeNull();
    rerender(<ReviewingLines first={0} body={{
      kind: "reviewing", status: status(1), waiting: false,
      criteria: [check({ key: "a", mark: "fail", fact: "a part of your question has no answer", landed: true }), ...criteria.slice(1).map((c) => ({ ...c, mark: "done" as const, landed: true }))],
      notes: [check({ key: "n1", text: "More on safety", mark: "done", fact: "honoured", landed: true })],
    }} />);
    expect(container.querySelector(".pb.ind")!.getAttribute("data-on")).toBe("0");
    const first = container.querySelector(".rv-list > .ln")!;
    expect(first.getAttribute("data-topic")).toBe("fail");
    expect([...first.querySelectorAll(".tf > span")].map((s) => [s.textContent, s.getAttribute("data-on")])).toEqual([["reading", "0"], ["a part of your question has no answer", "1"]]);
    expect(container.querySelector(".rv-notes-h")!.textContent).toBe("Your notes");
    expect(container.querySelector("[aria-label='Your notes'] > .ln")!.getAttribute("style")).toBe("--i: 8; --r: 5;");
  });
  it("Verifying and Writing tickers go back to their placeholder when a re-armed step starts again, and show the new pass's first sample at once", () => {
    const tally = { checked: 4, total: 10, verified: 4, corrected: 0, dropped: 0 };
    const verifying = (samples: TickerLine[]) => <VerifyingLines first={0} body={{ kind: "verifying", empty: null, samples, bar: 0.4, tally }} />;
    const { container, rerender } = render(verifying([tick({ key: "T1v1", text: "First pass" })]));
    expect(container.querySelector(".tickbox [data-on='1'] .qt")!.textContent).toBe("First pass");
    rerender(verifying([]));
    expect(container.querySelector(".tickbox .b-sub")!.getAttribute("data-on")).toBe("1");
    expect(container.querySelector(".tickbox .qt")).toBeNull();
    rerender(verifying([tick({ key: "T2v1", text: "Second pass" })]));
    expect(container.querySelector(".tickbox .b-sub")!.getAttribute("data-on")).toBe("0");
    expect(container.querySelector(".tickbox [data-on='1'] .qt")!.textContent).toBe("Second pass");
    const wtally = { checked: 1, drafted: 2, backed: 1, removed: 0, unchecked: 0, partsReturned: 1, partsTotal: 2 };
    const placeholder = "The first section is being drafted…";
    const writing = (samples: TickerLine[]) => <WritingLines first={0} body={{ kind: "writing", placeholder, samples, bar: 0.2, tally: wtally }} />;
    const w = render(writing([tick({ key: "T1w1", text: "Drafted once", verdict: "✓ backed by 1 finding" })]));
    w.rerender(writing([]));
    expect(w.container.querySelector(".tickbox .b-sub")!.getAttribute("data-on")).toBe("1");
    w.rerender(writing([tick({ key: "T2w1", text: "Drafted again", verdict: "✓ backed by 1 finding" })]));
    expect(w.container.querySelector(".tickbox [data-on='1'] .qt")!.textContent).toBe("Drafted again");
  });
  it("a met criterion and a passed slot say so to a screen reader, without changing the row's visible text", () => {
    const slots = render(<PlanningSlots first={0} slots={[
      slot({ key: "slot-1", n: 1, title: "Alpha", mark: "done" }),
      slot({ key: "slot-2", n: 2, title: "Beta", mark: "done", fact: "fixed" }),
      slot({ key: "slot-3", n: 3, title: "Gamma", mark: "fail", fact: "still flagged" }),
    ]} />);
    const rows = [...slots.container.querySelectorAll(".ps-slots > .ln")];
    expect(rows.map((r) => r.querySelector(".sr")?.textContent ?? null)).toEqual([" passed", null, null]);
    expect(rows.map((r) => r.querySelector(".tt")!.textContent)).toEqual(["1Alpha", "2Beta", "3Gamma"]);
    const criteria = [check({ key: "a", mark: "done", landed: true }), check({ key: "b", mark: "fail", fact: "a claim rests on a weak source", landed: true }), check({ key: "c", mark: "waiting", fact: "not checked", landed: true })];
    const review = render(<ReviewingLines first={0} body={{
      kind: "reviewing", status: { texts: ["Reading", "Done", ""], on: 1 }, waiting: false, criteria,
      notes: [check({ key: "n1", text: "More on safety", mark: "done", fact: "honoured", landed: true })],
    }} />);
    const checks = [...review.container.querySelectorAll(".rv-list .ln")];
    expect(checks.map((r) => r.querySelector(".sr")?.textContent ?? null)).toEqual([" met", null, null, null]);
    expect(checks.map((r) => r.querySelector(".tt")!.textContent)).toEqual(["Covers your whole question", "Covers your whole question", "Covers your whole question", "More on safety"]);
  });
});
