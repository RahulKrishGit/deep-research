"use client";
import { useRef } from "react";
import { STAGES, type NodeId, type PaintedMark, type RunState } from "@/lib/run-state";
import { useLoopArc } from "./loop-arc";

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
  useLoopArc(withArcs, wrap, list, run.arc, [withArcs, run.arc, run.loop, marks]);
  if (!withArcs) return <ol className="spine-lg" id={id} ref={list}>{rows}</ol>;
  return (
    <div className="spine-wrap" id="spineWrap" data-loop={run.loop} {...(run.arc ? { "data-arc": run.arc } : {})} ref={wrap}>
      <svg className="loop-layer" aria-hidden="true"><path className="loop-base" /><path className="loop-flow" /><path className="loop-head" /></svg>
      <ol className="spine-lg" id={id} ref={list}>{rows}</ol>
    </div>
  );
}
