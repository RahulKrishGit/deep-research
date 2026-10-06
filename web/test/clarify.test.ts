// @vitest-environment node
// The one-time check's derivations: which face it shows, the answers
// as posted and as the run starts with them, the summary line and the countdown.
import { describe, expect, it } from "vitest";
import { answersBody, checkPhase, countdownText, pipelineBegun, resolvedAnswers, secondsLeft, summaryText } from "../lib/clarify";
import { applyEvent, newRunState, type RunState } from "../lib/run-state";

const WIRE = [
  { id: "q1", dimension: "geography", text: "Which region should this cover?", short: "Region", options: ["United States", "European Union", "Global"], best_guess: "Global" },
  { id: "q2", dimension: "period", text: "How recent should the sources be?", short: "Period", options: ["Last 12 months", "Since 2023", "Any time"], best_guess: "Since 2023" },
  { id: "q3", dimension: "purpose", text: "What will you use it for?", short: "For", options: ["General understanding", "A project or investment decision", "Policy or regulation work"], best_guess: "General understanding" },
];
const DEADLINE = "2026-09-29T10:01:00.000Z";
function asked(): RunState {
  const run = newRunState();
  applyEvent(run, { type: "session.clarification.requested", metadata: { questions: WIRE, deadline_at: DEADLINE } });
  return run;
}
function answered(run: RunState): RunState {
  applyEvent(run, { type: "session.clarification.answered", metadata: { reason: "answered", answers: [
    { question_id: "q1", value: "United States", source: "chosen" },
    { question_id: "q2", value: "since 2021", source: "typed" },
    { question_id: "q3", value: "General understanding", source: "best_guess" },
  ] } });
  return run;
}

describe("checkPhase: which face the check shows", () => {
  it("a needs_input status asks before the stream has delivered the questions; a running one shows no check", () => {
    expect(checkPhase(newRunState(), "needs_input")).toBe("asking");
    expect(checkPhase(newRunState(), "running")).toBeNull();
  });
  it("once the stream has told the check it decides: asking, then starting, then nothing once the planner starts", () => {
    const run = asked();
    expect(checkPhase(run, "running")).toBe("asking");
    answered(run);
    expect(checkPhase(run, "needs_input")).toBe("starting");
    expect(pipelineBegun(run)).toBe(false);
    applyEvent(run, { type: "graph.node.started", metadata: { node: "planner", iteration: 0 } });
    expect(pipelineBegun(run)).toBe(true);
    expect(checkPhase(run, "running")).toBeNull();
  });
  it("a session that has ended shows no check", () => {
    const run = asked();
    applyEvent(run, { type: "graph.session.completed", metadata: { status: "failed" } });
    expect(checkPhase(run, "running")).toBeNull();
  });
});

describe("the answers", () => {
  const questions = asked().clarify!.questions;
  it("the POST body has one entry per answered question, in question order", () => {
    expect(answersBody(questions, { q3: { choice: "General understanding" }, q1: { text: "Canada" } })).toEqual([
      { question_id: "q1", text: "Canada" },
      { question_id: "q3", choice: "General understanding" },
    ]);
    expect(answersBody(questions, {})).toEqual([]);
  });
  it("a question the reader left takes its best guess, as the API resolves it", () => {
    expect(resolvedAnswers(questions, { q2: { choice: "Any time" } })).toEqual([
      { questionId: "q1", value: "Global", source: "best_guess" },
      { questionId: "q2", value: "Any time", source: "chosen" },
      { questionId: "q3", value: "General understanding", source: "best_guess" },
    ]);
  });
  it("the summary says what the run starts with and who chose each value", () => {
    const run = answered(asked());
    expect(summaryText(run.clarify!.questions, run.clarify!.answered!.answers)).toBe(
      "Starting research with: Region: United States (you said) · Period: since 2021 (you said) · For: General understanding (best guess)",
    );
  });
});

describe("the countdown", () => {
  const at = Date.parse(DEADLINE);
  it("counts whole seconds to the deadline and stops at 0", () => {
    expect(secondsLeft(DEADLINE, at - 60_000)).toBe(60);
    expect(secondsLeft(DEADLINE, at - 41_500)).toBe(42);
    expect(secondsLeft(DEADLINE, at + 5_000)).toBe(0);
    expect(secondsLeft("not a time", at)).toBe(0);
  });
  it("reads m:ss, so a full minute is 1:00", () => {
    expect(countdownText(60)).toBe("Starts with best guesses in 1:00 if you don't answer");
    expect(countdownText(42)).toBe("Starts with best guesses in 0:42 if you don't answer");
    expect(countdownText(0)).toBe("Starts with best guesses in 0:00 if you don't answer");
  });
});
