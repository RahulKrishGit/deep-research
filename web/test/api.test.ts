// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, ApiUnreachableError, evidenceMarkdownUrl, getStatus, reportUrl, startResearch, streamUrl, submitAnswers } from "../lib/api";

const json = (status: number, body: unknown, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", ...headers } });
const request = { query: "q", output_format: "markdown" as const, config_overrides: {} };

afterEach(() => vi.unstubAllGlobals());

describe("the client", () => {
  it("posts once and captures the mode header", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => json(202, { session_id: "abc", query: "q", status: "running" }, { "x-deep-research-mode": "replay" }));
    vi.stubGlobal("fetch", fetchMock);
    const result = await startResearch(request);
    expect(result.mode).toBe("replay");
    expect(result.data.session_id).toBe("abc");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/research");
    expect((fetchMock.mock.calls[0][1] as RequestInit).method).toBe("POST");
  });
  it("maps the proxy's 502 to ApiUnreachableError and never retries the POST", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => json(502, { error: { code: "api_unreachable", message: "Research service not reachable.", reason: null, issues: [], target: "http://127.0.0.1:59999" } }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(startResearch(request)).rejects.toBeInstanceOf(ApiUnreachableError);
    await expect(startResearch(request)).rejects.toMatchObject({ target: "http://127.0.0.1:59999" });
    expect(fetchMock).toHaveBeenCalledTimes(2); // one call per startResearch, none of its own
  });
  it("never retries a network error", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => { throw new TypeError("fetch failed"); });
    vi.stubGlobal("fetch", fetchMock);
    await expect(startResearch(request)).rejects.toBeInstanceOf(TypeError);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
  it("maps a 500 configuration_error to ApiError with its reason, in one call", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => json(500, { error: { code: "configuration_error", message: "Service configuration error.", reason: "missing_secrets", issues: [] } }));
    vi.stubGlobal("fetch", fetchMock);
    const error = await startResearch(request).catch((e) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error.body.reason).toBe("missing_secrets");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
  it("maps a 422 to ApiError with the issues", async () => {
    vi.stubGlobal("fetch", vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => json(422, { error: { code: "validation_error", message: "Request validation failed.", reason: null, issues: [{ location: "body.query", type: "string_too_short" }] } })));
    const error = await startResearch(request).catch((e) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(422);
    expect(error.body.issues).toEqual([{ location: "body.query", type: "string_too_short" }]);
  });
  it("maps a 404 status read to ApiError", async () => {
    vi.stubGlobal("fetch", vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => json(404, { error: { code: "session_not_found", message: "Research session not found.", reason: null, issues: [] } })));
    const error = await getStatus("nope").catch((e) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error.body.code).toBe("session_not_found");
  });
  it("posts the check's answers once to the session's answers route (live-briefs spec §4.4)", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => json(202, { session_id: "s1", status: "needs_input" }, { "x-deep-research-mode": "replay" }));
    vi.stubGlobal("fetch", fetchMock);
    const body = { answers: [{ question_id: "q1", choice: "Global" }, { question_id: "q2", text: "since 2021" }], skip: false };
    const result = await submitAnswers("s1", body);
    expect(result.data.status).toBe("needs_input");
    expect(result.mode).toBe("replay");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/research/s1/answers");
    expect((fetchMock.mock.calls[0][1] as RequestInit).method).toBe("POST");
    expect(JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string)).toEqual(body);
  });
  it("maps a late answer's 409 to ApiError and never retries it", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => json(409, { error: { code: "not_waiting_for_input", message: "Research session is not waiting for answers.", reason: null, issues: [] } }));
    vi.stubGlobal("fetch", fetchMock);
    const error = await submitAnswers("s1", { answers: [], skip: true }).catch((e) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect([error.status, error.body.code]).toEqual([409, "not_waiting_for_input"]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
  it("builds same-origin URLs", () => {
    expect(reportUrl("abc")).toBe("/api/research/abc/report");
    expect(evidenceMarkdownUrl("abc")).toBe("/api/research/abc/evidence?format=markdown");
    expect(streamUrl("abc")).toBe("/api/research/abc/stream");
  });
});
