"use client";
export function SessionNotFound({ onNew }: { onNew: () => void }) {
  return (
    <section className="stage is-on" id="stage-not-found" aria-labelledby="not-found-h">
      <div className="run-wrap">
        <div className="note bad" role="status">
          <div className="note-head"><span className="mk">gone</span><span id="not-found-h">This session isn't in the service's memory — sessions are lost when the API restarts.</span></div>
          <button className="btn btn-primary" type="button" onClick={onNew}>New research</button>
        </div>
      </div>
    </section>
  );
}
