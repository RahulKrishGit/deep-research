// The same-origin proxy: /api/<path> → ${DEEP_RESEARCH_API_URL}/<path>, request and response
// passed through as streams so an SSE body reaches the browser frame by frame (spec §4.1).
import type { NextRequest } from "next/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

// x-replay-clarify: replay's scripted one-time check asks only when the POST carried it (live-briefs spec §4.4).
// x-replay-hold-after: replay holds its stream after the named event, for captures (notes-progress-report spec §6.10).
const REQUEST_HEADERS = ["accept", "content-type", "x-replay-case", "x-replay-clarify", "x-replay-hold-after"] as const;
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
// NB5: that clean-close recovery is specific to the *stream* endpoint, whose reader already knows
// how to notice a gap and reconnect. A one-shot body — /report, /evidence — has no such reader: a
// cut response would otherwise reach the browser as a complete 200 and render as a full,
// truncated-but-plausible report. Only `text/event-stream` closes cleanly on a read failure;
// every other content type surfaces it as a rejection, exactly as an unwrapped stream would have.
function safeStream(body: ReadableStream<Uint8Array>, contentType: string | null): ReadableStream<Uint8Array> {
  const reader = body.getReader();
  const isEventStream = contentType?.startsWith("text/event-stream") ?? false;
  return new ReadableStream<Uint8Array>({
    async pull(controller) {
      try {
        const { value, done } = await reader.read();
        if (done) { controller.close(); return; }
        controller.enqueue(value);
      } catch (error) {
        if (isEventStream) controller.close();
        else controller.error(error);
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
  return new Response(upstream.body ? safeStream(upstream.body, upstream.headers.get("content-type")) : null, { status: upstream.status, headers: out });
}

export function GET(request: NextRequest, ctx: Ctx): Promise<Response> { return proxy(request, ctx, "GET"); }
export function POST(request: NextRequest, ctx: Ctx): Promise<Response> { return proxy(request, ctx, "POST"); }
