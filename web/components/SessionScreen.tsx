"use client";
import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ApiError, ApiUnreachableError, getStatus, type ResearchSessionResponse } from "@/lib/api";
import { useConsole } from "./ConsoleProvider";
import { SessionNotFound } from "./SessionNotFound";

export function SessionScreen({ sessionId }: { sessionId: string }) {
  const router = useRouter();
  const { noteMode, noteUnreachable, clearUnreachable, setChip } = useConsole();
  const [status, setStatus] = useState<ResearchSessionResponse | null>(null);
  const [notFound, setNotFound] = useState(false);

  const load = useCallback(async () => {
    try {
      const result = await getStatus(sessionId);
      noteMode(result.mode);
      setStatus(result.data);
      clearUnreachable();
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) setNotFound(true);
      else if (error instanceof ApiUnreachableError) noteUnreachable(error.target, () => void load());
    }
  }, [sessionId, noteMode, clearUnreachable, noteUnreachable]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => () => setChip(null), [setChip]);

  if (notFound) return <SessionNotFound onNew={() => router.push("/")} />;
  if (!status) {
    return (
      <section className="stage is-on" id="stage-loading"><div className="run-wrap"><p className="avail">loading session</p></div></section>
    );
  }
  return (
    <section className="stage is-on" id="stage-running" aria-labelledby="running-h">
      <div className="run-wrap">
        <div className="ask-head">
          <p className="eyebrow" style={{ margin: 0 }}>Session running</p>
          <h1 className="ask-q ask-locked" id="running-h">{status.query}</h1>
        </div>
      </div>
    </section>
  );
}
