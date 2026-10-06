// The stage of a session the reader stopped, rendered by SessionScreen from /status and the stream.
// Times are built in local time, so the clock reads the same in any time zone.
import { fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ConsoleProvider } from "../../components/ConsoleProvider";
import { SessionScreen } from "../../components/SessionScreen";
import { Topbar } from "../../components/Topbar";
import type { ResearchSessionResponse } from "../../lib/api";
import { readSubmission } from "../../lib/session-store";

const { push } = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }), useParams: () => ({}) }));

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const sse = (frames: string) => new Response(frames, { status: 200, headers: { "content-type": "text/event-stream", "x-deep-research-mode": "replay" } });
const frame = (n: number, type: string, metadata: Record<string, unknown> = {}) =>
  `id: ${n}\ndata: ${JSON.stringify({ event_type: type, source: "graph", message: "m", timestamp: "2026-09-30T00:00:00+00:00", metadata })}\n\n`;
const STARTED = new Date(2026, 8, 30, 20, 34, 36).toISOString();
const STOPPED_AT = new Date(2026, 8, 30, 20, 41, 7).toISOString();
const STOPPED: ResearchSessionResponse = {
  session_id: "s1", query: "Where can we get the best tasting lattes in San Jose?", status: "stopped", current_agent: null,
  iteration: 0, started_at: STARTED, finished_at: STOPPED_AT, report_path: null, trace_url: null, errors: [],
  evidence_path: null, quality_path: null, quality_contract_version: null, semantic_review_status: null,
  semantic_review_score: null, duration_seconds: null, coverage: null, evidence_counts: null, stopped_step: "researcher",
};
const TITLES = [{ coverage_id: "topic-01", title: "Published picks" }, { coverage_id: "topic-02", title: "Opening hours" }];
const RESEARCHING = [
  frame(1, "graph.node.started", { node: "planner", iteration: 0 }),
  frame(2, "planner.planning.completed", { sub_topic_count: 2, sub_topics: TITLES }),
  frame(3, "graph.node.completed", { node: "planner" }),
  frame(4, "graph.node.started", { node: "researcher", iteration: 0 }),
  frame(5, "researcher.sub_topic.started", { coverage_id: "topic-01", sub_topic: "Published picks", index: 1 }),
  frame(6, "researcher.sub_topic.started", { coverage_id: "topic-02", sub_topic: "Opening hours", index: 2 }),
  frame(7, "researcher.sub_topic.completed", { coverage_id: "topic-02", sub_topic: "Opening hours", index: 2, successful_reads: 3, findings_retained: 4 }),
  frame(8, "session.stopped", { step: "researcher", stopped_at: STOPPED_AT, elapsed_seconds: 391 }),
].join("");
const ROWS = ["planner", "researcher", "source_evaluator", "evidence_verifier", "report_writer", "report_reviewer", "finalize_report"];

/* /status answers `status` and the stream `frames`; `post` answers a POST when it returns a response. */
function serve(status: ResearchSessionResponse, frames: string, post: (init: RequestInit) => Response | null = () => null) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (init?.method === "POST") { const answer = post(init); if (answer) return answer; }
    if (url.includes("/stream")) return sse(frames);
    if (url.includes("/status")) return json(200, status);
    return json(200, { sessions: [] });
  });
}
const text = (el: Element | null) => (el?.textContent ?? "").replace(/\s+/g, " ").trim();
const row = (id: string) => document.querySelector(`#stage-user-stopped #spine > li[data-stage="${id}"]`)!;
/* What a row says under its name: a finished row its outcome, any other row its live line. */
const said = (id: string) => {
  const state = row(id).getAttribute("data-state");
  return [state, text(row(id).querySelector(state === "done" || state === "loop" ? ".m-out" : ".m-live"))];
};

afterEach(() => { vi.unstubAllGlobals(); push.mockReset(); });

describe("the stopped stage", () => {
  it("says when the reader stopped and how far in, and that nothing was written, under the stopped chip", async () => {
    vi.stubGlobal("fetch", serve(STOPPED, RESEARCHING));
    render(<ConsoleProvider><Topbar /><SessionScreen sessionId="s1" /></ConsoleProvider>);
    // The stage renders from /status at once; the stream's events, and the chip, land a moment later.
    await waitFor(() => expect(text(document.querySelector("#topbarStatus .chip"))).toBe("Stopped by you · at Researching"));
    await waitFor(() => expect(row("planner").getAttribute("data-state")).toBe("done"));
    const stage = document.getElementById("stage-user-stopped")!;
    expect(text(stage.querySelector(".ask-head .eyebrow"))).toBe("Stopped by you");
    expect(text(document.getElementById("user-stopped-h"))).toBe(STOPPED.query);
    const note = stage.querySelector(".stopped-note")!;
    expect(note.getAttribute("role")).toBe("status");
    expect([...note.querySelectorAll("p")].map((p) => text(p))).toEqual([
      "You stopped this research at 20:41, 6 minutes in.",
      "No report was written. The plan and what research found so far are kept below until the service restarts.",
    ]);
    expect([text(document.getElementById("askAgain")), document.getElementById("askAgain")!.className]).toEqual(["Ask again", "btn btn-ghost btn-sm"]);
    expect(["stage-failed", "stage-report", "stage-stopped", "stage-running"].map((id) => document.getElementById(id))).toEqual([null, null, null, null]);
    expect(document.querySelector("#topbarStatus .chip .dot")!.className).toBe("dot dot-neutral");
    expect(document.getElementById("stopBtn")).toBeNull();
  });

  it("freezes the pipeline at the stopped row: finished rows openable, the stopped row with its facts, later rows not run", async () => {
    vi.stubGlobal("fetch", serve(STOPPED, RESEARCHING));
    render(<ConsoleProvider><SessionScreen sessionId="s1" /></ConsoleProvider>);
    await waitFor(() => expect(ROWS.map(said)).toEqual([
      ["done", "2 sub-topics · 0m 00s"],
      ["stopped", "Stopped · 1 of 2 topics done · 3 pages read · 4 findings"],
      ["off", "not run"], ["off", "not run"], ["off", "not run"], ["off", "not run"], ["off", "not run"],
    ]));
    expect(text(row("researcher").querySelector(".stage-name"))).toBe("Researching (stopped here)");
    expect(row("researcher").getAttribute("aria-current")).toBeNull();
    expect(ROWS.slice(0, 3).map((id) => row(id).querySelector("button.ps-toggle") !== null)).toEqual([true, true, false]);
    expect(row("researcher").getAttribute("data-open")).toBe("0");
    fireEvent.click(row("researcher").querySelector("button.ps-toggle")!);
    await waitFor(() => expect(row("researcher").getAttribute("data-open")).toBe("1"));
    const topics = [...row("researcher").querySelectorAll(".ps-topics > [data-topic]")];
    expect(topics.map((topic) => [topic.getAttribute("data-topic"), text(topic.querySelector(".tf"))])).toEqual([["stopped", "stopped"], ["done", "4 findings"]]);
    // No hand-off, no arc, no note line, no acknowledgement.
    expect(document.querySelector("#stage-user-stopped [data-handoff]")).toBeNull();
    expect(document.querySelector("#stage-user-stopped #spineWrap")!.getAttribute("data-loop")).toBe("off");
    expect(document.querySelector("#stage-user-stopped #spineWrap")!.hasAttribute("data-arc")).toBe(false);
    expect(document.getElementById("noteLine")).toBeNull();
    expect(document.querySelector("#stage-user-stopped .ack")).toBeNull();
  });

  it("reads 'not run again' on the rows a loop had re-armed, and 'Stopped' on a Researching row with no topics yet", async () => {
    const looped = [
      frame(1, "graph.node.started", { node: "planner", iteration: 0 }),
      frame(2, "graph.node.completed", { node: "planner" }),
      ...["researcher", "source_evaluator", "evidence_verifier", "report_writer"].flatMap((node, i) => [
        frame(3 + 2 * i, "graph.node.started", { node, iteration: 0 }), frame(4 + 2 * i, "graph.node.completed", { node }),
      ]),
      frame(11, "graph.node.started", { node: "report_reviewer", iteration: 0 }),
      frame(12, "graph.route.decided", { destination: "extra_pass", reason: "extra_pass_requested", missing_required_target_ids: ["topic-01-target-01"], iteration: 0 }),
      frame(13, "graph.node.completed", { node: "report_reviewer" }),
      frame(14, "session.stopped", { step: "researcher", stopped_at: STOPPED_AT, elapsed_seconds: 391 }),
    ].join("");
    vi.stubGlobal("fetch", serve(STOPPED, looped));
    render(<ConsoleProvider><SessionScreen sessionId="s1" /></ConsoleProvider>);
    await waitFor(() => expect(ROWS.map(said)).toEqual([
      ["done", "1–10 sub-topics"],
      ["stopped", "Stopped"],
      ["off", "not run again"], ["off", "not run again"], ["off", "not run again"], ["off", "not run again"], ["off", "not run"],
    ]));
  });

  it("shows no pipeline card after a stop during the one-time check, and says the run had not started", async () => {
    const check: ResearchSessionResponse = { ...STOPPED, stopped_step: "check" };
    const frames = frame(1, "session.clarification.requested", { questions: [], deadline_at: STOPPED_AT })
      + frame(2, "session.stopped", { step: "check", stopped_at: STOPPED_AT, elapsed_seconds: 30 });
    vi.stubGlobal("fetch", serve(check, frames));
    render(<ConsoleProvider><Topbar /><SessionScreen sessionId="s1" /></ConsoleProvider>);
    await waitFor(() => expect(text(document.querySelector("#topbarStatus .chip"))).toBe("Stopped by you · at the questions"));
    expect(text(document.querySelector(".stopped-note .b-now"))).toBe("You stopped this research at 20:41, before it started.");
    expect(document.querySelector("#stage-user-stopped .card")).toBeNull();
  });

  it("reads the time and the minutes from the response until the stream has said them, moves focus to that line, and acknowledges no note in the stopped row", async () => {
    // The 202 (or /status) can land before the stream's session.stopped: Researching is still the
    // stream's active row then, and a note it acknowledged must not show in the frozen brief.
    const noted = [
      frame(1, "graph.node.started", { node: "planner", iteration: 0 }),
      frame(2, "graph.node.completed", { node: "planner" }),
      frame(3, "graph.node.started", { node: "researcher", iteration: 0 }),
      frame(4, "session.note.received", { note_id: "n1", text: "Pastries too" }),
      frame(5, "session.note.interpreted", { note_id: "n1", restatement: "pastries too", kinds: ["emphasis"], replaces: null, fallback: false }),
    ].join("");
    vi.stubGlobal("fetch", serve(STOPPED, noted));
    render(<ConsoleProvider><SessionScreen sessionId="s1" /></ConsoleProvider>);
    await waitFor(() => expect(document.getElementById("stoppedLine")).toBeTruthy());
    expect(text(document.getElementById("stoppedLine"))).toBe("You stopped this research at 20:41, 6 minutes in.");
    // Focus moves in an effect, which runs after the render that first shows the line.
    await waitFor(() => expect(document.activeElement).toBe(document.getElementById("stoppedLine")));
    await waitFor(() => expect(row("planner").getAttribute("data-state")).toBe("done"));
    fireEvent.click(row("researcher").querySelector("button.ps-toggle")!);
    await waitFor(() => expect(row("researcher").getAttribute("data-open")).toBe("1"));
    expect(document.querySelector("#stage-user-stopped .ack")).toBeNull();
  });

  it("Ask again starts a new session with the same question and the default settings, records it and opens it", async () => {
    const posts: unknown[] = [];
    vi.stubGlobal("fetch", serve(STOPPED, RESEARCHING, (init) => {
      posts.push(JSON.parse(String(init.body)));
      return json(202, { ...STOPPED, session_id: "s2", status: "running", finished_at: null, stopped_step: null });
    }));
    render(<ConsoleProvider><SessionScreen sessionId="s1" /></ConsoleProvider>);
    await waitFor(() => expect(document.getElementById("askAgain")).toBeTruthy());
    fireEvent.click(document.getElementById("askAgain")!);
    await waitFor(() => expect(push).toHaveBeenCalledWith("/research/s2"));
    expect(posts).toEqual([{
      query: STOPPED.query, output_format: "markdown", ask_clarifying_questions: true,
      config_overrides: { llm: { model: "deepseek-flash", thinking_mode: "enabled" }, output: { directory: "output/" } },
    }]);
    expect(readSubmission("s2")).not.toBeNull();
  });

  it("a failed Ask again says so and lets the reader try again", async () => {
    vi.stubGlobal("fetch", serve(STOPPED, RESEARCHING, () => json(500, { error: { code: "http_500", message: "x", reason: null, issues: [] } })));
    render(<ConsoleProvider><SessionScreen sessionId="s1" /></ConsoleProvider>);
    await waitFor(() => expect(document.getElementById("askAgain")).toBeTruthy());
    fireEvent.click(document.getElementById("askAgain")!);
    await waitFor(() => expect(text(document.getElementById("askAgainFailed"))).toBe("Couldn't ask again — try again"));
    expect(document.getElementById("askAgainFailed")!.getAttribute("role")).toBe("alert");
    expect((document.getElementById("askAgain") as HTMLButtonElement).disabled).toBe(false);
    expect(push).not.toHaveBeenCalled();
  });
});
