"use client";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { ApiUnreachableError, listSessions, type ApiMode, type ResearchSessionResponse } from "@/lib/api";
import type { SessionView } from "@/lib/format";

type SidebarMode = "expanded" | "collapsed";
interface Unreachable { target: string; retry: () => void }
export interface ConsoleState {
  mode: ApiMode | null; noteMode(mode: ApiMode | null): void;
  chip: SessionView | null; setChip(view: SessionView | null): void;
  sessions: ResearchSessionResponse[]; refreshSessions(): Promise<void>;
  sidebar: SidebarMode; setSidebar(mode: SidebarMode): void;
  unreachable: Unreachable | null; noteUnreachable(target: string, retry: () => void): void; clearUnreachable(): void;
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
  const [sidebar, setSidebarState] = useState<SidebarMode>("expanded");
  const [unreachable, setUnreachable] = useState<Unreachable | null>(null);
  const noteMode = useCallback((m: ApiMode | null) => { if (m) setMode(m); }, []);
  const noteUnreachable = useCallback((target: string, retry: () => void) => setUnreachable({ target, retry }), []);
  const clearUnreachable = useCallback(() => setUnreachable(null), []);
  const refreshSessions = useCallback(async () => {
    try {
      const result = await listSessions(50);
      setSessions(result.data.sessions);
      noteMode(result.mode);
      setUnreachable(null);
    } catch (error) {
      if (error instanceof ApiUnreachableError) setUnreachable({ target: error.target, retry: () => void refreshSessions() });
    }
  }, [noteMode]);
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
  const setSidebar = useCallback((m: SidebarMode) => { setSidebarState(m); window.localStorage.setItem("dr.console.sidebar", m); }, []);
  const value = useMemo<ConsoleState>(
    () => ({ mode, noteMode, chip, setChip, sessions, refreshSessions, sidebar, setSidebar, unreachable, noteUnreachable, clearUnreachable }),
    [mode, noteMode, chip, sessions, refreshSessions, sidebar, setSidebar, unreachable, noteUnreachable, clearUnreachable],
  );
  return <ConsoleContext.Provider value={value}>{children}</ConsoleContext.Provider>;
}
