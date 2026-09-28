"use client";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { ApiUnreachableError, listSessions, type ApiMode, type ResearchSessionResponse } from "@/lib/api";
import type { SessionView } from "@/lib/format";

type SidebarMode = "expanded" | "collapsed";
interface Unreachable { target: string; retry: () => void }
export interface ConsoleState {
  mode: ApiMode | null; noteMode(mode: ApiMode | null): void;
  chip: SessionView | null; setChip(view: SessionView | null): void;
  sessions: ResearchSessionResponse[]; sessionsLoaded: boolean; refreshSessions(): Promise<void>;
  sidebar: SidebarMode; setSidebar(mode: SidebarMode): void;
  /* C1: keyed by owner ("sidebar", "session", "report", "evidence", "composer", …) so one
     component's success never silently dismisses another component's still-broken read. The
     banner is up while any key is registered; its target comes from whichever entry exists, and
     its Retry (`unreachable.retry`) re-runs every registered retry, not just the last one noted. */
  unreachable: Unreachable | null; noteUnreachable(key: string, target: string, retry: () => void): void; clearUnreachable(key: string): void;
}
const ConsoleContext = createContext<ConsoleState | null>(null);
export function useConsole(): ConsoleState {
  const value = useContext(ConsoleContext);
  if (!value) throw new Error("useConsole() outside <ConsoleProvider>");
  return value;
}
export function ConsoleProvider({ children }: { children: ReactNode }) {
  const [mode, setMode] = useState<ApiMode | null>(null);
  const [chip, setChip] = useState<SessionView | null>(null);
  const [sessions, setSessions] = useState<ResearchSessionResponse[]>([]);
  const [sessionsLoaded, setSessionsLoaded] = useState(false);
  const [sidebar, setSidebarState] = useState<SidebarMode>("expanded");
  const [registry, setRegistry] = useState<Record<string, Unreachable>>({});
  const noteMode = useCallback((m: ApiMode | null) => { if (m) setMode(m); }, []);
  const noteUnreachable = useCallback((key: string, target: string, retry: () => void) => {
    setRegistry((prev) => ({ ...prev, [key]: { target, retry } }));
  }, []);
  const clearUnreachable = useCallback((key: string) => {
    setRegistry((prev) => {
      if (!(key in prev)) return prev;
      const next = { ...prev };
      delete next[key];
      return next;
    });
  }, []);
  const refreshSessions = useCallback(async () => {
    try {
      const result = await listSessions(50);
      setSessions(result.data.sessions);
      setSessionsLoaded(true);
      noteMode(result.mode);
      clearUnreachable("sidebar");
    } catch (error) {
      if (error instanceof ApiUnreachableError) noteUnreachable("sidebar", error.target, () => void refreshSessions());
    }
  }, [noteMode, noteUnreachable, clearUnreachable]);
  useEffect(() => { void refreshSessions(); }, [refreshSessions]);
  const anyRunning = sessions.some((s) => s.status === "running");
  useEffect(() => {
    if (!anyRunning) return;
    const timer = setInterval(() => void refreshSessions(), 5000);
    return () => clearInterval(timer);
  }, [anyRunning, refreshSessions]);
  useEffect(() => {
    // On the drawer breakpoint (<= 1080 px) "expanded" means "open", so a phone starts closed (index.html:1891-1899).
    if (window.matchMedia("(max-width:1080px)").matches) { setSidebarState("collapsed"); return; }
    const saved = window.localStorage.getItem("dr.console.sidebar");
    if (saved === "collapsed" || saved === "expanded") setSidebarState(saved);
  }, []);
  const setSidebar = useCallback((m: SidebarMode) => {
    setSidebarState(m);
    if (!window.matchMedia("(max-width:1080px)").matches) window.localStorage.setItem("dr.console.sidebar", m);
  }, []);
  const retryAll = useCallback(() => {
    for (const entry of Object.values(registry)) entry.retry();
  }, [registry]);
  const unreachable = useMemo<Unreachable | null>(() => {
    const keys = Object.keys(registry);
    return keys.length === 0 ? null : { target: registry[keys[0]].target, retry: retryAll };
  }, [registry, retryAll]);
  const value = useMemo<ConsoleState>(
    () => ({ mode, noteMode, chip, setChip, sessions, sessionsLoaded, refreshSessions, sidebar, setSidebar, unreachable, noteUnreachable, clearUnreachable }),
    [mode, noteMode, chip, sessions, sessionsLoaded, refreshSessions, sidebar, setSidebar, unreachable, noteUnreachable, clearUnreachable],
  );
  return <ConsoleContext.Provider value={value}>{children}</ConsoleContext.Provider>;
}
