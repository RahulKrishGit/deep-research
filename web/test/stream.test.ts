// @vitest-environment node
import { createServer, type Server } from "node:http";
import { createServer as createNetServer, type Server as NetServer } from "node:net";
import { afterEach, describe, expect, it } from "vitest";
import type { ResearchEvent } from "../lib/api";
import { backoffDelaysMs, parseSse, readStream } from "../lib/stream";

const frame = (id: number, type: string) =>
  `id: ${id}\nevent: ${type}\ndata: ${JSON.stringify({ event_type: type, source: "graph", message: "m", timestamp: "2026-09-27T00:00:00+00:00", metadata: { id } })}\n\n`;

describe("parseSse", () => {
  it("splits complete frames and keeps the unterminated rest", () => {
    const text = frame(1, "graph.session.started") + "id: 2\nevent: graph.node.started\ndata: {\"a\":";
    const { frames, rest } = parseSse(text);
    expect(frames.map((f) => [f.id, f.event])).toEqual([["1", "graph.session.started"]]);
    expect(rest).toBe("id: 2\nevent: graph.node.started\ndata: {\"a\":");
  });
  it("handles CRLF, multi-line data and comment lines", () => {
    const { frames } = parseSse(": keep-alive\r\n\r\nid: 7\r\nevent: x\r\ndata: {\"a\":\r\ndata: 1}\r\n\r\n");
    expect(frames).toEqual([{ id: "7", event: "x", data: "{\"a\":\n1}" }]);
  });
  it("yields nothing for a comment-only block", () => {
    expect(parseSse(": ping\n\n").frames).toEqual([]);
  });
});

describe("backoffDelaysMs", () => {
  it("doubles from one second and settles at thirty", () => {
    const gen = backoffDelaysMs();
    expect(Array.from({ length: 7 }, () => gen.next().value)).toEqual([1000, 2000, 4000, 8000, 16000, 30000, 30000]);
  });
});

describe("readStream", () => {
  let server: Server | null = null;
  afterEach(() => new Promise<void>((r) => (server ? server.close(() => r()) : r())));
  // Node's Server#address() types as `string | AddressInfo | null`; the cast to the ephemeral
  // port shape is the standard idiom for reading back a `.listen(0, …)` port — no external or
  // user-controlled data is involved, so there is nothing to validate at runtime.
  const portOf = (s: NetServer) => (s.address() as { port: number }).port;
  const listen = (handler: Parameters<typeof createServer>[1]) =>
    new Promise<string>((resolve) => { server = createServer(handler).listen(0, "127.0.0.1", () => resolve(`http://127.0.0.1:${portOf(server!)}`)); });
  // No hard-coded refused port — reserve an ephemeral port with a real bind, then close it
  // so the same port is guaranteed refused for the duration of the assertion below.
  const reservePort = () =>
    new Promise<number>((resolve) => {
      const probe = createNetServer();
      probe.listen(0, "127.0.0.1", () => {
        const port = portOf(probe);
        probe.close(() => resolve(port));
      });
    });

  it("delivers frames as they arrive and resolves ended on a clean close", async () => {
    const origin = await listen((_req, res) => {
      res.writeHead(200, { "content-type": "text/event-stream", "x-deep-research-mode": "replay" });
      res.write(frame(1, "graph.session.started"));
      // real socket timing: proves frame 1 is delivered before frame 2 is written; a fake clock
      // cannot drive node:http.
      setTimeout(() => { res.write(frame(2, "graph.node.started")); res.end(); }, 100);
    });
    const seen: [string, number, number][] = [];
    let mode: string | null = "unset";
    const t0 = performance.now();
    const end = await readStream(`${origin}/stream`, {
      onOpen: (m) => { mode = m; },
      onEvent: (e: ResearchEvent, id) => seen.push([e.event_type, id, performance.now() - t0]),
    }, new AbortController().signal);
    expect(end).toEqual({ kind: "ended" });
    expect(mode).toBe("replay");
    expect(seen.map(([type, id]) => [type, id])).toEqual([["graph.session.started", 1], ["graph.node.started", 2]]);
    expect(seen[0][2]).toBeLessThan(90); // the first frame did not wait for the second
  });
  it("reports a non-2xx as failed with its status", async () => {
    const origin = await listen((_req, res) => { res.writeHead(404, { "content-type": "application/json" }); res.end("{}"); });
    const end = await readStream(`${origin}/stream`, { onOpen: () => {}, onEvent: () => {} }, new AbortController().signal);
    expect(end.kind).toBe("failed");
    expect(end.kind === "failed" && end.status).toBe(404);
  });
  it("reports a refused connection as failed with no status", async () => {
    const port = await reservePort();
    const end = await readStream(`http://127.0.0.1:${port}/stream`, { onOpen: () => {}, onEvent: () => {} }, new AbortController().signal);
    expect(end.kind).toBe("failed");
    expect(end.kind === "failed" && end.status).toBeNull();
  });
});
