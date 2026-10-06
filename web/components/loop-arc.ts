"use client";
// The return arc (DESIGN.md §3.5), measured from the two rows' bullets exactly as the prototype's
// drawLoop() measures it, and kept attached while rows change height.
import { useLayoutEffect, type DependencyList, type RefObject } from "react";
import { ARCS } from "@/lib/run-state";

export type ArcKind = keyof typeof ARCS;

export function drawLoopArc(host: HTMLElement | null, list: HTMLElement | null, arc: ArcKind | null): void {
  const svg = host?.querySelector<SVGSVGElement>("svg.loop-layer");
  if (!host || !list || !svg || !arc) return;
  const route = ARCS[arc];
  const from = list.querySelector<HTMLElement>(`li[data-stage="${route.from}"] .bullet`);
  const to = list.querySelector<HTMLElement>(`li[data-stage="${route.to}"] .bullet`);
  if (!from || !to) return;
  const hostRect = host.getBoundingClientRect();
  const w = Math.round(hostRect.width), h = Math.round(hostRect.height);
  if (!w || !h) return;
  svg.setAttribute("width", String(w)); svg.setAttribute("height", String(h));
  const leave = from.getBoundingClientRect(), enter = to.getBoundingClientRect();
  const x1 = enter.left - hostRect.left + enter.width / 2, y1 = enter.top - hostRect.top + enter.height / 2;
  const x2 = leave.left - hostRect.left + leave.width / 2, y2 = leave.top - hostRect.top + leave.height / 2;
  const r = Math.max(2, x1 - enter.width / 2 - 8);
  const d = `M ${x2} ${y2} H ${r} V ${y1} H ${x1 + 10}`;
  svg.querySelector(".loop-base")?.setAttribute("d", d);
  svg.querySelector(".loop-flow")?.setAttribute("d", d);
  svg.querySelector(".loop-head")?.setAttribute("d", `M ${x1 + 3} ${y1 - 4} L ${x1 + 11} ${y1} L ${x1 + 3} ${y1 + 4} Z`);
}

/* Re-measured on every layout pass, on a window resize, whenever the wrap itself changes size (a row
   opening or closing grows or shrinks it on every frame of the transition) and at the end of each
   row's height transition. */
export function useLoopArc(enabled: boolean, wrap: RefObject<HTMLElement | null>, list: RefObject<HTMLElement | null>, arc: ArcKind | null, deps: DependencyList): void {
  useLayoutEffect(() => {
    if (!enabled) return;
    const host = wrap.current;
    const draw = () => drawLoopArc(wrap.current, list.current, arc);
    draw();
    window.addEventListener("resize", draw);
    const observer = host && typeof ResizeObserver === "function" ? new ResizeObserver(draw) : null;
    if (observer && host) observer.observe(host);
    const onEnd = (event: TransitionEvent) => { if (event.propertyName === "grid-template-rows") draw(); };
    host?.addEventListener("transitionend", onEnd);
    return () => {
      window.removeEventListener("resize", draw);
      observer?.disconnect();
      host?.removeEventListener("transitionend", onEnd);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
}
