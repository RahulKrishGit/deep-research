"use client";
import { useCallback, useEffect, useReducer, useRef, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { ApiError, ApiUnreachableError, getStatus, streamUrl, type ResearchSessionResponse } from "@/lib/api";
import { toSessionView, type SessionView } from "@/lib/format";
import { applyEvent, marksFor, newRunState, toRunEvent, type RunState } from "@/lib/run-state";
import { readSubmission, submittedBeatRemaining, type Submission } from "@/lib/session-store";
import { backoffDelaysMs, readStream } from "@/lib/stream";
import { useConsole } from "./ConsoleProvider";
import { Counters } from "./Counters";
import { RunningPipeline } from "./RunningPipeline";
import { SessionNotFound } from "./SessionNotFound";
import { SettingsStrip } from "./SettingsStrip";
import { Spine } from "./Spine";
import { SubmittedStage } from "./SubmittedStage";

/* The stage is derived from /status and the stream (spec §4.3 stage table):
   404 → not in memory · running+finished_at → service stopped · running → Submitted (this tab, < 2.2 s) then Running ·
   failed → Failed (Task 18) · other terminal → Report (Task 17). */
export function SessionScreen({ sessionId }: { sessionId: string }) {
  const router = useRouter();
  const { noteMode, noteUnreachable, clearUnreachable, setChip } = useConsole();
  const [status, setStatus] = useState<ResearchSessionResponse | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [submission, setSubmission] = useState<Submission | null>(null);
  const [beat, setBeat] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const run = useRef<RunState>(newRunState(null));
  const ceilingFromStream = useRef<number | null>(null);
  const wake = useRef<(() => void) | null>(null);
  const [version, bump] = useReducer((n: number) => n + 1, 0);

  // Facts only this tab has (sessionStorage): read after mount so the server render never disagrees.
  useEffect(() => {
    const held = readSubmission(sessionId);
    setSubmission(held);
    const remaining = submittedBeatRemaining(sessionId);
    if (remaining > 0) { setBeat(true); const t = setTimeout(() => setBeat(false), remaining); return () => clearTimeout(t); }
  }, [sessionId]);

  const load = useCallback(async (): Promise<ResearchSessionResponse | null> => {
    try {
      const result = await getStatus(sessionId);
      noteMode(result.mode);
      setStatus(result.data);
      clearUnreachable();
      return result.data;
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) setNotFound(true);
      else if (error instanceof ApiUnreachableError) noteUnreachable(error.target, () => { wake.current?.(); void load(); });
      return null;
    }
  }, [sessionId, noteMode, clearUnreachable, noteUnreachable]);
  useEffect(() => { void load(); }, [load]);

  const ceiling = useCallback(() => ceilingFromStream.current ?? (submission ? submission.settings.extraPasses + 1 : null), [submission]);

  // The stream: opened for every known session (a finished one replays and closes); reconnect on
  // the ladder while the session is still running with no finished_at. K7: a service-stopped
  // session (running with finished_at set) also streams once — the server still replays its
  // recorded events from index 0 even though status stays "running" — but the post-stream check
  // below reads finished_at and returns without ever scheduling another attempt for it.
  const ready = status !== null && !notFound;
  useEffect(() => {
    if (!ready) return;
    const controller = new AbortController();
    let cancelled = false;
    const delays = backoffDelaysMs();
    const sleep = (ms: number) => new Promise<void>((resolve) => { const t = setTimeout(() => { wake.current = null; resolve(); }, ms); wake.current = () => { clearTimeout(t); wake.current = null; resolve(); }; });
    (async () => {
      while (!cancelled) {
        run.current = newRunState(ceiling());
        bump();
        const end = await readStream(streamUrl(sessionId), {
          onOpen: (mode) => { noteMode(mode); clearUnreachable(); setStreaming(true); },
          onEvent: (event) => {
            if (event.event_type === "graph.session.started" && typeof event.metadata.max_extra_passes === "number") ceilingFromStream.current = (event.metadata.max_extra_passes as number) + 1;
            applyEvent(run.current, toRunEvent(event));
            bump();
          },
        }, controller.signal);
        setStreaming(false);
        if (cancelled) return;
        const latest = await load();
        if (latest === null && end.kind === "failed") { /* unreachable or 404: the banner or the not-found state is up */ }
        if (latest && (latest.status !== "running" || latest.finished_at !== null)) return;
        if (notFound) return;
        await sleep(delays.next().value);
      }
    })();
    return () => { cancelled = true; controller.abort(); wake.current = null; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, ready]);

  // The topbar chip follows the run while streaming and /status afterwards.
  const passes = ceiling();
  const view: SessionView | null = status ? toSessionView(status, passes) : null;
  if (view && status?.status === "running" && streaming) { view.iteration = run.current.pass - 1; view.passes = run.current.maxPasses; }
  useEffect(() => { setChip(view); return () => setChip(null); }, [setChip, status, version, streaming, passes]); // eslint-disable-line react-hooks/exhaustive-deps

  if (notFound) return <SessionNotFound onNew={() => router.push("/")} />;
  if (!status) return <section className="stage is-on" id="stage-loading"><div className="run-wrap"><p className="avail">loading session</p></div></section>;
  const strip = <SettingsStrip settings={submission?.settings ?? null} ceiling={passes} id="runningOpts" />;
  if (status.status === "running" && status.finished_at !== null) return <StoppedStage status={status} run={run.current} strip={strip} onNew={() => router.push("/")} />;
  if (status.status === "running") {
    if (beat) return <SubmittedStage question={status.query} strip={strip} />;
    return <RunningPipeline run={run.current} question={status.query} strip={strip} startedAt={status.started_at} ceiling={passes} />;
  }
  return <FinishedHeader status={status} run={run.current} strip={strip} />;
}

/* S4: the service stopped while the run was in progress (running + finished_at). K7: the pipeline
   the run actually reached stays on screen, frozen — the same Spine the Running stage paints, just
   with no more events left to update it (no reconnect for this status). */
function StoppedStage({ status, run, strip, onNew }: { status: ResearchSessionResponse; run: RunState; strip: ReactNode; onNew: () => void }) {
  return (
    <section className="stage is-on" id="stage-stopped" aria-labelledby="stopped-h">
      <div className="run-wrap">
        <div className="ask-head">
          <p className="eyebrow" style={{ margin: 0 }}>Session interrupted</p>
          <h1 className="ask-q ask-locked" id="stopped-h">{status.query}</h1>
          {strip}
        </div>
        <div className="note bad" role="status">
          <div className="note-head"><span className="mk">stopped</span><span>The service stopped while this run was in progress. Nothing was published.</span></div>
          <button className="btn btn-primary" type="button" onClick={onNew}>New research</button>
        </div>
        <div className="card stack" style={{ gap: "var(--space-5)" }}>
          <Spine marks={marksFor(run, run.active)} run={run} withArcs />
          <Counters counters={run.counters} absentText="not reached" pass={run.countersPass} />
        </div>
      </div>
    </section>
  );
}

/* A finished session's header and frozen counters; Task 17 renders the Report stage and Task 18 the Failed stage here. */
function FinishedHeader({ status, run, strip }: { status: ResearchSessionResponse; run: RunState; strip: ReactNode }) {
  return (
    <section className="stage is-on" id="stage-finished" aria-labelledby="finished-h">
      <div className="run-wrap">
        <div className="ask-head">
          <p className="eyebrow" style={{ margin: 0 }}>Session finished</p>
          <h1 className="ask-q ask-locked" id="finished-h">{status.query}</h1>
          {strip}
        </div>
        <div className="card stack" style={{ gap: "var(--space-5)" }}>
          <Counters counters={run.counters} absentText="not reached" pass={run.countersPass} />
        </div>
      </div>
    </section>
  );
}
