"use client";
import type { ReactNode } from "react";
import type { ResearchError, ResearchSessionResponse } from "@/lib/api";
import { HALT_HEADLINES, fmtClock, qFitClass } from "@/lib/format";
import { AGENT_ORDER, failedMarks, type RunState } from "@/lib/run-state";
import { Counters } from "./Counters";
import { Spine } from "./Spine";

const API_FAILURE: ResearchError = { error_type: "api.research.failed", source: "api", message: "", recoverable: false, timestamp: "", details: {} };

export function FailedStage({ status, run, strip }: { status: ResearchSessionResponse; run: RunState; strip: ReactNode }) {
  const hard = status.errors.find((e) => !e.recoverable) ?? API_FAILURE;
  const reason = typeof hard.details.reason === "string" ? hard.details.reason : null;
  const exceptionType = typeof hard.details.exception_type === "string" ? hard.details.exception_type : null;
  const haltIndex = run.openNode ? AGENT_ORDER.indexOf(run.openNode) : -1;
  return (
    <section className="stage is-on" id="stage-failed" aria-labelledby="failed-h">
      <div className="report-head">
        <div style={{ minWidth: 0 }}>
          <p className="cap" id="failedMeta">session {status.session_id} · finished {fmtClock(status.finished_at) ?? "not recorded"}</p>
          {/* Controller ruling 1: same q-center pattern as #report-h (Task 17) — the prototype's
              #failed-h markup (index.html:1517) and its matching CSS (.report-q.q-center, :372)
              both key off the base class report-q, not ask-q/ask-locked. */}
          <h1 className={"report-q" + qFitClass(status.query)} id="failed-h">{status.query}</h1>
          {strip}
        </div>
        <div className="row wrap">
          {status.trace_url ? <a className="btn btn-ghost" href={status.trace_url} target="_blank" rel="noopener" id="failedTrace">Open LangSmith trace <span aria-hidden="true">↗</span></a> : null}
        </div>
      </div>
      <div className="with-rail">
        <div className="stack" style={{ gap: "var(--space-5)" }}>
          <div className="note bad">
            {/* The headline is the halting type in plain words; the enumerated type stays in the facts; the API's message is the sentence. */}
            <div className="note-head"><span className="mk">halt</span><span id="failedType">{HALT_HEADLINES[hard.error_type] ?? hard.error_type}</span></div>
            <p id="failedMessage">{hard.message || "The run stopped on a non-recoverable error."}</p>
          </div>
          <div className="card stack" style={{ gap: "var(--space-4)" }}>
            <h2 className="card-title">Why nothing was published</h2>
            <p className="sm">A halted run skips publication: no report, evidence log or quality record was written.</p>
            <dl className="kv">
              <dt>error_type</dt><dd id="failFactType">{hard.error_type}</dd>
              {/* K19: no invented values — the source row is omitted rather than showing a made-up "graph". */}
              {hard.source ? <><dt>source</dt><dd id="failFactSource">{hard.source}</dd></> : null}
              <dt>recoverable</dt><dd id="failFactRecoverable">{String(Boolean(hard.recoverable))}</dd>
              {reason ? <><dt>reason</dt><dd id="failFactReason">{reason}</dd></> : null}
              {exceptionType ? <><dt>exception_type</dt><dd id="failFactException">{exceptionType}</dd></> : null}
              <dt>report_path</dt><dd className="avail">Not published</dd>
              <dt>GET /report</dt><dd>409 report_unavailable</dd>
            </dl>
            <p className="avail">A finished session with no artifact answers <span className="mono">409 report_unavailable</span> — distinct from the <span className="mono">409 session_not_complete</span> a running session returns, and from the <span className="mono">404</span> an unknown id returns. There is no download control here, disabled or otherwise.</p>
          </div>
        </div>
        <aside className="rail" aria-label="Run details">
          <div className="card stack" style={{ gap: "var(--space-4)" }}>
            <div className="row-between">
              <h2 className="card-title">Where it stopped</h2>
              <span className="avail-mono" id="failedHaltedAt">{haltIndex >= 0 ? `halted at stage ${haltIndex + 1}` : "halted before the first stage"}</span>
            </div>
            <Spine marks={failedMarks(run, status.status)} run={run} withArcs={false} id="spineFailed" />
          </div>
          <div className="card stack-2">
            <h2 className="card-title">What survived the halt</h2>
            <Counters counters={run.counters} absentText="not reached" pass={run.countersPass} id="failedCounters" />
            <p className="avail">The same counters the running stage keeps, frozen at the halt. A counter whose node never ran reads <span className="mono">not reached</span>: a value never measured, not a zero.</p>
          </div>
        </aside>
      </div>
    </section>
  );
}
