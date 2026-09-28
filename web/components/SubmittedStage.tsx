"use client";
import type { ReactNode } from "react";
export function SubmittedStage({ question, strip }: { question: string; strip: ReactNode }) {
  return (
    <section className="stage is-on" id="stage-submitted" aria-labelledby="submitted-h">
      <div className="run-wrap">
        <div className="ask-head">
          <p className="eyebrow" style={{ margin: 0 }}>Question locked in</p>
          <h1 className="ask-q ask-locked" id="submitted-h" aria-describedby="submittedOpts">{question}</h1>
          {strip}
        </div>
      </div>
    </section>
  );
}
