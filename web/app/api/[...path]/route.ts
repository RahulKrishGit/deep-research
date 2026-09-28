// The same-origin proxy: /api/<path> → ${DEEP_RESEARCH_API_URL}/<path>, request and response
// passed through as streams so an SSE body reaches the browser frame by frame (spec §4.1).
import type { NextRequest } from "next/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const REQUEST_HEADERS = ["accept", "content-type", "x-replay-case"] as const;
const RESPONSE_HEADERS = ["content-type", "cache-control", "x-accel-buffering", "x-deep-research-mode"] as const;
type Ctx = { params: Promise<{ path: string[] }> };

// M6: a trailing slash in DEEP_RESEARCH_API_URL would otherwise produce "//research" — a distinct,
// 404 route on the FastAPI side.
const apiOrigin = () => (process.env.DEEP_RESEARCH_API_URL ?? "http://127.0.0.1:8000").replace(/\/+$/, "");

// I1: undici's fetch raises BodyTimeoutError after ~300 s of upstream silence (no heartbeat is in
// scope — a config change, and the AC19 dependency freeze rules out `bodyTimeout: 0`, which needs
// the `undici` package). Left as a raw pipe, that reaches Next as an unhandled "failed to pipe
// response" stack and the browser sees an ECONNRESET mid-frame. Wrapping the body in a pull-based
// stream lets a read failure end the stream *cleanly* instead: the browser sees an ordinary close,
// re-reads /status and reconnects on its own ladder — the same recovery path a finished session's
// clean close already takes. `cancel` is forwarded to the upstream reader so an aborted/cancelled
// consumer (T-W4) still tears down the upstream connection.
function safeStream(body: ReadableStream<Uint8Array>): ReadableStream<Uint8Array> {
  const reader = body.getReader();
  return new ReadableStream<Uint8Array>({
    async pull(controller) {
      try {
        const { value, done } = await reader.read();
        if (done) { controller.close(); return; }
        controller.enqueue(value);
      } catch {
        controller.close();
      }
    },
    cancel(reason) {
      return reader.cancel(reason);
    },
  });
}

async function proxy(request: NextRequest, ctx: Ctx, method: "GET" | "POST"): Promise<Response> {
  const { path } = await ctx.params;
  const target = `${apiOrigin()}/${path.map(encodeURIComponent).join("/")}${request.nextUrl.search}`;
  const headers = new Headers();
  for (const name of REQUEST_HEADERS) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method,
      headers,
      body: method === "POST" ? await request.text() : undefined,
      cache: "no-store",
      signal: request.signal,
    });
  } catch {
    return Response.json(
      { error: { code: "api_unreachable", message: "Research service not reachable.", reason: null, issues: [], target: apiOrigin() } },
      { status: 502 },
    );
  }
  const out = new Headers();
  for (const name of RESPONSE_HEADERS) {
    const value = upstream.headers.get(name);
    if (value) out.set(name, value);
  }
  return new Response(upstream.body ? safeStream(upstream.body) : null, { status: upstream.status, headers: out });
}

export function GET(request: NextRequest, ctx: Ctx): Promise<Response> { return proxy(request, ctx, "GET"); }
export function POST(request: NextRequest, ctx: Ctx): Promise<Response> { return proxy(request, ctx, "POST"); }
