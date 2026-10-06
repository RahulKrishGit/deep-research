"use client";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ApiUnreachableError, listSessions, type ApiMode, type ResearchSessionResponse } from "@/lib/api";
import { isLive, type SessionView } from "@/lib/format";

type SidebarMode = "expanded" | "collapsed";
interface Unreachable { target: string; retry: () => void }
/* The session the topbar's Stop acts on, and how the screen takes the
   stopped session the API answers with. */
export interface StopTarget { sessionId: string; onStopped(response: ResearchSessionResponse): void }
export interface ConsoleState {
  mode: ApiMode | null; noteMode(mode: ApiMode | null): void;
  chip: SessionView | null; setChip(view: SessionView | null): void;
  sessions: ResearchSessionResponse[]; sessionsLoaded: boolean; refreshSessions(): Promise<void>;
  sidebar: SidebarMode; setSidebar(mode: SidebarMode): void;
  /* Keyed by owner ("sidebar", "session", "report", "evidence", "composer", …) so one
     component's success never silently dismisses another component's still-broken read. The
     banner is up while any key is registered; its target comes from whichever entry exists, and
     its Retry (`unreachable.retry`) re-runs every registered retry, not just the last one noted. */
  unreachable: Unreachable | null; noteUnreachable(key: string, target: string, retry: () => void): void; clearUnreachable(key: string): void;
  /* Set by SessionScreen while its session can be stopped; null otherwise. */
  stop: StopTarget | null; setStop(target: StopTarget | null): void;
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
  const [stop, setStop] = useState<StopTarget | null>(null);
  const [sessions, setSessions] = useState<ResearchSessionResponse[]>([]);
  const [sessionsLoaded, setSessionsLoaded] = useState(false);
  const [sidebar, setSidebarState] = useState<SidebarMode>("expanded");
  const [registry, setRegistry] = useState<Record<string, Unreachable>>({});
  const registryRef = useRef<Record<string, Unreachable>>({});
  const noteMode = useCallback((m: ApiMode | null) => { if (m) setMode(m); }, []);
  const noteUnreachable = useCallback((key: string, target: string, retry: () => void) => {
    const next = { ...registryRef.current, [key]: { target, retry } };
    registryRef.current = next;
    setRegistry(next);
  }, []);
  const clearUnreachable = useCallback((key: string) => {
    if (!(key in registryRef.current)) return;
    const next = { ...registryRef.current };
    delete next[key];
    registryRef.current = next;
    setRegistry(next);
    // A read that just succeeded (or landed a definite 404) is decent evidence the
    // outage affecting *other* still-registered owners is over too — the
    // banner disappears on the first success, not only on the first success of every owner
    // independently. Without this, a key with no automatic ladder of its own (the sidebar's list
    // read has none — its 5 s poll only runs while the last *successfully loaded* list showed a
    // running session, which a failed read can never produce) could sit registered long after
    // the service came back, propping the banner up until something unrelated happened to call
    // its retry. `next` (not the pre-clear registry) so a key noted in the same tick is
    // included; the key that just cleared is skipped so it isn't re-run on its own success.
    // Each sibling gets one attempt, not a loop — a failed attempt re-registers itself exactly
    // like any other failure, and the next success cascades again from there.
    for (const [otherKey, entry] of Object.entries(next)) {
      if (otherKey !== key) entry.retry();
    }
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
  const anyRunning = sessions.some((s) => isLive(s.status)); // needs_input too: its mark must clear when the run ends
  useEffect(() => {
    if (!anyRunning) return;
    const timer = setInterval(() => void refreshSessions(), 5000);
    return () => clearInterval(timer);
  }, [anyRunning, refreshSessions]);
  useEffect(() => {
    // On the drawer breakpoint (<= 1080 px) "expanded" means "open", so a phone starts closed.
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
    () => ({ mode, noteMode, chip, setChip, sessions, sessionsLoaded, refreshSessions, sidebar, setSidebar, unreachable, noteUnreachable, clearUnreachable, stop, setStop }),
    [mode, noteMode, chip, sessions, sessionsLoaded, refreshSessions, sidebar, setSidebar, unreachable, noteUnreachable, clearUnreachable, stop],
  );
  return <ConsoleContext.Provider value={value}>{children}</ConsoleContext.Provider>;
}
