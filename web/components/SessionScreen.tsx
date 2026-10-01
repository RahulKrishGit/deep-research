"use client";
import { useCallback, useEffect, useReducer, useRef, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { ApiError, ApiUnreachableError, getStatus, streamUrl, type ResearchSessionResponse } from "@/lib/api";
import { checkPhase } from "@/lib/clarify";
import { isLive, qFitClass, toSessionView, type SessionView } from "@/lib/format";
import { cancelDeferredClearIdleToRunningFlight, clearIdleToRunningFlight, clearRunningLayout, deferClearIdleToRunningFlight } from "@/lib/handoff";
import { applyEvent, chipStep, marksFor, newRunState, stepLabel, toRunEvent, toggleOpen, type NodeId, type RunState } from "@/lib/run-state";
import { readSubmission, submittedBeatRemaining, type Submission } from "@/lib/session-store";
import { stoppedStepLabel } from "@/lib/stop";
import { backoffDelaysMs, readStream } from "@/lib/stream";
import { ClarifyStage } from "./ClarifyStage";
import { useConsole } from "./ConsoleProvider";
import { Counters } from "./Counters";
import { FailedStage } from "./FailedStage";
import { ReportStage } from "./ReportStage";
import { RunningPipeline } from "./RunningPipeline";
import { SessionNotFound } from "./SessionNotFound";
import { SettingsStrip } from "./SettingsStrip";
import { Spine } from "./Spine";
import { SubmittedStage } from "./SubmittedStage";
import { UserStoppedStage } from "./UserStoppedStage";

/* The stage is derived from /status and the stream (spec §4.3 stage table):
   404 → not in memory · running+finished_at → service stopped · running or needs_input → Submitted (this tab, < 2.2 s),
   then the one-time check while it asks and until the planner starts (live-briefs spec §4.5), then Running ·
   stopped → the stopped stage (notes-progress-report spec §8.5) · failed → Failed (Task 18) ·
   other terminal → Report (Task 17). */
export function SessionScreen({ sessionId }: { sessionId: string }) {
  const router = useRouter();
  const { noteMode, noteUnreachable, clearUnreachable, refreshSessions, setChip, setStop } = useConsole();
  const [status, setStatus] = useState<ResearchSessionResponse | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [submission, setSubmission] = useState<Submission | null>(null);
  const [beat, setBeat] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const run = useRef<RunState>(newRunState());
  const wake = useRef<(() => void) | null>(null);
  const delaysRef = useRef(backoffDelaysMs());
  const [version, bump] = useReducer((n: number) => n + 1, 0);
  // A done row reopened or closed by the reader (live-briefs spec §4.3): reader state on the run.
  const toggleRow = useCallback((id: NodeId) => { toggleOpen(run.current, id); bump(); }, []);

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
      clearUnreachable("session");
      return result.data;
    } catch (error) {
      // C1: a 404 means the session truly is not in memory — SessionNotFound's own sentence is
      // the answer, never an "unreachable service" banner, so this tab's own key clears too.
      if (error instanceof ApiError && error.status === 404) { setNotFound(true); clearUnreachable("session"); }
      // Retry (spec line 522) forces an attempt immediately and resets the ladder back to 1 s.
      else if (error instanceof ApiUnreachableError) noteUnreachable("session", error.target, () => { delaysRef.current = backoffDelaysMs(); wake.current?.(); void load(); });
      return null;
    }
  }, [sessionId, noteMode, clearUnreachable, noteUnreachable]);
  useEffect(() => { void load(); }, [load]);
  // NB1: a session key stuck on ApiUnreachableError (e.g. the page is navigated away from mid
  // outage) would otherwise stay registered forever — nothing left to run its retry, so the
  // banner would sit up until an unrelated key happened to clear.
  useEffect(() => () => clearUnreachable("session"), [clearUnreachable]);

  // C1: a page loaded while the API is down never gets a first `/status` — the ladder below used
  // to run only once a status had already arrived, so a dead-on-arrival page sat on "loading
  // session" behind the banner forever with no way out but a manual Retry click. This retries
  // `load()` on the same ladder while no attempt has ever succeeded (or answered 404) yet.
  useEffect(() => {
    if (status !== null || notFound) return;
    let cancelled = false;
    const sleep = (ms: number) => new Promise<void>((resolve) => { const t = setTimeout(() => { wake.current = null; resolve(); }, ms); wake.current = () => { clearTimeout(t); wake.current = null; resolve(); }; });
    (async () => {
      while (!cancelled) {
        await sleep(delaysRef.current.next().value);
        if (cancelled) return;
        await load();
      }
    })();
    return () => { cancelled = true; wake.current?.(); };
  }, [status, notFound, load]);

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
    delaysRef.current = backoffDelaysMs();
    let streamed = false; // this effect run (this session) has already opened the stream once
    const sleep = (ms: number) => new Promise<void>((resolve) => { const t = setTimeout(() => { wake.current = null; resolve(); }, ms); wake.current = () => { clearTimeout(t); wake.current = null; resolve(); }; });
    (async () => {
      while (!cancelled) {
        await readStream(streamUrl(sessionId), {
          // C2: the fresh RunState is swapped in only once the connection actually opens — never
          // before the fetch — so a reconnect attempt that fails during an outage leaves whatever
          // stage last rendered on screen under the banner, instead of resetting to a fake
          // "Planning" pipeline the run never actually re-entered. I1: the ladder also resets here
          // (not only once at effect start), so a long-lived run's periodic ~300 s idle drop keeps
          // reconnecting after 1 s, never inheriting a stale 30 s cadence from an earlier gap.
          // Live-briefs final review M2: the rows the reader reopened are reader state, not stream
          // state, so they ride across the reset (only on a reconnect of this same session). A row
          // that is not finished never renders open, and a loop's re-arm during the replay drops the
          // rows it re-arms, exactly as it did the first time.
          onOpen: (mode) => {
            noteMode(mode); clearUnreachable("session"); delaysRef.current = backoffDelaysMs();
            const reopened = streamed ? run.current.open : new Set<NodeId>();
            streamed = true;
            run.current = newRunState();
            run.current.open = reopened;
            bump(); setStreaming(true);
          },
          onEvent: (event) => {
            applyEvent(run.current, toRunEvent(event));
            bump();
          },
        }, controller.signal);
        if (cancelled) return;
        // M3: `load()` (a real GET) settles before the chip is allowed to fall back to streaming's
        // idea of the run — otherwise a one-round-trip window shows the `/status` fetched at mount.
        const latest = await load();
        setStreaming(false);
        if (latest && (!isLive(latest.status) || latest.finished_at !== null)) {
          // Final-wave item 2: the sidebar only polls every 5 s while some session is running;
          // refresh it the moment this tab proves the run it's watching just finished, so the
          // running ring doesn't linger on a stopped run for the rest of that window.
          void refreshSessions();
          return;
        }
        await sleep(delaysRef.current.next().value);
      }
    })();
    return () => { cancelled = true; controller.abort(); wake.current?.(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, ready]);

  // The topbar chip follows the run while streaming and /status afterwards. K7/M3: a service-stopped
  // session shows no chip at all — the sentence above the frozen pipeline already says what
  // happened, and the green "Running" chip would contradict it. Re-review: a mid-run API restart
  // can leave a stale "running" `status` on screen while the ladder's next /status lands a 404 —
  // `notFound` must blank the chip too, or the not-in-memory page keeps showing "Running · pass…"
  // over a session the page itself just said isn't in memory.
  // live-briefs spec §4.4: a session waiting for the reader (needs_input) is as live as a running one.
  const live = status !== null && isLive(status.status);
  const stopped = live && status.finished_at !== null;
  // live-briefs spec §4.5: "asking" while the check waits for the reader, "starting" from its answers
  // until the planner starts, null otherwise (lib/clarify.ts checkPhase).
  const phase = live ? checkPhase(run.current, status.status) : null;
  // live-briefs spec §4.2: "Running · {active step label}" — the stream's active row while it is
  // open (chipStep), the status snapshot's current_agent otherwise; no pass number anywhere. While
  // the check asks, the chip reads "Waiting for you · a few quick questions": the stream is newer
  // than the last /status, so the phase, not the snapshot, picks the chip's status.
  // notes-progress-report spec §8.4: a session the reader stopped names the step it was stopped at.
  const step = live && phase !== "asking" ? stepLabel((streaming ? chipStep(run.current) : null) ?? status.current_agent)
    : status?.status === "stopped" ? stoppedStepLabel(status.stopped_step) : null;
  const view: SessionView | null = status ? toSessionView(live ? { ...status, status: phase === "asking" ? "needs_input" : "running" } : status, step) : null;
  useEffect(() => { setChip(stopped || notFound ? null : view); return () => setChip(null); }, [setChip, status, version, streaming, stopped, notFound]); // eslint-disable-line react-hooks/exhaustive-deps
  // notes-progress-report spec §8.5 (D17): Stop is offered from the one-time check through Reviewing —
  // while the session is live and not closed out, and neither the stream nor the last /status shows
  // Publishing or a finished graph: the API refuses a stop from the run's decision to publish.
  const stoppable = live && status.finished_at === null && run.current.finalStatus === null
    && chipStep(run.current) !== "finalize_report" && status.current_agent !== "finalize_report";
  useEffect(() => {
    setStop(stoppable ? { sessionId, onStopped: setStatus } : null);
    return () => setStop(null);
  }, [setStop, stoppable, sessionId]);

  // Review fix round 1 (Important #1): a pending idle→running flight is only ever consumed by
  // SubmittedStage. Every other resolution — not found, a stopped/failed/finished session, or the
  // beat already having expired by the time status arrives — must drop it here, or the box is
  // stranded (fixed, opaque, at the composer's old position) over whatever renders instead.
  useEffect(() => {
    if (notFound) { clearIdleToRunningFlight(); return; }
    if (!status) return; // still loading: give the pending flight a chance to reach SubmittedStage
    if (stopped) { clearIdleToRunningFlight(); return; }
    if (live && beat) return; // this is the SubmittedStage branch
    clearIdleToRunningFlight();
  }, [notFound, status, stopped, beat]);
  // Abandoning this page (a sidebar click, New Research) mid-flight must not leave the box
  // animating over whatever the operator navigates to next. Deferred (fix round 2): React
  // StrictMode (next dev) mounts, cleans up and remounts this effect synchronously on first
  // mount, so an immediate clear here would kill a flight that had only just landed — deferring
  // it and letting the remount's own body cancel it (below) survives that, while a real unmount
  // or a genuine sessionId change still clears, since nothing cancels it there.
  useEffect(() => {
    cancelDeferredClearIdleToRunningFlight(sessionId);
    return () => deferClearIdleToRunningFlight(sessionId);
  }, [sessionId]);
  // Review fix round 1 (Important #2): a running-stage layout this tab noted (for this session or
  // an earlier one it watched run) must not survive past this page — otherwise a later visit to
  // this same session's now-finished report, reached without passing through RunningPipeline
  // again (e.g. a sidebar click), can consume a stale rect and slide when it should not.
  useEffect(() => () => clearRunningLayout(), [sessionId]);

  if (notFound) return <SessionNotFound onNew={() => router.push("/")} />;
  if (!status) return <section className="stage is-on" id="stage-loading"><div className="run-wrap"><p className="avail">loading session</p></div></section>;
  const strip = <SettingsStrip settings={submission?.settings ?? null} id="runningOpts" />;
  if (stopped) return <StoppedStage status={status} run={run.current} strip={strip} onNew={() => router.push("/")} />;
  if (live) {
    if (beat) return <SubmittedStage sessionId={sessionId} question={status.query} strip={strip} />;
    // live-briefs spec §4.5: the check takes the pipeline card's place until the planner starts.
    if (phase !== null) return <ClarifyStage sessionId={sessionId} run={run.current} phase={phase} question={status.query} strip={strip} />;
    return <RunningPipeline sessionId={sessionId} run={run.current} question={status.query} strip={strip} startedAt={status.started_at} onToggleRow={toggleRow} notesRemaining={status.notes_remaining} />;
  }
  if (status.status === "stopped") return <UserStoppedStage status={status} run={run.current} strip={strip} settings={submission?.settings ?? null} onToggleRow={toggleRow} />;
  if (status.status === "failed") return <FailedStage status={status} run={run.current} strip={strip} />;
  return <ReportStage sessionId={sessionId} status={status} strip={<SettingsStrip settings={submission?.settings ?? null} id="reportOpts" />} />;
}

/* S4: the service stopped while the run was in progress (running + finished_at). K7: the pipeline
   the run actually reached stays on screen, frozen — the same Spine the Running stage paints, just
   with no more events left to update it (no reconnect for this status), and no topbar chip (M3). */
function StoppedStage({ status, run, strip, onNew }: { status: ResearchSessionResponse; run: RunState; strip: ReactNode; onNew: () => void }) {
  return (
    <section className="stage is-on" id="stage-stopped" aria-labelledby="stopped-h">
      <div className="run-wrap">
        <div className="ask-head">
          <p className="eyebrow" style={{ margin: 0 }}>Session interrupted</p>
          <h1 className={"ask-q ask-locked" + qFitClass(status.query)} id="stopped-h">{status.query}</h1>
          {strip}
        </div>
        <div className="note bad" role="status">
          <div className="note-head"><span className="mk">stopped</span><span>The service stopped while this run was in progress. Nothing was published.</span></div>
          <button className="btn btn-primary" type="button" onClick={onNew}>New research</button>
        </div>
        <div className="card stack" style={{ gap: "var(--space-5)" }}>
          {/* M6: the halting row is the node that actually started and never completed — never the
              derived "next" row, which may not have started at all. Same convention as failedMarks. */}
          <Spine marks={marksFor(run, run.openNode)} run={run} withArcs />
          <Counters counters={run.counters} absentText="not reached" pass={run.countersPass} />
        </div>
      </div>
    </section>
  );
}

