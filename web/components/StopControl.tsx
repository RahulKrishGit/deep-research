"use client";
import { useEffect, useRef, useState } from "react";
import { ApiError, stopResearch } from "@/lib/api";
import { STOP_BODY, STOP_CLOSE, STOP_CONFIRM, STOP_FAILED, STOP_KEEP, STOP_LABEL, STOP_TITLE, STOP_TOO_LATE } from "@/lib/stop";
import { useConsole, type StopTarget } from "./ConsoleProvider";

/* notes-progress-report spec §8.5 (D17, D18, D24; Stop.dc.html columns 1-2): Stop, after the running
   status chip — ghost, small, a square in the text colour — and its one confirmation. "Keep going" takes
   focus on open; Escape, a click outside or "Keep going" closes it and gives focus back to Stop. "Stop
   research" posts once: a 202 hands the stopped session to the screen, a 409 means the run is already
   finishing, and any other failure keeps the question open. Nothing stops until the reader says so. */
type Face = "ask" | "busy" | "late" | "failed";

export function StopControl({ target }: { target: StopTarget }) {
  const { noteMode, refreshSessions } = useConsole();
  const [open, setOpen] = useState(false);
  const [face, setFace] = useState<Face>("ask");
  const anchor = useRef<HTMLSpanElement>(null);
  const stopBtn = useRef<HTMLButtonElement>(null);
  const keepBtn = useRef<HTMLButtonElement>(null);
  const confirmBtn = useRef<HTMLButtonElement>(null);
  const closeBtn = useRef<HTMLButtonElement>(null);
  const busy = face === "busy";

  function close() { setOpen(false); setFace("ask"); stopBtn.current?.focus(); }
  // Focus follows the face: Keep going on open, Close after a 409, Stop research after a failure.
  useEffect(() => {
    if (!open) return;
    if (face === "ask") keepBtn.current?.focus();
    else if (face === "late") closeBtn.current?.focus();
    else if (face === "failed") confirmBtn.current?.focus();
  }, [open, face]);
  // While the POST is in flight neither Escape nor a click outside closes it, so the answer always
  // lands on an open popover (spec ambiguity 10).
  useEffect(() => {
    if (!open || busy) return;
    const onKeyDown = (event: KeyboardEvent) => { if (event.key === "Escape") close(); };
    // A press outside closes it. Its default action would move focus after this handler — to what was
    // pressed, or to the page when that cannot take focus — so it is cancelled, and focus stays on Stop.
    const onMouseDown = (event: MouseEvent) => {
      if (anchor.current && !anchor.current.contains(event.target as Node)) { event.preventDefault(); close(); }
    };
    document.addEventListener("keydown", onKeyDown);
    document.addEventListener("mousedown", onMouseDown);
    return () => { document.removeEventListener("keydown", onKeyDown); document.removeEventListener("mousedown", onMouseDown); };
  }, [open, busy]);

  async function confirm() {
    setFace("busy");
    try {
      const result = await stopResearch(target.sessionId); // one POST; never retried
      noteMode(result.mode);
      setOpen(false); setFace("ask");
      target.onStopped(result.data);
      void refreshSessions();
    } catch (error) {
      setFace(error instanceof ApiError && error.status === 409 ? "late" : "failed");
    }
  }

  return (
    <span className="stop-anchor" ref={anchor}>
      <button className="btn btn-ghost btn-sm btn-stop" id="stopBtn" type="button" ref={stopBtn}
        aria-haspopup="dialog" aria-expanded={open} aria-controls={open ? "stopConfirm" : undefined}
        onClick={() => { if (!open) setOpen(true); else if (!busy) close(); }}>
        <span className="stop-sq" aria-hidden="true" />{STOP_LABEL}
      </button>
      {open ? (
        <div className="stop-confirm" id="stopConfirm" role="dialog" aria-labelledby="stopConfirmT">
          <p className="confirm-t" id="stopConfirmT">{STOP_TITLE}</p>
          {face === "late" ? (
            <>
              <p className="b-sub" id="stopTooLate">{STOP_TOO_LATE}</p>
              <div className="confirm-btns">
                <button className="btn btn-quiet btn-sm" type="button" ref={closeBtn} onClick={close}>{STOP_CLOSE}</button>
              </div>
            </>
          ) : (
            <>
              <p className="b-sub">{STOP_BODY}</p>
              {face === "failed" ? <p className="cap" id="stopFailed" role="alert">{STOP_FAILED}</p> : null}
              <div className="confirm-btns">
                <button className="btn btn-quiet btn-sm" type="button" ref={keepBtn} disabled={busy} onClick={close}>{STOP_KEEP}</button>
                <button className="btn btn-sm btn-danger" type="button" ref={confirmBtn} disabled={busy} onClick={() => void confirm()}>{STOP_CONFIRM}</button>
              </div>
            </>
          )}
        </div>
      ) : null}
    </span>
  );
}
