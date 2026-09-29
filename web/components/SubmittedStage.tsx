"use client";
import { useLayoutEffect, useState, type ReactNode } from "react";
import { qFitClass } from "@/lib/format";
import { clearIdleToRunningFlight, motionMs, runIdleToRunningLift, takeIdleToRunningFlight } from "@/lib/handoff";

/* Beats two and three of the idle→running lift land here (DESIGN.md:1378-1406). The section
   starts held at opacity 0 (is-preparing, index.html:224-225) so #submitted-h can be measured
   while laid out but unseen; runIdleToRunningLift drives the (already-created, cross-route) box
   from the composer's frame to this one, and onLanded flips is-preparing off and is-revealing on,
   fading the record in underneath the dissolving box (index.html:226-229). no-enter
   (index.html:2440, :3797, showStage's own `enter:false`) is what keeps the section's generic
   6px entrance from adding its own offset on top of the box's landing geometry.
   No pending flight (a reload mid-beat, or reduced motion never created a box) still runs the
   same prepare→reveal fade, on the same --motion-clear timing, matching holdBeat's "no travel"
   branch (index.html:2100-2105): the record simply appears rather than being carried to.
   Review fix round 1 (Important #1): the flight branch returns a cleanup that clears the box —
   a sidebar click or a second submit mid-lift must not leave it flying over whatever page comes
   next; SessionScreen's own cleanups cover every other way this stage can be abandoned. */
export function SubmittedStage({ sessionId, question, strip }: { sessionId: string; question: string; strip: ReactNode }) {
  const [preparing, setPreparing] = useState(true);
  const [revealing, setRevealing] = useState(false);
  useLayoutEffect(() => {
    const onLanded = () => { setPreparing(false); setRevealing(true); };
    const flight = takeIdleToRunningFlight(sessionId);
    const target = document.getElementById("submitted-h");
    if (flight && target) {
      runIdleToRunningLift(flight, target, onLanded);
      return () => clearIdleToRunningFlight();
    }
    const t = setTimeout(onLanded, motionMs("--motion-clear", 320));
    return () => clearTimeout(t);
  }, [sessionId]);
  const cls = "stage is-on no-enter" + (preparing ? " is-preparing" : "") + (revealing ? " is-revealing" : "");
  return (
    <section className={cls} id="stage-submitted" aria-labelledby="submitted-h">
      <div className="run-wrap">
        <div className="ask-head">
          <p className="eyebrow" style={{ margin: 0 }}>Question locked in</p>
          <h1 className={"ask-q ask-locked" + qFitClass(question)} id="submitted-h" aria-describedby="submittedOpts">{question}</h1>
          {strip}
        </div>
      </div>
    </section>
  );
}
