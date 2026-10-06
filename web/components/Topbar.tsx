// Topbar.tsx — the prototype's topbar and renderTopbar: the chip is the only status copy.
"use client";
import { useRef } from "react";
import { useConsole } from "./ConsoleProvider";
import { ModeChip } from "./ModeChip";
import { StatusChip } from "./StatusChip";
import { StopControl } from "./StopControl";
export function Topbar() {
  const { sidebar, setSidebar, chip, mode, stop } = useConsole();
  const open = sidebar === "expanded";
  const chipRef = useRef<HTMLSpanElement>(null); // where Stop's focus goes when Stop is withdrawn
  return (
    <header className="topbar">
      <div className="topbar-in">
        <button className="icon-btn" id="sidebarToggle" type="button" aria-controls="sidebar" aria-expanded={open} onClick={() => setSidebar(open ? "collapsed" : "expanded")}>
          <span className="sr">Toggle session history</span>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="square" aria-hidden="true" style={{ width: 17, height: 17 }}><path d="M4 6h16M4 12h16M4 18h16" /></svg>
        </button>
        <div className="topbar-right" id="topbarStatus">
          {chip ? <StatusChip view={chip} ref={chipRef} /> : null}
          {/* Stop sits after the status chip and before the replay chip. */}
          {stop ? <StopControl key={stop.sessionId} target={stop} returnFocusTo={chipRef} /> : null}
          {mode === "replay" ? <ModeChip /> : null}
        </div>
      </div>
    </header>
  );
}
