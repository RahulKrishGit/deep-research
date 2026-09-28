// Topbar.tsx — index.html:1175-1185 + renderTopbar (:2119-2130): the chip is the only status copy.
"use client";
import { useConsole } from "./ConsoleProvider";
import { ModeChip } from "./ModeChip";
import { StatusChip } from "./StatusChip";
export function Topbar() {
  const { sidebar, setSidebar, chip, mode } = useConsole();
  const open = sidebar === "expanded";
  return (
    <header className="topbar">
      <div className="topbar-in">
        <button className="icon-btn" id="sidebarToggle" type="button" aria-controls="sidebar" aria-expanded={open} onClick={() => setSidebar(open ? "collapsed" : "expanded")}>
          <span className="sr">Toggle session history</span>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="square" aria-hidden="true" style={{ width: 17, height: 17 }}><path d="M4 6h16M4 12h16M4 18h16" /></svg>
        </button>
        <div className="topbar-right" id="topbarStatus">
          {chip ? <StatusChip view={chip} /> : null}
          {mode === "replay" ? <ModeChip /> : null}
        </div>
      </div>
    </header>
  );
}
