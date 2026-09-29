import { describe, expect, it } from "vitest";
import { drawLoopArc } from "../../components/loop-arc";

const rect = (left: number, top: number, width: number, height: number) =>
  ({ left, top, width, height, right: left + width, bottom: top + height, x: left, y: top, toJSON() { return this; } }) as DOMRect;

describe("drawLoopArc", () => {
  it("runs from the reviewer's bullet centre to the destination's, with its arrowhead", () => {
    document.body.innerHTML = `<div id="w"><svg class="loop-layer"><path class="loop-base"></path><path class="loop-flow"></path><path class="loop-head"></path></svg>
      <ol id="l"><li data-stage="researcher"><span class="bullet"></span></li><li data-stage="report_reviewer"><span class="bullet"></span></li></ol></div>`;
    const host = document.getElementById("w")!, list = document.getElementById("l")!;
    host.getBoundingClientRect = () => rect(0, 0, 600, 500);
    list.querySelector<HTMLElement>('[data-stage="researcher"] .bullet')!.getBoundingClientRect = () => rect(12, 100, 33, 33);
    list.querySelector<HTMLElement>('[data-stage="report_reviewer"] .bullet')!.getBoundingClientRect = () => rect(12, 400, 33, 33);
    drawLoopArc(host, list, "extra_pass");
    expect(host.querySelector(".loop-base")!.getAttribute("d")).toBe("M 28.5 416.5 H 4 V 116.5 H 38.5");
    expect(host.querySelector(".loop-flow")!.getAttribute("d")).toBe("M 28.5 416.5 H 4 V 116.5 H 38.5");
    expect(host.querySelector(".loop-head")!.getAttribute("d")).toBe("M 31.5 112.5 L 39.5 116.5 L 31.5 120.5 Z");
    expect(host.querySelector("svg")!.getAttribute("width")).toBe("600");
  });
  it("draws nothing without an arc or a laid-out host", () => {
    document.body.innerHTML = `<div id="w"><svg class="loop-layer"><path class="loop-base"></path></svg><ol id="l"></ol></div>`;
    const host = document.getElementById("w")!, list = document.getElementById("l")!;
    drawLoopArc(host, list, null);
    drawLoopArc(host, list, "redraft");
    expect(host.querySelector(".loop-base")!.hasAttribute("d")).toBe(false);
  });
});
