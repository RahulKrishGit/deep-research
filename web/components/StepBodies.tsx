"use client";
// The step bodies of the running spine's briefs (notes-progress-report spec §6.3-§6.7). Each renders
// one step's `.ln` lines, numbered from `first` so a brief keeps one stagger (live-briefs pick 1A).
import type { CSSProperties } from "react";
import {
  VERIFY_PLACEHOLDER,
  type BriefBody, type CheckLine, type SlotLine, type StatusStack, type TickerLine,
} from "@/lib/briefs";
import { useTicker } from "@/lib/ticker";
import { useTween } from "@/lib/tween";

const lineStyle = (i: number, r?: number) =>
  ({ ["--i" as string]: String(i), ...(r === undefined ? {} : { ["--r" as string]: String(r) }) }) as CSSProperties;

/* ✓ drawn, ● running, ○ waiting, amber ✗: one 14px slot, so the column never shifts. */
export function Mark() {
  return (
    <span className="mk" aria-hidden="true">
      <span className="ring" /><span className="dotc" />
      <svg viewBox="0 0 14 14"><path d="M3 7.4 L6 10.2 L11.2 4.2" /></svg>
      <svg className="x" viewBox="0 0 14 14"><path d="M4 4 L10 10" /><path d="M10 4 L4 10" /></svg>
    </span>
  );
}

/* A status line: every text stacked in one grid cell, the `on`th one shown (the others cross-fade out). */
export function StatusLine({ stack, i }: { stack: StatusStack; i: number }) {
  return (
    <div className="ln b-now xf rise" style={lineStyle(i)}>
      {stack.texts.map((text, k) => (
        <p key={k} data-on={k === stack.on ? "1" : "0"} aria-hidden={k === stack.on ? undefined : "true"}>{text}</p>
      ))}
    </div>
  );
}

/* §6.3: Planning's slots — a skeleton bar that cross-fades to its title, a mark and a fact. */
export function PlanningSlots({ slots, first }: { slots: SlotLine[]; first: number }) {
  return (
    <div className="ps-topics ps-slots" role="list">
      {slots.map((slot, k) => (
        <div key={slot.key} className="ln" role="listitem" data-topic={slot.mark} data-gone={slot.gone ? "1" : undefined}
          data-rise={slot.rise ? "1" : undefined} aria-hidden={slot.gone ? "true" : undefined} style={lineStyle(first + k)}>
          <Mark />
          <span className="tt xf rise">
            <span className="sk" data-on={slot.title === null ? "1" : "0"} style={slot.width ? { width: slot.width } : undefined} />
            <span data-on={slot.title === null ? "0" : "1"}>{slot.title === null ? null : <><span className="tn">{slot.n}</span>{slot.title}</>}</span>
          </span>
          <span className="tf">{slot.fact}</span>
        </div>
      ))}
    </div>
  );
}

function Count({ value }: { value: number }) { return <>{useTween(value)}</>; }

/* §6.4: the lead, a determinate bar and the Rated / Strong / Fair / Weak split. */
export function EvaluatingLines({ body, first }: { body: Extract<BriefBody, { kind: "evaluating" }>; first: number }) {
  const s = body.stats;
  const stat = (label: string, value: number | null, of?: number) => (
    <div className="stat"><span className="eyebrow">{label}</span><span className="v">{value === null ? "not yet" : <><Count value={value} />{of === undefined ? "" : " of " + of}</>}</span></div>
  );
  return (
    <>
      <p className="ln b-now" style={lineStyle(first)}>{body.lead}</p>
      {body.bar === null ? null : <span className="ln pb" style={lineStyle(first + 1)}><i style={{ transform: `scaleX(${body.bar})` }} /></span>}
      {s === null ? null : (
        <div className="ln stats" style={lineStyle(first + 2)}>
          {stat("Rated", s.rated, s.toRate)}{stat("Strong", s.strong)}{stat("Fair", s.fair)}{stat("Weak", s.weak)}
        </div>
      )}
    </>
  );
}

/* §6.5, §6.6: the ticker box — the last two samples stacked, the newest showing, paced by useTicker. */
function TickerBox({ samples, placeholder, i }: { samples: TickerLine[]; placeholder: string; i: number }) {
  const { current, previous } = useTicker(samples.at(-1) ?? null);
  return (
    <div className="ln tickbox" style={lineStyle(i)}>
      <div className="xf rise">
        <p className="b-sub" data-on={current ? "0" : "1"} aria-hidden={current ? "true" : undefined}>{placeholder}</p>
        {[previous, current].map((line) => line === null ? null : (
          <div key={line.key} data-on={line === current ? "1" : "0"} aria-hidden={line === current ? undefined : "true"}>
            <p className="qt">{line.quoted ? "“" + line.text + "”" : line.text}</p>
            <p className="vd" data-kept={line.kept ? "1" : "0"}><b>{line.verdict}</b>{line.where ? " · " + line.where : ""}</p>
          </div>
        ))}
      </div>
    </div>
  );
}

export function VerifyingLines({ body, first }: { body: Extract<BriefBody, { kind: "verifying" }>; first: number }) {
  if (body.empty !== null) return <p className="ln b-sub" style={lineStyle(first)}>{body.empty}</p>;
  const t = body.tally;
  return (
    <>
      <p className="ln eyebrow" style={lineStyle(first)}>Just checked</p>
      <TickerBox samples={body.samples} placeholder={VERIFY_PLACEHOLDER} i={first + 1} />
      <span className="ln pb" style={lineStyle(first + 2)}><i style={{ transform: `scaleX(${body.bar})` }} /></span>
      {t === null ? null : (
        <p className="ln b-facts" style={lineStyle(first + 3)}>
          <b>{t.checked}</b> of {t.total} checked · <b>{t.verified}</b> verified · <b>{t.corrected}</b> corrected · <b>{t.dropped}</b> dropped
        </p>
      )}
    </>
  );
}

export function WritingLines({ body, first }: { body: Extract<BriefBody, { kind: "writing" }>; first: number }) {
  const t = body.tally;
  return (
    <>
      <p className="ln eyebrow" style={lineStyle(first)}>Just written</p>
      <TickerBox samples={body.samples} placeholder={body.placeholder} i={first + 1} />
      <span className="ln pb" style={lineStyle(first + 2)}><i style={{ transform: `scaleX(${body.bar})` }} /></span>
      {t === null ? null : (
        <p className="ln b-facts" style={lineStyle(first + 3)}>
          <b>{t.checked}</b> of {t.drafted} sentences checked · <span className="ok">✓ {t.backed}</span> backed · <span className="no">✗ {t.removed}</span> removed
          {t.partsTotal > 0 ? ` · section ${t.partsReturned} of ${t.partsTotal}` : ""}{t.unchecked > 0 ? ` · ${t.unchecked} not checked` : ""}
        </p>
      )}
    </>
  );
}

/* §6.7: one criterion or note — its mark, its text, and its fact cross-fading from "reading". `r` is
   its place in the reveal, 60 ms apart once the review lands. */
function CheckRow({ line, i, r }: { line: CheckLine; i: number; r: number }) {
  return (
    <div className="ln" role="listitem" data-topic={line.mark} style={lineStyle(i, r)}>
      <Mark />
      <span className="tt">{line.text}</span>
      <span className="tf xf">
        <span data-on={line.landed ? "0" : "1"} aria-hidden={line.landed ? "true" : undefined}>{line.before}</span>
        <span data-on={line.landed ? "1" : "0"} aria-hidden={line.landed ? undefined : "true"}>{line.fact}</span>
      </span>
    </div>
  );
}

export function ReviewingLines({ body, first }: { body: Extract<BriefBody, { kind: "reviewing" }>; first: number }) {
  let i = first;
  const status = i++;
  const bar = i++;
  const criteria = body.criteria.map((line, r) => <CheckRow key={line.key} line={line} i={i++} r={r} />);
  const head = body.notes.length > 0 ? i++ : -1;
  const notes = body.notes.map((line, r) => <CheckRow key={line.key} line={line} i={i++} r={body.criteria.length + r} />);
  return (
    <>
      <StatusLine stack={body.status} i={status} />
      <span className="ln pb ind" data-on={body.waiting ? "1" : "0"} aria-hidden="true" style={lineStyle(bar)}><i /></span>
      <div className="ps-topics rv-list" role="list" aria-label="Checks">{criteria}</div>
      {head < 0 ? null : (
        <>
          <div className="ln rv-notes-h" style={lineStyle(head)}><div className="b-rule" /><span className="eyebrow">Your notes</span></div>
          <div className="ps-topics rv-list" role="list" aria-label="Your notes">{notes}</div>
        </>
      )}
    </>
  );
}
