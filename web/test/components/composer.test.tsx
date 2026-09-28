import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Composer, buildRequest, DEFAULT_SETTINGS } from "../../components/Composer";
import { ConsoleProvider } from "../../components/ConsoleProvider";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }), useParams: () => ({}) }));
const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
afterEach(() => vi.unstubAllGlobals());

describe("Composer", () => {
  it("builds the request the design specifies", () => {
    expect(buildRequest("  q  ", DEFAULT_SETTINGS)).toEqual({
      query: "q", max_iterations: 1, output_format: "markdown",
      config_overrides: { llm: { model: "deepseek-flash", thinking_mode: "enabled" }, output: { directory: "output/" } },
    });
    expect(buildRequest("q", { ...DEFAULT_SETTINGS, extraPasses: 0, outputDir: "" }).max_iterations).toBe(0);
    expect(buildRequest("q", { ...DEFAULT_SETTINGS, outputDir: "" }).config_overrides).toEqual({ llm: { model: "deepseek-flash", thinking_mode: "enabled" } });
  });
  it("shows the 422 issues under the box, keeps the question and posts exactly once", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "POST") return json(422, { error: { code: "validation_error", message: "Request validation failed.", reason: null, issues: [{ location: "body.config_overrides.llm.model", type: "value_error" }] } });
      return json(200, { sessions: [] });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ConsoleProvider><Composer /></ConsoleProvider>);
    const box = screen.getByLabelText("Research question") as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: "How mature is quantum error correction?" } });
    fireEvent.submit(box.closest("form")!);
    await waitFor(() => expect(screen.getByRole("alert").textContent).toBe("The service rejected the request: body.config_overrides.llm.model (value_error)"));
    expect(box.value).toBe("How mature is quantum error correction?");
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);
    expect(push).not.toHaveBeenCalled();
  });
  it("shows the enumerated configuration reason on a 500", async () => {
    vi.stubGlobal("fetch", vi.fn(async (_i: RequestInfo | URL, init?: RequestInit) =>
      init?.method === "POST" ? json(500, { error: { code: "configuration_error", message: "Research service configuration is unavailable.", reason: "missing_secrets", issues: [] } }) : json(200, { sessions: [] })));
    render(<ConsoleProvider><Composer /></ConsoleProvider>);
    fireEvent.change(screen.getByLabelText("Research question"), { target: { value: "q" } });
    fireEvent.submit(screen.getByLabelText("Research question").closest("form")!);
    await waitFor(() => expect(screen.getByRole("alert").textContent).toBe("Service configuration error · missing_secrets"));
  });
  it("omits the reason clause on a configuration error with no reason (K19: never invent a value)", async () => {
    vi.stubGlobal("fetch", vi.fn(async (_i: RequestInfo | URL, init?: RequestInit) =>
      init?.method === "POST" ? json(500, { error: { code: "configuration_error", message: "Research service configuration is unavailable.", reason: null, issues: [] } }) : json(200, { sessions: [] })));
    render(<ConsoleProvider><Composer /></ConsoleProvider>);
    fireEvent.change(screen.getByLabelText("Research question"), { target: { value: "q" } });
    fireEvent.submit(screen.getByLabelText("Research question").closest("form")!);
    await waitFor(() => expect(screen.getByRole("alert").textContent).toBe("Service configuration error"));
  });
  it("requires a question on empty submit and marks the field invalid", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json(200, { sessions: [] })));
    render(<ConsoleProvider><Composer /></ConsoleProvider>);
    const box = screen.getByLabelText("Research question") as HTMLTextAreaElement;
    fireEvent.submit(box.closest("form")!);
    expect(screen.getByRole("alert").textContent).toBe("A question is required.");
    expect(box.getAttribute("aria-invalid")).toBe("true");
  });
  it("clears the error and the invalid mark when the question changes", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json(200, { sessions: [] })));
    render(<ConsoleProvider><Composer /></ConsoleProvider>);
    const box = screen.getByLabelText("Research question") as HTMLTextAreaElement;
    fireEvent.submit(box.closest("form")!);
    expect(screen.getByRole("alert").textContent).toBe("A question is required.");
    fireEvent.change(box, { target: { value: "a" } });
    expect(screen.getByRole("alert").textContent).toBe("");
    expect(box.getAttribute("aria-invalid")).toBeNull();
  });
});
