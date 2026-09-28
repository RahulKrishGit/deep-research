"use client";
import { useEffect, type ReactNode } from "react";
import { useConsole } from "./ConsoleProvider";
import { ServiceBanner } from "./ServiceBanner";
import { Sidebar } from "./Sidebar";
import { Topbar } from "./Topbar";

export function AppShell({ children }: { children: ReactNode }) {
  const { sidebar, setSidebar, unreachable } = useConsole();
  useEffect(() => {
    const drawer = window.matchMedia("(max-width:1080px)").matches;
    document.body.classList.toggle("is-locked", drawer && sidebar === "expanded");
  }, [sidebar]);
  return (
    <div className="app" id="app" data-sidebar={sidebar}>
      <Sidebar />
      <button className="scrim" id="scrim" type="button" tabIndex={-1} aria-hidden="true" onClick={() => setSidebar("collapsed")}>
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
