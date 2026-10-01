"use client";
import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { notRunText, rowBrief, stoppedSubtitle, subtitleText, type RowState, type Subtitle } from "@/lib/briefs";
import { ARCS, STAGES, type NodeId, type PaintedMark, type RunState } from "@/lib/run-state";
import { useTween } from "@/lib/tween";
import { useLoopArc } from "./loop-arc";
import { EvaluatingLines, PlanningSlots, ReviewingLines, StatusLine, VerifyingLines, WritingLines } from "./StepBodies";

/* How long the two hand-off roles stay on their rows (live-briefs spec §4.3 motion table): past the
   last line's rise with ten topics, 900 + 10 × 60 + 240 = 1740 ms. */
export const HANDOFF_HOLD_MS = 2000;
/* `awaiting` is the row the active row just left before that row was marked done. A route decision
   moves the active row one event before the row it leaves reports its own completion
   (graph.route.decided precedes the reviewer's graph.node.completed, web/lib/run-state.ts:145-158 and
   :100-108; 150 ms apart in replay). Until that completion arrives the awaited row keeps painting as
   the active row, open; then it takes the `from` role and folds with the 3B timings.
   `held` (decision D39, notes-progress-report spec §6.7 as amended): a loop route — the extra pass, a
   redraft, a note pass, a note redraft — holds Reviewing as the awaited row for HANDOFF_HOLD_MS, open on
   its checks and its verdict, while the row the run goes back to waits, closed and pending, with no role;
   then Reviewing takes the `from` role and the ordinary hand-off runs. The hold paints; it never marks. */
interface Handoff { from: NodeId | null; to: NodeId | null; awaiting: NodeId | null; held: boolean }
/* `frozen`: the row the reader stopped the run at, on the stopped stage (notes-progress-report spec §8.5). */
/* `now`: RunningPipeline's one-second clock (notes-progress-report spec §6.9), which the elapsed times tick with. */
interface Props { marks: Partial<Record<NodeId, PaintedMark>>; run: RunState; onToggle(id: NodeId): void; frozen?: NodeId; now?: number }

const lineStyle = (i: number) => ({ ["--i" as string]: String(i) }) as CSSProperties;
const finishedState = (st: RowState | undefined) => st === "done" || st === "loop";

function ResearchSubtitle({ subtitle }: { subtitle: Extract<Subtitle, { kind: "research" }> }) {
  /* An unmeasured count (null) tweens as 0 so the hooks stay unconditional, and is passed on as null:
     its phrase stays out of the line (D19). */
  const done = useTween(subtitle.done), pages = useTween(subtitle.pages ?? 0), findings = useTween(subtitle.findings ?? 0);
  return <>{subtitleText({ ...subtitle, done, pages: subtitle.pages === null ? null : pages, findings: subtitle.findings === null ? null : findings })}</>;
}

/* The running stage's spine (picks 1A, 2C, 3B): each row is li > bullet + (head, brief). The active
   row is always open; a done or loop row shows its outcome and reopens from its head; a pending row
   never opens. The Failed and service-stopped stages keep the compact <Spine>.
   With `frozen` (notes-progress-report spec §8.5) it is the stopped stage's spine, frozen at the row the
   reader stopped: the rows before it as recorded, that row "stopped" (it opens to its frozen brief),
   every later row "off" — with no hand-off and no arc. */
export function BriefSpine({ marks, run, onToggle, frozen, now }: Props) {
  const wrap = useRef<HTMLDivElement>(null);
  const list = useRef<HTMLOListElement>(null);
  // The hand-off is the render in which the active row changes: the row that was active (now done)
  // and the row that is active now carry data-handoff for HANDOFF_HOLD_MS, so the stylesheet can
  // time them as one choreography. Derived during render (React's "adjust state on prop change").
  // When the row that was active is not done yet and the new active row is its successor, that row is
  // awaited instead: its mark turning done or loop gives it the `from` role, restarting the hold.
  const [prevActive, setPrevActive] = useState<NodeId | null>(run.active);
  const [handoff, setHandoff] = useState<Handoff | null>(null);
  if (frozen === undefined && run.active !== prevActive) {
    setPrevActive(run.active);
    if (prevActive === "report_reviewer" && run.arc !== null && run.active === ARCS[run.arc].to) {
      // D39: a loop route — the run left Reviewing for the lit arc's own destination. A finalize or end
      // route, graph.session.completed and session.stopped all clear the arc; it stays lit when the
      // loop's own start event lands in the same render; a burst already past the destination hands
      // over as usual.
      setHandoff({ from: null, to: run.active, awaiting: "report_reviewer", held: true });
    } else {
      const from = prevActive && finishedState(marks[prevActive]) ? prevActive : null;
      const successor = prevActive === null ? null : STAGES[STAGES.findIndex((s) => s.id === prevActive) + 1]?.id ?? null;
      const awaiting = from === null && prevActive !== null && successor === run.active && run.finalStatus === null ? prevActive : null;
      setHandoff({ from, to: run.active, awaiting, held: false });
    }
  } else if (handoff?.awaiting && !handoff.held && handoff.from === null && handoff.to === run.active && finishedState(marks[handoff.awaiting])) {
    setHandoff({ from: handoff.awaiting, to: run.active, awaiting: null, held: false });
  }
  useEffect(() => {
    if (!handoff) return;
    // D39: the hold is a dwell on a timer, kept under reduced motion; when it ends Reviewing takes the
    // `from` role (a loop's own completion of the reviewer is inert, so no mark would ever release it).
    if (handoff.held) {
      const to = handoff.to;
      const held = setTimeout(() => setHandoff({ from: "report_reviewer", to, awaiting: null, held: false }), HANDOFF_HOLD_MS);
      return () => clearTimeout(held);
    }
    // An awaited hand-off (a route decision ahead of the row's own completion) waits for that
    // completion however long it takes; only the roles themselves time out.
    if (handoff.awaiting && handoff.from === null) return;
    const timer = setTimeout(() => setHandoff(null), HANDOFF_HOLD_MS);
    return () => clearTimeout(timer);
  }, [handoff]);
  useLoopArc(frozen === undefined, wrap, list, run.arc, [run.arc, run.loop, marks]);
  const frozenAt = frozen === undefined ? -1 : STAGES.findIndex((s) => s.id === frozen);
  const rows = STAGES.map((s, i) => {
    const awaited = handoff?.awaiting === s.id;
    const st: RowState = frozenAt >= 0
      ? (i < frozenAt ? marks[s.id] || "pending" : i === frozenAt ? "stopped" : "off")
      : awaited ? "active" : handoff?.held && handoff.to === s.id ? "pending" : marks[s.id] || "pending";
    const prev = i > 0 ? marks[STAGES[i - 1].id] || "pending" : null;
    const finished = finishedState(st);
    // D39: once the hold has ended, the hollow Reviewing row (the loop's rearm took its mark) reopens to
    // its checks and its verdict, until Reviewing runs again (graph.node.started resets run.reviewing).
    const reopenable = s.id === "report_reviewer" && st === "pending" && run.rearmed.report_reviewer === true && run.reviewing.landed;
    const openable = finished || st === "stopped" || reopenable;
    const open = st === "active" || (openable && run.open.has(s.id));
    const brief = rowBrief(run, s.id, st, now);
    const showLoop = run.rearmedFirst === s.id && st === "loop";
    const role = handoff?.held ? undefined : handoff?.from === s.id ? "from" : handoff?.to === s.id ? "to" : undefined;
    const fixed = st === "stopped" ? stoppedSubtitle(run, s.id) : st === "off" ? notRunText(run, s.id) : null;
    // The head never changes element, so its subtitle can cross-fade when the row finishes; a done
    // or loop row adds a button[aria-expanded] laid over the whole head, named by the head itself.
    const head = (
      <div className="ps-head">
        <span className="stage-name" id={`name-${s.id}`}>{s.label}<span className="sr">{st === "active" ? " (in progress)" : st === "stopped" ? " (stopped here)" : ""}</span></span>
        <span className="stage-meta xf" id={`meta-${s.id}`}>
          <span className="m-live" aria-hidden={finished ? "true" : undefined}>
            {fixed !== null ? fixed : brief.subtitle.kind === "research" ? <ResearchSubtitle subtitle={brief.subtitle} /> : brief.subtitle.text}
          </span>
          <span className="m-out" aria-hidden={finished ? undefined : "true"}>{fixed !== null ? null : brief.outcome}</span>
        </span>
        <span className="stage-meta loops" hidden={!showLoop}>{showLoop ? "↺" : ""}</span>
        {openable ? <button type="button" className="ps-toggle" aria-expanded={open} aria-controls={`brief-${s.id}`} aria-labelledby={`name-${s.id} meta-${s.id}`} onClick={() => onToggle(s.id)} /> : null}
      </div>
    );
    let n = 0;
    const lines: ReactNode[] = [];
    if (brief.why) lines.push(<p key="why" className="ln b-why" data-kind={brief.why.kind} style={lineStyle(n++)}>{brief.why.text}</p>);
    const body = brief.body;
    // notes-progress-report spec §6.3: Planning's status line leads its brief; the acknowledgements follow it.
    if (body.kind === "planning") lines.push(<StatusLine key="status" stack={body.status} i={n++} />);
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
    if (body.kind === "sentence") lines.push(<p key="sentence" className="ln b-line" style={lineStyle(n++)}>{body.text}</p>);
    if (body.kind === "research") {
      lines.push(
        <div key="topics" className="ps-topics" role="list">
          {body.topics.map((t) => {
            // A topic still running when the reader stopped reads "stopped", with the ring; one that had
            // not started reads "not run", with no ring (§8.5).
            const halted = st === "stopped" && t.state === "running";
            const unrun = st === "stopped" && t.state === "waiting";
            return (
              <div key={t.key} className="ln" role="listitem" data-topic={halted ? "stopped" : t.state} style={lineStyle(n++)}>
                <span className="mk" aria-hidden="true"><span className="ring" /><span className="dotc" /><svg viewBox="0 0 14 14"><path d="M3 7.4 L6 10.2 L11.2 4.2" /></svg></span>
                <span className="tt"><span className="tn">{t.n}</span>{t.title}</span>
                <span className="tf">{halted ? "stopped" : unrun ? "not run" : t.fact}</span>
              </div>
            );
          })}
        </div>,
      );
    }
    // notes-progress-report spec §6.3-§6.7: each step's own body, numbered on from the lines above it.
    if (body.kind === "planning") lines.push(<PlanningSlots key="body" slots={body.slots} first={n} />);
    if (body.kind === "evaluating") lines.push(<EvaluatingLines key="body" body={body} first={n} />);
    if (body.kind === "verifying") lines.push(<VerifyingLines key="body" body={body} first={n} />);
    if (body.kind === "writing") lines.push(<WritingLines key="body" body={body} first={n} />);
    if (body.kind === "reviewing") lines.push(<ReviewingLines key="body" body={body} first={n} />);
    return (
      <li key={s.id} className="spine-row" data-stage={s.id} data-state={st} data-fed={prev === null ? undefined : finishedState(prev) ? "1" : "0"}
        data-open={open ? "1" : "0"} data-handoff={role} data-toggle={openable ? "1" : undefined}
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
