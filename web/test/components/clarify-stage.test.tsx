import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CHOICE_ADVANCE_MS, ClarifyStage } from "../../components/ClarifyStage";
import { applyEvent, newRunState, type RunState } from "../../lib/run-state";

const QUESTIONS = [
  { id: "q1", dimension: "geography", text: "Which region should this cover?", short: "Region", options: ["United States", "European Union", "Global"], best_guess: "Global" },
  { id: "q2", dimension: "period", text: "How recent should the sources be?", short: "Period", options: ["Last 12 months", "Since 2023", "Any time"], best_guess: "Since 2023" },
  { id: "q3", dimension: "purpose", text: "What will you use it for?", short: "For", options: ["General understanding", "A project or investment decision", "Policy or regulation work"], best_guess: "General understanding" },
];
const NOW = Date.parse("2026-09-29T10:00:00.000Z");
const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

function asking(): RunState {
  const run = newRunState();
  applyEvent(run, { type: "session.clarification.requested", metadata: { questions: QUESTIONS, deadline_at: "2026-09-29T10:01:00.000Z" } });
  return run;
}
function show(run: RunState = asking(), phase: "asking" | "starting" = "asking") {
  return render(<ClarifyStage sessionId="s1" run={run} phase={phase} question="What limits grid-scale battery storage?" strip={null} />);
}
const card = () => document.getElementById("clarifyCard")!;
const option = (name: string) => screen.getByRole("button", { name: new RegExp(`^${name}`) });
const posts = (fetchMock: ReturnType<typeof vi.fn>) => fetchMock.mock.calls.filter(([, init]) => (init as RequestInit | undefined)?.method === "POST");
const tap = (name: string) => { fireEvent.click(option(name)); act(() => { vi.advanceTimersByTime(CHOICE_ADVANCE_MS); }); };

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

describe("ClarifyStage — the card (live-briefs spec §4.5, pick 4B)", () => {
  it("asks question 1 of 3 with its step dots, the best guess marked, Other… last, and no purple", () => {
    vi.useFakeTimers({ now: NOW });
    show();
    expect(document.getElementById("stage-clarify")!.className).toBe("stage is-on no-enter is-arriving");
    expect(document.getElementById("clarifyStep")!.textContent).toBe("Question 1 of 3");
    expect([...card().querySelectorAll(".step-dots i")].map((i) => i.getAttribute("data-on"))).toEqual(["1", "0", "0"]);
    expect(screen.getByRole("heading", { level: 3 }).textContent).toBe("Which region should this cover?");
    const answers = [...card().querySelectorAll(".choices .choice")];
    expect(answers.map((b) => b.textContent)).toEqual(["United States", "European Union", "Globalbest guess", "Other…"]);
    expect(answers.map((b) => b.getAttribute("aria-pressed"))).toEqual(["false", "false", "false", "false"]);
    expect(card().querySelector(".choice .cap")!.textContent).toBe("best guess");
    expect(option("Back")).toHaveProperty("disabled", true);
    expect(document.getElementById("clarifyCountdown")!.textContent).toBe("Starts with best guesses in 1:00 if you don't answer");
    expect(card().querySelector(".btn-primary")).toBeNull();
  });

  it("counts down from the deadline, once a second", () => {
    vi.useFakeTimers({ now: NOW });
    show();
    act(() => { vi.advanceTimersByTime(18_000); });
    expect(document.getElementById("clarifyCountdown")!.textContent).toBe("Starts with best guesses in 0:42 if you don't answer");
  });

  it("marks a tapped answer and moves on after 240 ms; Back returns with it pressed", () => {
    vi.useFakeTimers({ now: NOW });
    show();
    fireEvent.click(option("European Union"));
    expect(option("European Union").getAttribute("aria-pressed")).toBe("true");
    expect(document.getElementById("clarifyStep")!.textContent).toBe("Question 1 of 3");
    act(() => { vi.advanceTimersByTime(CHOICE_ADVANCE_MS); });
    expect(document.getElementById("clarifyStep")!.textContent).toBe("Question 2 of 3");
    expect([...card().querySelectorAll(".step-dots i")].map((i) => i.getAttribute("data-on"))).toEqual(["0", "1", "0"]);
    expect(document.activeElement).toBe(screen.getByRole("heading", { level: 3 }));
    fireEvent.click(option("Back"));
    expect(document.getElementById("clarifyStep")!.textContent).toBe("Question 1 of 3");
    expect(option("European Union").getAttribute("aria-pressed")).toBe("true");
  });

  it("Other… is pressed only once it holds the reader's own words, never beside a chosen answer", () => {
    vi.useFakeTimers({ now: NOW });
    show();
    tap("United States");
    fireEvent.click(option("Back"));
    fireEvent.click(option("Other…"));
    // .choice[aria-pressed="true"] is the only style that marks a chosen answer (globals.css), so the
    // attribute is the whole visual state: exactly one answer reads chosen, and it is the tapped one.
    expect([...card().querySelectorAll('.choices .choice[aria-pressed="true"]')].map((b) => b.textContent)).toEqual(["United States"]);
    expect(option("Other…").getAttribute("aria-pressed")).toBe("false");
    expect(option("Other…").getAttribute("aria-expanded")).toBe("true");
    // Words typed into Other… make it the chosen answer, and only it, when the reader comes back to it.
    fireEvent.change(screen.getByLabelText("Your own answer"), { target: { value: "Canada" } });
    fireEvent.click(option("Next"));
    fireEvent.click(option("Back"));
    expect([...card().querySelectorAll('.choices .choice[aria-pressed="true"]')].map((b) => b.textContent)).toEqual(["Other…"]);
  });

  it("opens Other… on a text field; Next waits for text; Enter moves on", () => {
    vi.useFakeTimers({ now: NOW });
    show();
    fireEvent.click(option("Other…"));
    const field = screen.getByLabelText("Your own answer") as HTMLInputElement;
    expect(field.className).toBe("tx");
    expect(document.activeElement).toBe(field);
    expect(option("Other…").getAttribute("aria-expanded")).toBe("true");
    expect(option("Next")).toHaveProperty("disabled", true);
    fireEvent.change(field, { target: { value: "  Canada " } });
    expect(option("Next")).toHaveProperty("disabled", false);
    fireEvent.keyDown(field, { key: "Enter" });
    expect(document.getElementById("clarifyStep")!.textContent).toBe("Question 2 of 3");
  });

  it("posts the answers once after the last question and shows the summary", async () => {
    vi.useFakeTimers({ now: NOW });
    const fetchMock = vi.fn(async () => json(202, { session_id: "s1", status: "needs_input" }));
    vi.stubGlobal("fetch", fetchMock);
    show();
    tap("United States");
    fireEvent.click(option("Other…"));
    fireEvent.change(screen.getByLabelText("Your own answer"), { target: { value: "since 2021" } });
    fireEvent.click(option("Next"));
    await act(async () => { fireEvent.click(option("Skip this one")); });
    expect(posts(fetchMock)).toHaveLength(1);
    const [url, init] = posts(fetchMock)[0] as [string, RequestInit];
    expect(url).toBe("/api/research/s1/answers");
    expect(JSON.parse(init.body as string)).toEqual({ answers: [{ question_id: "q1", choice: "United States" }, { question_id: "q2", text: "since 2021" }], skip: false });
    expect(document.getElementById("clarifySummary")!.textContent).toBe(
      "Starting research with: Region: United States (you said) · Period: since 2021 (you said) · For: General understanding (best guess)",
    );
    expect(card().getAttribute("data-face")).toBe("summary");
    expect(document.activeElement).toBe(document.getElementById("clarifySummary"));
  });

  it("Just start posts what was answered so far with skip, once", async () => {
    vi.useFakeTimers({ now: NOW });
    const fetchMock = vi.fn(async () => json(202, { session_id: "s1", status: "needs_input" }));
    vi.stubGlobal("fetch", fetchMock);
    show();
    tap("Global");
    await act(async () => { fireEvent.click(option("Just start")); });
    expect(posts(fetchMock)).toHaveLength(1);
    expect(JSON.parse((posts(fetchMock)[0][1] as RequestInit).body as string)).toEqual({ answers: [{ question_id: "q1", choice: "Global" }], skip: true });
    expect(document.getElementById("clarifySummary")!.textContent).toBe(
      "Starting research with: Region: Global (you said) · Period: Since 2023 (best guess) · For: General understanding (best guess)",
    );
    expect(document.activeElement).toBe(document.getElementById("clarifySummary"));
  });

  it("Just start keeps the words typed into the open Other… field for the question in view", async () => {
    vi.useFakeTimers({ now: NOW });
    const fetchMock = vi.fn(async () => json(202, { session_id: "s1", status: "needs_input" }));
    vi.stubGlobal("fetch", fetchMock);
    show();
    tap("Global");
    fireEvent.click(option("Other…"));
    fireEvent.change(screen.getByLabelText("Your own answer"), { target: { value: "  since 2021 " } });
    await act(async () => { fireEvent.click(option("Just start")); });
    expect(posts(fetchMock)).toHaveLength(1);
    expect(JSON.parse((posts(fetchMock)[0][1] as RequestInit).body as string)).toEqual({
      answers: [{ question_id: "q1", choice: "Global" }, { question_id: "q2", text: "since 2021" }],
      skip: true,
    });
    expect(document.getElementById("clarifySummary")!.textContent).toBe(
      "Starting research with: Region: Global (you said) · Period: since 2021 (you said) · For: General understanding (best guess)",
    );
  });

  it("Just start with an empty or closed Other… field sends only what was answered", async () => {
    vi.useFakeTimers({ now: NOW });
    const fetchMock = vi.fn(async () => json(202, { session_id: "s1", status: "needs_input" }));
    vi.stubGlobal("fetch", fetchMock);
    show();
    fireEvent.click(option("Other…"));
    fireEvent.change(screen.getByLabelText("Your own answer"), { target: { value: "  " } });
    await act(async () => { fireEvent.click(option("Just start")); });
    expect(JSON.parse((posts(fetchMock)[0][1] as RequestInit).body as string)).toEqual({ answers: [], skip: true });
  });

  it("an answer the API refuses as late reads 'Already started with best guesses'", async () => {
    vi.useFakeTimers({ now: NOW });
    vi.stubGlobal("fetch", vi.fn(async () => json(409, { error: { code: "not_waiting_for_input", message: "Research session is not waiting for answers.", reason: null, issues: [] } })));
    show();
    await act(async () => { fireEvent.click(option("Just start")); });
    expect(card().textContent).toBe("Already started with best guesses");
    expect(document.activeElement).toBe(card().querySelector(".ck-summary"));
  });

  it("any other failure says the answers were not sent, and never posts again", async () => {
    vi.useFakeTimers({ now: NOW });
    const fetchMock = vi.fn(async () => json(502, { error: { code: "api_unreachable", message: "Research service not reachable.", reason: null, issues: [], target: "http://127.0.0.1:8010" } }));
    vi.stubGlobal("fetch", fetchMock);
    show();
    await act(async () => { fireEvent.click(option("Just start")); });
    expect(card().textContent).toBe("Your answers could not be sent; the run starts with best guesses.");
    expect(document.activeElement).toBe(card().querySelector(".ck-summary"));
    expect(posts(fetchMock)).toHaveLength(1);
  });

  it("once the stream says the check was answered, it shows what the run starts with", () => {
    const run = asking();
    applyEvent(run, { type: "session.clarification.answered", metadata: { reason: "timed_out", answers: QUESTIONS.map((q) => ({ question_id: q.id, value: q.best_guess, source: "best_guess" })) } });
    show(run, "starting");
    expect(document.getElementById("clarifySummary")!.textContent).toBe(
      "Starting research with: Region: Global (best guess) · Period: Since 2023 (best guess) · For: General understanding (best guess)",
    );
  });

  it("keeps the reader's place when a reconnect swaps in a fresh run state before the replay", () => {
    vi.useFakeTimers({ now: NOW });
    const { rerender } = show();
    tap("United States");
    expect(document.getElementById("clarifyStep")!.textContent).toBe("Question 2 of 3");
    const props = { sessionId: "s1", phase: "asking" as const, question: "What limits grid-scale battery storage?", strip: null };
    rerender(<ClarifyStage {...props} run={newRunState()} />);
    expect(document.getElementById("clarifyStep")!.textContent).toBe("Question 2 of 3");
    rerender(<ClarifyStage {...props} run={asking()} />);
    expect(document.getElementById("clarifyStep")!.textContent).toBe("Question 2 of 3");
    fireEvent.click(option("Back"));
    expect(option("United States").getAttribute("aria-pressed")).toBe("true");
  });

  it("holds the header alone until the stream delivers the questions", () => {
    show(newRunState());
    expect(document.getElementById("clarify-h")!.textContent).toBe("What limits grid-scale battery storage?");
    expect(document.getElementById("clarifyCard")).toBeNull();
  });

  it("renders no summary at all when the stream answered a check it never asked (no questions to summarise)", () => {
    const run = newRunState();
    applyEvent(run, { type: "session.clarification.answered", metadata: { reason: "skipped", answers: [] } });
    show(run, "starting");
    expect(document.getElementById("clarify-h")!.textContent).toBe("What limits grid-scale battery storage?");
    expect(document.getElementById("clarifyCard")).toBeNull();
    expect(document.body.textContent).not.toContain("Starting research with");
  });
});
