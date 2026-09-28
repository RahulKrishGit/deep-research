"use client";
import { useLayoutEffect, useRef } from "react";
import { ARCS, STAGES, type NodeId, type PaintedMark, type RunState } from "@/lib/run-state";

interface Props { marks: Partial<Record<NodeId, PaintedMark>>; run: RunState; withArcs: boolean; id?: string }

/* Seven rows, one per graph node; data-stage binds the arcs to the node, never to list position.
   With arcs, the list sits in .spine-wrap[data-loop][data-arc] under an SVG whose path is measured
   from the two rows' bullets, exactly as drawLoop() measures it. */
export function Spine({ marks, run, withArcs, id = "spine" }: Props) {
  const wrap = useRef<HTMLDivElement>(null);
  const list = useRef<HTMLOListElement>(null);
  const done = (st: string | undefined) => st === "done" || st === "loop";
  const rows = STAGES.map((s, i) => {
    const st = marks[s.id] || "pending";
    const prev = i > 0 ? marks[STAGES[i - 1].id] || "pending" : null;
    const showLoop = run.rearmedFirst === s.id && st === "loop";
    return (
      <li key={s.id} data-stage={s.id} data-state={st} data-fed={prev === null ? undefined : done(prev) ? "1" : "0"} style={{ ["--delay" as string]: String(i * 60) }}>
        <span className="bullet" aria-hidden="true">{i + 1}</span>
        <span>
          <span className="stage-name">{s.label}<span className="sr">{st === "active" ? " (in progress)" : ""}</span></span>
          <span className="stage-meta">{run.captions[s.id] || s.meta}</span>
          <span className="stage-meta loops" hidden={!showLoop}>{showLoop ? "↺" : ""}</span>
        </span>
      </li>
    );
  });
  useLayoutEffect(() => {
    if (!withArcs) return;
    const draw = () => {
      const host = wrap.current, ol = list.current;
      const svg = host?.querySelector<SVGSVGElement>("svg.loop-layer");
      if (!host || !ol || !svg || !run.arc) return;
      const arc = ARCS[run.arc];
      const from = ol.querySelector<HTMLElement>(`li[data-stage="${arc.from}"] .bullet`);
      const to = ol.querySelector<HTMLElement>(`li[data-stage="${arc.to}"] .bullet`);
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
    };
    draw();
    window.addEventListener("resize", draw);
    return () => window.removeEventListener("resize", draw);
  }, [withArcs, run.arc, run.loop, marks]);
  if (!withArcs) return <ol className="spine-lg" id={id} ref={list}>{rows}</ol>;
  return (
    <div className="spine-wrap" id="spineWrap" data-loop={run.loop} {...(run.arc ? { "data-arc": run.arc } : {})} ref={wrap}>
      <svg className="loop-layer" aria-hidden="true"><path className="loop-base" /><path className="loop-flow" /><path className="loop-head" /></svg>
      <ol className="spine-lg" id={id} ref={list}>{rows}</ol>
    </div>
  );
}
