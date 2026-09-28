"use client";
import { useEffect, useState, type ReactNode } from "react";
import { useConsole } from "./ConsoleProvider";
import { ServiceBanner } from "./ServiceBanner";
import { Sidebar } from "./Sidebar";
import { Topbar } from "./Topbar";

export function AppShell({ children }: { children: ReactNode }) {
  const { sidebar, setSidebar, unreachable } = useConsole();
  const [ready, setReady] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  useEffect(() => { setReady(true); }, []);
  useEffect(() => {
    const sync = () => {
      const drawer = window.matchMedia("(max-width:1080px)").matches;
      document.body.classList.toggle("is-locked", drawer && sidebar === "expanded");
      setDrawerOpen(drawer && sidebar === "expanded");
    };
    sync();
    window.addEventListener("resize", sync);
    return () => window.removeEventListener("resize", sync);
  }, [sidebar]);
  useEffect(() => {
    // index.html:1908-1912 — Escape closes the drawer (the popover closes itself; see SettingsPopover).
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (window.matchMedia("(max-width:1080px)").matches && sidebar === "expanded") setSidebar("collapsed");
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [sidebar, setSidebar]);
  return (
    <div className="app" id="app" data-sidebar={sidebar} data-ready={ready ? "" : undefined}>
      <Sidebar />
      <button className="scrim" id="scrim" type="button" tabIndex={-1} aria-hidden={drawerOpen ? "false" : "true"} onClick={() => setSidebar("collapsed")}>
        <span className="sr">Close session history</span>
      </button>
      <div className="main">
        <Topbar />
        <div className="viewport" id="viewport">
          {unreachable ? <ServiceBanner target={unreachable.target} onRetry={unreachable.retry} /> : null}
          {children}
        </div>
      </div>
    </div>
  );
}
