// The one-time check's derivations: which face the check shows, the
// answers as the API takes them and as the run starts with them, the summary line and the
// countdown. Pure — a function of the stream's RunState and the reader's picks only.
import type { ClarificationAnswer, SessionStatus } from "./api";
import type { ClarifyAnswer, ClarifyQuestion, RunState } from "./run-state";

export type CheckPhase = "asking" | "starting";
/* What the reader picked for one question: an offered option, or their own words. */
export type Pick = { choice: string } | { text: string };

/* The pipeline has begun once any node has started or finished, or the session has ended. */
export function pipelineBegun(run: RunState): boolean {
  return run.openNode !== null || Object.keys(run.marks).length > 0 || run.finalStatus !== null;
}
/* "asking" while the check waits for the reader, "starting" from the answers until the planner
   starts, null otherwise. Once the stream has told the check, the stream decides; before that, a
   needs_input status alone reads as "asking" (the card then waits for the stream's questions). */
export function checkPhase(run: RunState, status: SessionStatus): CheckPhase | null {
  if (pipelineBegun(run)) return null;
  if (run.clarify) return run.clarify.answered ? "starting" : "asking";
  return status === "needs_input" ? "asking" : null;
}
/* The POST /answers entries: one per question the reader answered, in question order. */
export function answersBody(questions: readonly ClarifyQuestion[], picks: Readonly<Record<string, Pick>>): ClarificationAnswer[] {
  return questions.filter((q) => picks[q.id]).map((q) => ({ question_id: q.id, ...picks[q.id] }));
}
/* The answers the run starts with, resolved as the API resolves them: an unanswered question takes
   its best guess. */
export function resolvedAnswers(questions: readonly ClarifyQuestion[], picks: Readonly<Record<string, Pick>>): ClarifyAnswer[] {
  return questions.map((q): ClarifyAnswer => {
    const pick = picks[q.id];
    if (!pick) return { questionId: q.id, value: q.bestGuess, source: "best_guess" };
    return "choice" in pick ? { questionId: q.id, value: pick.choice, source: "chosen" } : { questionId: q.id, value: pick.text, source: "typed" };
  });
}
/* "Starting research with: Region: United States (you said) · Period: Since 2023 (best guess) · …" */
export function summaryText(questions: readonly ClarifyQuestion[], answers: readonly ClarifyAnswer[]): string {
  return "Starting research with: " + questions.map((q) => {
    const answer = answers.find((a) => a.questionId === q.id);
    const said = answer && answer.source !== "best_guess" ? "you said" : "best guess";
    return `${q.short}: ${answer ? answer.value : q.bestGuess} (${said})`;
  }).join(" · ");
}
/* Whole seconds until the check starts on best guesses; 0 once passed, or for an unreadable time. */
export function secondsLeft(deadlineAt: string, now: number): number {
  const at = Date.parse(deadlineAt);
  return Number.isFinite(at) ? Math.max(0, Math.ceil((at - now) / 1000)) : 0;
}
/* The footer cap, m:ss so that a full minute reads 1:00. */
export function countdownText(seconds: number): string {
  return `Starts with best guesses in ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")} if you don't answer`;
}
