// The SSE reader: fetch + ReadableStream, not EventSource (EventSource reconnects on
// its own schedule after every close, including a finished session's clean close, and hides the
// response headers the mode chip needs).
import type { ApiMode, ResearchEvent } from "./api";

export interface SseFrame { id: string | null; event: string | null; data: string }

/* Split complete frames (terminated by a blank line, LF or CRLF) off the front of `buffer`;
   return the unterminated remainder. Comment lines (":…") and data-less blocks are dropped. */
export function parseSse(buffer: string): { frames: SseFrame[]; rest: string } {
  const frames: SseFrame[] = [];
  let rest = buffer;
  for (;;) {
    const m = /\r?\n\r?\n/.exec(rest);
    if (!m) break;
    const raw = rest.slice(0, m.index);
    rest = rest.slice(m.index + m[0].length);
    const frame: SseFrame = { id: null, event: null, data: "" };
    const data: string[] = [];
    for (const line of raw.split(/\r?\n/)) {
      if (!line || line.startsWith(":")) continue;
      const colon = line.indexOf(":");
      const field = colon < 0 ? line : line.slice(0, colon);
      let value = colon < 0 ? "" : line.slice(colon + 1);
      if (value.startsWith(" ")) value = value.slice(1);
      if (field === "id") frame.id = value;
      else if (field === "event") frame.event = value;
      else if (field === "data") data.push(value);
    }
    if (data.length) { frame.data = data.join("\n"); frames.push(frame); }
  }
  return { frames, rest };
}

export interface StreamCallbacks {
  onOpen(mode: ApiMode | null): void;
  onEvent(event: ResearchEvent, id: number): void;
}
export type StreamEnd = { kind: "ended" } | { kind: "failed"; status: number | null; error: unknown };

/* One connection. Resolves `ended` when the server closes the body (a finished session), `failed`
   on a non-2xx, a network error or an abort. The page owns reconnecting (the backoff ladder). */
export async function readStream(url: string, callbacks: StreamCallbacks, signal: AbortSignal): Promise<StreamEnd> {
  let response: Response;
  try {
    response = await fetch(url, { headers: { accept: "text/event-stream" }, cache: "no-store", signal });
  } catch (error) {
    return { kind: "failed", status: null, error };
  }
  if (!response.ok || !response.body) return { kind: "failed", status: response.status, error: null };
  const mode = response.headers.get("x-deep-research-mode");
  callbacks.onOpen(mode === "live" || mode === "replay" ? mode : null);
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parsed = parseSse(buffer);
      buffer = parsed.rest;
      for (const frame of parsed.frames) callbacks.onEvent(JSON.parse(frame.data) as ResearchEvent, Number(frame.id));
    }
  } catch (error) {
    return { kind: "failed", status: response.status, error };
  }
  return { kind: "ended" };
}

/* 1, 2, 4, 8, 16 s, then 30 s forever. */
export function* backoffDelaysMs(): Generator<number> {
  for (const d of [1000, 2000, 4000, 8000, 16000]) yield d;
  for (;;) yield 30000;
}
