// @vitest-environment node
import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import { createServer as createNetServer } from "node:net";
import { NextRequest } from "next/server";
import { afterEach, describe, expect, it } from "vitest";
import { GET, POST } from "../app/api/[...path]/route";

const ctx = (...path: string[]) => ({ params: Promise.resolve({ path }) });
let server: Server | null = null;
afterEach(async () => { if (server) await new Promise<void>((r) => server!.close(() => r())); server = null; });
function upstream(handler: (req: IncomingMessage, res: ServerResponse, body: string) => void): Promise<string> {
  return new Promise((resolve) => {
    server = createServer((req, res) => { let body = ""; req.on("data", (c) => (body += c)); req.on("end", () => handler(req, res, body)); })
      .listen(0, "127.0.0.1", () => resolve(`http://127.0.0.1:${(server!.address() as { port: number }).port}`));
  });
}
// No hard-coded refused port — reserve an ephemeral port with a real bind, then close it
// so the same port is guaranteed refused for the duration of the assertion below.
function reservePort(): Promise<number> {
  return new Promise((resolve) => {
    const probe = createNetServer();
    probe.listen(0, "127.0.0.1", () => {
      const port = (probe.address() as { port: number }).port;
      probe.close(() => resolve(port));
    });
  });
}

// These specs drive a real node:http server over a real socket and assert on genuine elapsed
// wall-clock time (the frame must arrive before the upstream's second write, and the abort must
// propagate to a real TCP close); fake timers would
// prove nothing about buffering.
describe("the proxy", () => {
  it("streams an SSE body frame by frame without buffering", async () => {
    let secondWritten = false;
    process.env.DEEP_RESEARCH_API_URL = await upstream((_req, res) => {
      res.writeHead(200, { "content-type": "text/event-stream", "cache-control": "no-cache", "x-accel-buffering": "no", "x-deep-research-mode": "replay", "server": "uvicorn", "date": "x" });
      res.write("id: 1\nevent: a\ndata: {}\n\n");
      setTimeout(() => { secondWritten = true; res.write("id: 2\nevent: b\ndata: {}\n\n"); res.end(); }, 300);
    });
    const response = await GET(new NextRequest("http://localhost:3000/api/research/s1/stream"), ctx("research", "s1", "stream"));
    expect(response.status).toBe(200);
    expect([...response.headers.keys()].sort()).toEqual(["cache-control", "content-type", "x-accel-buffering", "x-deep-research-mode"]);
    const reader = response.body!.getReader();
    const first = new TextDecoder().decode((await reader.read()).value);
    expect(first).toContain("event: a");
    expect(secondWritten).toBe(false); // the first frame reached us before the upstream wrote the second
    let rest = "";
    for (;;) { const { value, done } = await reader.read(); if (done) break; rest += new TextDecoder().decode(value); }
    expect(rest).toContain("event: b");
  });
  it("forwards a POST body, the query string and x-replay-case; passes the status through", async () => {
    const seen: { method?: string; url?: string; headers?: IncomingMessage["headers"]; body?: string } = {};
    process.env.DEEP_RESEARCH_API_URL = await upstream((req, res, body) => {
      Object.assign(seen, { method: req.method, url: req.url, headers: req.headers, body });
      res.writeHead(202, { "content-type": "application/json", "x-deep-research-mode": "live" });
      res.end(JSON.stringify({ session_id: "abc" }));
    });
    const request = new NextRequest("http://localhost:3000/api/research?limit=5", {
      method: "POST", body: JSON.stringify({ query: "q" }),
      headers: { "content-type": "application/json", "x-replay-case": "review-unavailable", cookie: "a=b", accept: "application/json" },
    });
    const response = await POST(request, ctx("research"));
    expect(response.status).toBe(202);
    expect(await response.json()).toEqual({ session_id: "abc" });
    expect(seen.method).toBe("POST");
    expect(seen.url).toBe("/research?limit=5");
    expect(seen.body).toBe(JSON.stringify({ query: "q" }));
    expect(seen.headers!["content-type"]).toBe("application/json");
    expect(seen.headers!["x-replay-case"]).toBe("review-unavailable");
    expect(seen.headers!["accept"]).toBe("application/json");
    expect(seen.headers!["cookie"]).toBeUndefined();
  });
  it("forwards x-replay-clarify, so replay's scripted check can be asked for", async () => {
    const seen: { headers?: IncomingMessage["headers"] } = {};
    process.env.DEEP_RESEARCH_API_URL = await upstream((req, res) => {
      seen.headers = req.headers;
      res.writeHead(202, { "content-type": "application/json" });
      res.end("{}");
    });
    const request = new NextRequest("http://localhost:3000/api/research", {
      method: "POST", body: "{}", headers: { "content-type": "application/json", "x-replay-clarify": "on" },
    });
    expect((await POST(request, ctx("research"))).status).toBe(202);
    expect(seen.headers!["x-replay-clarify"]).toBe("on");
  });
  it("forwards x-replay-hold-after, so a capture can hold replay's stream at one step", async () => {
    const seen: { headers?: IncomingMessage["headers"] } = {};
    process.env.DEEP_RESEARCH_API_URL = await upstream((req, res) => {
      seen.headers = req.headers;
      res.writeHead(202, { "content-type": "application/json" });
      res.end("{}");
    });
    const request = new NextRequest("http://localhost:3000/api/research", {
      method: "POST", body: "{}", headers: { "content-type": "application/json", "x-replay-hold-after": "evidence_verifier.progress#2" },
    });
    expect((await POST(request, ctx("research"))).status).toBe(202);
    expect(seen.headers!["x-replay-hold-after"]).toBe("evidence_verifier.progress#2");
  });
  it("answers 502 api_unreachable with the target when the connection is refused", async () => {
    const port = await reservePort();
    process.env.DEEP_RESEARCH_API_URL = `http://127.0.0.1:${port}`;
    const response = await GET(new NextRequest("http://localhost:3000/api/research"), ctx("research"));
    expect(response.status).toBe(502);
    expect(await response.json()).toEqual({ error: { code: "api_unreachable", message: "Research service not reachable.", reason: null, issues: [], target: `http://127.0.0.1:${port}` } });
  });
  it("forwards its abort signal: aborting the handler's request closes the upstream socket", async () => {
    let closed = false;
    process.env.DEEP_RESEARCH_API_URL = await upstream((req, res) => {
      res.writeHead(200, { "content-type": "text/event-stream" });
      res.write("id: 1\nevent: a\ndata: {}\n\n");
      req.on("close", () => { closed = true; });
    });
    const controller = new AbortController();
    const response = await GET(new NextRequest("http://localhost:3000/api/research/s1/stream", { signal: controller.signal }), ctx("research", "s1", "stream"));
    await response.body!.getReader().read();
    controller.abort();
    // Real wait: the abort must propagate through fetch → the upstream TCP socket → the
    // server's "close" event, none of which a fake clock can simulate.
    await new Promise((r) => setTimeout(r, 200));
    expect(closed).toBe(true);
  });
  it("ends the proxied body cleanly when the upstream body errors mid-stream (300 s idle timeout), never throwing", async () => {
    process.env.DEEP_RESEARCH_API_URL = await upstream((_req, res) => {
      res.writeHead(200, { "content-type": "text/event-stream" });
      res.write("id: 1\nevent: a\ndata: {}\n\n");
      // Real delay, not a fake timer: this drives a real node:http socket destroy() over a real
      // TCP connection (the file-level comment above explains why fake timers can't simulate
      // this), and only needs to fire after the first real chunk lands on the wire.
      setTimeout(() => res.destroy(), 50); // simulates undici's BodyTimeoutError after 300 s idle
    });
    const response = await GET(new NextRequest("http://localhost:3000/api/research/s1/stream"), ctx("research", "s1", "stream"));
    const reader = response.body!.getReader();
    const first = await reader.read();
    expect(new TextDecoder().decode(first.value)).toContain("event: a");
    // The upstream connection is destroyed mid-body; the wrapped stream must close cleanly
    // (done: true) instead of rejecting, so the browser sees an ordinary end and reconnects on
    // its own ladder rather than Next surfacing an unhandled "failed to pipe response" stack.
    const second = await reader.read();
    expect(second.done).toBe(true);
  });
  it("a non-SSE body (e.g. /report) that errors mid-send rejects instead of ending cleanly, so a truncated body never renders as a complete response", async () => {
    process.env.DEEP_RESEARCH_API_URL = await upstream((_req, res) => {
      res.writeHead(200, { "content-type": "text/markdown" });
      res.write("# Partial report\n\nSome content that never finishes");
      // Real delay, not a fake timer: see the SSE test above for why (drives a real socket
      // destroy(), needs to fire after the first real chunk lands on the wire).
      setTimeout(() => res.destroy(), 50);
    });
    const response = await GET(new NextRequest("http://localhost:3000/api/research/s1/report"), ctx("research", "s1", "report"));
    const reader = response.body!.getReader();
    const first = await reader.read();
    expect(new TextDecoder().decode(first.value)).toContain("Partial report");
    // Unlike the SSE case, a cut /report must surface as an error, never as a clean 200 the
    // browser would render as a complete, correct report.
    await expect(reader.read()).rejects.toBeTruthy();
  });
  it("strips a trailing slash from DEEP_RESEARCH_API_URL so the target path never doubles up", async () => {
    const seen: { url?: string } = {};
    const base = await upstream((req, res) => {
      seen.url = req.url;
      res.writeHead(200, { "content-type": "application/json" });
      res.end("{}");
    });
    process.env.DEEP_RESEARCH_API_URL = `${base}/`;
    const response = await GET(new NextRequest("http://localhost:3000/api/research"), ctx("research"));
    expect(response.status).toBe(200);
    expect(seen.url).toBe("/research");
  });
});
