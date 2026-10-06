"use client";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { startResearch, type ResearchSessionResponse } from "@/lib/api";
import { qFitClass } from "@/lib/format";
import { STAGES, marksFor, type NodeId, type RunState } from "@/lib/run-state";
import { recordSubmission, type SubmittedSettings } from "@/lib/session-store";
import { ASK_AGAIN, ASK_AGAIN_FAILED, STOPPED_EYEBROW, STOPPED_KEPT, secondsBetween, stoppedLine } from "@/lib/stop";
import { BriefSpine } from "./BriefSpine";
import { DEFAULT_SETTINGS, buildRequest } from "./Composer";
import { useConsole } from "./ConsoleProvider";

/* A session the reader stopped. The
   question and its settings; one short note — when, how far in, that no report was written — with "Ask
   again" (the same question, a new session); then the pipeline frozen at the stopped row. A stop during
   the one-time check shows no pipeline card. The service-stopped stage (#stage-stopped,
   SessionScreen.tsx) is a different stage: the service shut down, the reader did not stop anything. */
export function UserStoppedStage({ status, run, strip, settings, onToggleRow }: {
  status: ResearchSessionResponse; run: RunState; strip: ReactNode; settings: SubmittedSettings | null; onToggleRow(id: NodeId): void;
}) {
  const router = useRouter();
  const { noteMode, refreshSessions } = useConsole();
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const line = useRef<HTMLParagraphElement>(null);
  // The response says the step; the stream's session.stopped says when and how long, and until it has
  // arrived the response's own times stand in.
  const step = status.stopped_step ?? run.stopped?.step ?? null;
  const at = run.stopped?.at ?? status.finished_at;
  const seconds = run.stopped?.elapsedSeconds ?? secondsBetween(status.started_at, status.finished_at);
  const frozen = STAGES.find((s) => s.id === step)?.id ?? null;
  // The Stop control that had focus went with the running stage: the note's first line takes it, so a
  // keyboard or screen-reader user keeps their place. It is not a control, so it draws no ring. When the
  // stream's end and /status show the stop before the POST's 202 lands, this stage mounts while the
  // control is still up and still holds focus (a disabled "Stop research"), and unmounts a moment
  // later: focus inside it counts as lost too.
  useEffect(() => {
    const held = document.activeElement;
    if (held === document.body || held?.closest(".stop-anchor")) line.current?.focus();
  }, []);

  async function askAgain() {
    if (busy) return;
    setBusy(true); setFailed(false);
    const chosen = settings ?? DEFAULT_SETTINGS;
    try {
      const result = await startResearch(buildRequest(status.query, chosen)); // one POST; never retried
      noteMode(result.mode);
      recordSubmission(result.data.session_id, chosen);
      void refreshSessions();
      router.push(`/research/${result.data.session_id}`);
    } catch {
      setFailed(true);
      setBusy(false);
    }
  }

  return (
    <section className="stage is-on" id="stage-user-stopped" aria-labelledby="user-stopped-h">
      <div className="run-wrap">
        <div className="ask-head">
          <p className="eyebrow" style={{ margin: 0 }}>{STOPPED_EYEBROW}</p>
          <h1 className={"ask-q ask-locked" + qFitClass(status.query)} id="user-stopped-h">{status.query}</h1>
          {strip}
        </div>
        <div className="stopped-note" role="status">
          <p className="b-now" id="stoppedLine" tabIndex={-1} ref={line}>{stoppedLine(step, at, seconds)}</p>
          <p className="b-sub">{STOPPED_KEPT}</p>
          <div className="stop-actions">
            <button className="btn btn-ghost btn-sm" id="askAgain" type="button" disabled={busy} onClick={() => void askAgain()}>{ASK_AGAIN}</button>
            {failed ? <span className="cap" id="askAgainFailed" role="alert">{ASK_AGAIN_FAILED}</span> : null}
          </div>
        </div>
        {frozen !== null ? (
          <div className="card stack" style={{ gap: "var(--space-5)" }}>
            {/* No row is active on a stopped session. Until the stream's session.stopped clears it, the
                stream's last active row is the stopped one, and its brief would acknowledge notes. */}
            <BriefSpine marks={marksFor(run, null)} run={{ ...run, active: null }} onToggle={onToggleRow} frozen={frozen} />
          </div>
        ) : null}
      </div>
    </section>
  );
}
