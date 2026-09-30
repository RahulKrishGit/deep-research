"use client";
import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { rowBrief, subtitleText, type RowState, type Subtitle } from "@/lib/briefs";
import { STAGES, type NodeId, type PaintedMark, type RunState } from "@/lib/run-state";
import { useTween } from "@/lib/tween";
import { useLoopArc } from "./loop-arc";

/* How long the two hand-off roles stay on their rows (live-briefs spec §4.3 motion table): past the
   last line's rise with ten topics, 900 + 10 × 60 + 240 = 1740 ms. */
export const HANDOFF_HOLD_MS = 2000;
/* `awaiting` is the row the active row just left before that row was marked done. A route decision
   moves the active row one event before the row it leaves reports its own completion
   (graph.route.decided precedes the reviewer's graph.node.completed, web/lib/run-state.ts:145-158 and
   :100-108; 150 ms apart in replay). Until that completion arrives the awaited row keeps painting as
   the active row, open; then it takes the `from` role and folds with the 3B timings. */
interface Handoff { from: NodeId | null; to: NodeId | null; awaiting: NodeId | null }
interface Props { marks: Partial<Record<NodeId, PaintedMark>>; run: RunState; onToggle(id: NodeId): void }

const lineStyle = (i: number) => ({ ["--i" as string]: String(i) }) as CSSProperties;
const finishedState = (st: RowState | undefined) => st === "done" || st === "loop";

function ResearchSubtitle({ subtitle }: { subtitle: Extract<Subtitle, { kind: "research" }> }) {
  const done = useTween(subtitle.done), pages = useTween(subtitle.pages), findings = useTween(subtitle.findings);
  return <>{subtitleText({ ...subtitle, done, pages, findings })}</>;
}

/* The running stage's spine (picks 1A, 2C, 3B): each row is li > bullet + (head, brief). The active
   row is always open; a done or loop row shows its outcome and reopens from its head; a pending row
   never opens. The Failed and Stopped stages keep the compact <Spine>. */
export function BriefSpine({ marks, run, onToggle }: Props) {
  const wrap = useRef<HTMLDivElement>(null);
  const list = useRef<HTMLOListElement>(null);
  // The hand-off is the render in which the active row changes: the row that was active (now done)
  // and the row that is active now carry data-handoff for HANDOFF_HOLD_MS, so the stylesheet can
  // time them as one choreography. Derived during render (React's "adjust state on prop change").
  // When the row that was active is not done yet and the new active row is its successor, that row is
  // awaited instead: its mark turning done or loop gives it the `from` role, restarting the hold.
  const [prevActive, setPrevActive] = useState<NodeId | null>(run.active);
  const [handoff, setHandoff] = useState<Handoff | null>(null);
  if (run.active !== prevActive) {
    setPrevActive(run.active);
    const from = prevActive && finishedState(marks[prevActive]) ? prevActive : null;
    const successor = prevActive === null ? null : STAGES[STAGES.findIndex((s) => s.id === prevActive) + 1]?.id ?? null;
    const awaiting = from === null && prevActive !== null && successor === run.active && run.finalStatus === null ? prevActive : null;
    setHandoff({ from, to: run.active, awaiting });
  } else if (handoff?.awaiting && handoff.from === null && handoff.to === run.active && finishedState(marks[handoff.awaiting])) {
    setHandoff({ from: handoff.awaiting, to: run.active, awaiting: null });
  }
  useEffect(() => {
    if (!handoff) return;
    // An awaited hand-off (a route decision ahead of the row's own completion) waits for that
    // completion however long it takes; only the roles themselves time out.
    if (handoff.awaiting && handoff.from === null) return;
    const timer = setTimeout(() => setHandoff(null), HANDOFF_HOLD_MS);
    return () => clearTimeout(timer);
  }, [handoff]);
  useLoopArc(true, wrap, list, run.arc, [run.arc, run.loop, marks]);
  const rows = STAGES.map((s, i) => {
    const awaited = handoff?.awaiting === s.id;
    const st: RowState = awaited ? "active" : marks[s.id] || "pending";
    const prev = i > 0 ? marks[STAGES[i - 1].id] || "pending" : null;
    const finished = finishedState(st);
    const open = st === "active" || (finished && run.open.has(s.id));
    const brief = rowBrief(run, s.id, st);
    const showLoop = run.rearmedFirst === s.id && st === "loop";
    const role = handoff?.from === s.id ? "from" : handoff?.to === s.id ? "to" : undefined;
    // The head never changes element, so its subtitle can cross-fade when the row finishes; a done
    // or loop row adds a button[aria-expanded] laid over the whole head, named by the head itself.
    const head = (
      <div className="ps-head">
        <span className="stage-name" id={`name-${s.id}`}>{s.label}<span className="sr">{st === "active" ? " (in progress)" : ""}</span></span>
        <span className="stage-meta xf" id={`meta-${s.id}`}>
          <span className="m-live" aria-hidden={finished ? "true" : undefined}>
            {brief.subtitle.kind === "research" ? <ResearchSubtitle subtitle={brief.subtitle} /> : brief.subtitle.text}
          </span>
          <span className="m-out" aria-hidden={finished ? undefined : "true"}>{brief.outcome}</span>
        </span>
        <span className="stage-meta loops" hidden={!showLoop}>{showLoop ? "↺" : ""}</span>
        {finished ? <button type="button" className="ps-toggle" aria-expanded={open} aria-controls={`brief-${s.id}`} aria-labelledby={`name-${s.id} meta-${s.id}`} onClick={() => onToggle(s.id)} /> : null}
      </div>
    );
    let n = 0;
    const lines: ReactNode[] = [];
    if (brief.why) lines.push(<p key="why" className="ln b-why" data-kind={brief.why.kind} style={lineStyle(n++)}>{brief.why.text}</p>);
    // live-briefs spec §4.7 (D9): the reader's notes, acknowledged at the top of the running row's
    // brief — a muted dot, then the run's reading of the note, never the note echoed back. Each line
    // is a polite live region: nothing moves focus, so a screen reader hears the line whole when
    // "Reading your note…" becomes "Got it — …".
    for (const ack of brief.acks) {
      lines.push(
        <p key={`ack-${ack.key}`} className="ln ack" data-ack={ack.key} aria-live="polite" aria-atomic="true" style={lineStyle(n++)}>
          <span className="d" aria-hidden="true" />
          <span>{ack.lead}{ack.said !== null ? <span className="said">{ack.said}</span> : null}{ack.rest}</span>
        </p>,
      );
    }
    if (brief.earlier) lines.push(<p key="ack-earlier" className="ln ack" style={lineStyle(n++)}><span className="d" aria-hidden="true" /><span>{brief.earlier}</span></p>);
    if (brief.sentence) lines.push(<p key="sentence" className="ln b-line" style={lineStyle(n++)}>{brief.sentence}</p>);
    if (brief.topics) {
      lines.push(
        <div key="topics" className="ps-topics" role="list">
          {brief.topics.map((t) => (
            <div key={t.key} className="ln" role="listitem" data-topic={t.state} style={lineStyle(n++)}>
              <span className="mk" aria-hidden="true"><span className="ring" /><span className="dotc" /><svg viewBox="0 0 14 14"><path d="M3 7.4 L6 10.2 L11.2 4.2" /></svg></span>
              <span className="tt"><span className="tn">{t.n}</span>{t.title}</span>
              <span className="tf">{t.fact}</span>
            </div>
          ))}
        </div>,
      );
    }
    if (brief.titles) {
      lines.push(
        <div key="titles" className="ps-titles" role="list">
          {brief.titles.map((title, k) => (
            <div key={k} className="ln" role="listitem" style={lineStyle(n++)}><span className="tn">{k + 1}</span><span>{title}</span></div>
          ))}
        </div>,
      );
    }
    return (
      <li key={s.id} className="spine-row" data-stage={s.id} data-state={st} data-fed={prev === null ? undefined : finishedState(prev) ? "1" : "0"}
        data-open={open ? "1" : "0"} data-handoff={role} data-toggle={finished ? "1" : undefined}
        aria-current={st === "active" && !awaited ? "step" : undefined} style={{ ["--delay" as string]: String(i * 60) }}>
        <span className="bullet" aria-hidden="true">{i + 1}</span>
        <div className="ps-body">
          {head}
          <div className="ps-x" id={`brief-${s.id}`} aria-hidden={open ? undefined : "true"}>
            <div className="ps-xi"><div className="ps-brief">{lines}</div></div>
          </div>
        </div>
      </li>
    );
  });
  return (
    <div className="spine-wrap" id="spineWrap" data-loop={run.loop} {...(run.arc ? { "data-arc": run.arc } : {})} ref={wrap}>
      <svg className="loop-layer" aria-hidden="true"><path className="loop-base" /><path className="loop-flow" /><path className="loop-head" /></svg>
      <ol className="spine-lg briefs" id="spine" ref={list}>{rows}</ol>
    </div>
  );
}
