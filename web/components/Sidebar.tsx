"use client";
import { Fragment } from "react";
import { useParams, useRouter } from "next/navigation";
import type { ResearchSessionResponse } from "@/lib/api";
import { isLive } from "@/lib/format";
import { useConsole } from "./ConsoleProvider";

/* Today · Yesterday · Earlier by the local date of started_at; the API's order (newest first) is kept. */
export function groupByDay(sessions: ResearchSessionResponse[], now = new Date()): [string, ResearchSessionResponse[]][] {
  const day = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const today = day(now);
  const yesterday = today - 86_400_000;
  const groups = new Map<string, ResearchSessionResponse[]>();
  for (const s of sessions) {
    const started = day(new Date(s.started_at));
    const label = started >= today ? "Today" : started >= yesterday ? "Yesterday" : "Earlier";
    groups.set(label, [...(groups.get(label) ?? []), s]);
  }
  return ["Today", "Yesterday", "Earlier"].filter((g) => groups.has(g)).map((g) => [g, groups.get(g)!]);
}

export function Sidebar() {
  const { sessions, sessionsLoaded, setSidebar } = useConsole();
  const router = useRouter();
  const params = useParams();
  const active = typeof params?.id === "string" ? params.id : null;
  /* index.html:3782,:3838 — choosing a session or starting over collapses the drawer;
     on desktop "expanded" is a pinned state, so navigation never touches it there. */
  const navigate = (path: string) => {
    router.push(path);
    if (window.matchMedia("(max-width:1080px)").matches) setSidebar("collapsed");
  };
  return (
    <aside className="sidebar" id="sidebar" aria-label="Session history">
      <div className="sb-head">
        <span className="mark" aria-hidden="true"></span>
        <span className="sb-wordmark">Deep Research</span>
      </div>
      <button className="btn btn-ghost sb-new" id="newResearch" type="button" onClick={() => navigate("/")}>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="square" aria-hidden="true" style={{ width: 14, height: 14, flex: "none" }}><path d="M12 5v14M5 12h14" /></svg>
        <span>New Research</span>
      </button>
      <div className="sb-label"><span>Sessions</span><span className="mono" id="sbCount">{sessionsLoaded ? sessions.length : null}</span></div>
      <ul className="sb-list" id="sessionList">
        {groupByDay(sessions).map(([group, items]) => (
          <Fragment key={group}>
            <li className="sb-group">{group}</li>
            {items.map((s) => {
              const running = isLive(s.status); // live-briefs spec §4.5: a session waiting for the reader counts as running
              /* A running session is the only one that carries a mark: no chips, counts or durations here (index.html:1871-1876). */
              return (
                <li key={s.session_id}>
                  <button type="button" className="sb-item" data-session={s.session_id} data-run={running ? "1" : "0"} title={s.session_id}
                    aria-current={active === s.session_id ? "true" : "false"} aria-label={running ? `${s.query} — ${s.status === "needs_input" ? "waiting for you" : "running"}` : undefined}
                    onClick={() => navigate(`/research/${s.session_id}`)}>
                    <span className="q">{s.query}</span><span className="sb-live" aria-hidden="true"></span>
                  </button>
                </li>
              );
            })}
          </Fragment>
        ))}
      </ul>
      <div className="sb-foot">
        <p>Sessions are held in the service process's memory; this list empties when the service restarts.</p>
      </div>
    </aside>
  );
}
