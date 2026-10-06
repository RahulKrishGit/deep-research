"use client";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { ApiError, submitAnswers } from "@/lib/api";
import { answersBody, countdownText, resolvedAnswers, secondsLeft, summaryText, type CheckPhase, type Pick } from "@/lib/clarify";
import { qFitClass } from "@/lib/format";
import type { ClarifyQuestion, ClarifyState, RunState } from "@/lib/run-state";

/* A tapped answer shows as chosen for this long before the next question. */
export const CHOICE_ADVANCE_MS = 240;
type Sent = "idle" | "sending" | "sent" | "late" | "failed";

/* The one-time check takes the pipeline card's place on
   page 2, one question at a time. The header is the running stage's own — the locked question and
   the settings strip — so when the planner starts only the card below it changes. The card arrives
   once the stream has delivered the questions (.is-arriving, like the pipeline card). */
export function ClarifyStage({ sessionId, run, phase, question, strip }: { sessionId: string; run: RunState; phase: CheckPhase; question: string; strip: ReactNode }) {
  // A stream reconnect swaps in a fresh RunState before the replay arrives (SessionScreen's onOpen),
  // so run.clarify is null for a render or two: the last check the stream told keeps the card, and
  // the reader's place in it, mounted until the replayed request arrives. React's "adjust state
  // while rendering" idiom keeps it: a check the stream told replaces the kept one at once, and the
  // card renders from the kept one, so no ref is read during render.
  const [kept, setKept] = useState<ClarifyState | null>(run.clarify);
  if (run.clarify && run.clarify !== kept) setKept(run.clarify);
  return (
    <section className="stage is-on no-enter is-arriving" id="stage-clarify" aria-labelledby="clarify-h">
      <div className="run-wrap">
        <div className="ask-head">
          <p className="eyebrow" style={{ margin: 0 }}>Before we start</p>
          <h1 className={"ask-q ask-locked" + qFitClass(question)} id="clarify-h" aria-describedby="runningOpts">{question}</h1>
          {strip}
        </div>
        {/* A check with no questions (an answered event the stream never asked) has nothing to show. */}
        {kept && kept.questions.length > 0 ? <CheckCard sessionId={sessionId} check={kept} phase={phase} /> : null}
      </div>
    </section>
  );
}

function CheckCard({ sessionId, check, phase }: { sessionId: string; check: ClarifyState; phase: CheckPhase }) {
  const { questions } = check;
  const [step, setStep] = useState(0);
  const [picks, setPicks] = useState<Record<string, Pick>>({});
  const [otherOpen, setOtherOpen] = useState(false);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [sent, setSent] = useState<Sent>("idle");
  const [now, setNow] = useState(() => Date.now());
  const posted = useRef(false);
  const advancing = useRef<ReturnType<typeof setTimeout> | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const face = useRef<HTMLParagraphElement>(null);
  const moved = useRef(false);
  useEffect(() => {
    const tick = setInterval(() => setNow(Date.now()), 1000);
    return () => { clearInterval(tick); if (advancing.current) clearTimeout(advancing.current); };
  }, []);
  // Focus follows the reader's own move to another question, never the first render; once the
  // reader has sent the answers, it follows to the line that replaced the buttons they pressed.
  useEffect(() => { if (moved.current) heading.current?.focus(); }, [step]);
  useEffect(() => { if (posted.current) face.current?.focus(); }, [sent]);

  const q: ClarifyQuestion = questions[Math.min(step, questions.length - 1)];
  const pick = picks[q.id];
  const busy = () => advancing.current !== null || posted.current;

  /* One POST, never retried (web/lib/api.ts convention). */
  async function send(final: Record<string, Pick>, skip: boolean) {
    if (posted.current) return;
    posted.current = true;
    setPicks(final);
    setSent("sending");
    try {
      await submitAnswers(sessionId, { answers: answersBody(questions, final), skip });
      setSent("sent");
    } catch (error) {
      setSent(error instanceof ApiError && error.status === 409 ? "late" : "failed");
    }
  }
  function advance(final: Record<string, Pick>) {
    setPicks(final);
    if (step >= questions.length - 1) { void send(final, false); return; }
    moved.current = true;
    const next = step + 1;
    setOtherOpen(Boolean(final[questions[next].id] && "text" in final[questions[next].id]));
    setStep(next);
  }
  function choose(option: string) {
    if (busy()) return;
    const final = { ...picks, [q.id]: { choice: option } };
    setPicks(final);
    setOtherOpen(false);
    advancing.current = setTimeout(() => { advancing.current = null; advance(final); }, CHOICE_ADVANCE_MS);
  }
  function submitOther() {
    const text = (drafts[q.id] ?? "").trim();
    if (!text || busy()) return;
    advance({ ...picks, [q.id]: { text } });
  }
  function skipOne() {
    if (busy()) return;
    const final = { ...picks };
    delete final[q.id];
    advance(final);
  }
  /* "Just start": whatever the reader has answered, best guesses for the rest (reason "skipped").
     Words typed into the open Other… field are an answer too, so they go with it. */
  function justStart() {
    if (posted.current) return;
    if (advancing.current) { clearTimeout(advancing.current); advancing.current = null; }
    const text = otherOpen ? (drafts[q.id] ?? "").trim() : "";
    void send(text ? { ...picks, [q.id]: { text } } : picks, true);
  }
  function back() {
    if (busy() || step === 0) return;
    const previous = questions[step - 1];
    moved.current = true;
    setOtherOpen(Boolean(picks[previous.id] && "text" in picks[previous.id]));
    setStep(step - 1);
  }

  if (sent === "late") {
    return <div className="card" id="clarifyCard" data-face="late"><p className="sm ck-summary" tabIndex={-1} ref={face}>Already started with best guesses</p></div>;
  }
  if (sent === "failed" && phase === "asking") {
    return <div className="card" id="clarifyCard" data-face="failed"><p className="sm ck-summary" tabIndex={-1} ref={face}>Your answers could not be sent; the run starts with best guesses.</p></div>;
  }
  if (phase === "starting" || sent === "sending" || sent === "sent") {
    const answers = check.answered ? check.answered.answers : resolvedAnswers(questions, picks);
    return <div className="card" id="clarifyCard" data-face="summary"><p className="sm ck-summary" id="clarifySummary" tabIndex={-1} ref={face}>{summaryText(questions, answers)}</p></div>;
  }
  const chosen = pick && "choice" in pick ? pick.choice : null;
  const typed = pick && "text" in pick ? pick.text : null;
  const draft = drafts[q.id] ?? typed ?? "";
  return (
    <div className="card" id="clarifyCard" data-face="asking">
      <div className="ck-top">
        <span className="cap" id="clarifyStep">Question {step + 1} of {questions.length}</span>
        <span className="step-dots" aria-hidden="true">{questions.map((x, i) => <i key={x.id} data-on={i === step ? "1" : "0"} />)}</span>
      </div>
      <div className="ck-q" key={q.id}>
        <h3 className="card-title" id="clarifyQ" tabIndex={-1} ref={heading}>{q.text}</h3>
        <div className="choices" role="group" aria-labelledby="clarifyQ">
          {q.options.map((option) => (
            <button key={option} type="button" className="choice" aria-pressed={chosen === option} onClick={() => choose(option)}>
              <span>{option}</span>{option === q.bestGuess ? <span className="cap">best guess</span> : null}
            </button>
          ))}
          <button type="button" className="choice" id="clarifyOtherBtn" aria-pressed={typed !== null} aria-expanded={otherOpen} aria-controls="clarifyOtherRow"
            onClick={() => { if (!busy()) setOtherOpen((open) => !open); }}>
            <span>Other…</span>
          </button>
        </div>
        {otherOpen ? (
          <div className="ck-other" id="clarifyOtherRow">
            <input className="tx" id="clarifyOther" aria-label="Your own answer" placeholder="Type your own answer" maxLength={200} autoFocus value={draft}
              onChange={(e) => { const value = e.target.value; setDrafts((d) => ({ ...d, [q.id]: value })); }}
              onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); submitOther(); } }} />
            <button type="button" className="btn btn-ghost btn-sm" disabled={!draft.trim()} onClick={submitOther}>Next</button>
          </div>
        ) : null}
      </div>
      <div className="ic-foot">
        <div className="ic-btns">
          <button type="button" className="btn btn-quiet btn-sm ck-back" disabled={step === 0} onClick={back}>Back</button>
          <button type="button" className="btn btn-quiet btn-sm" onClick={skipOne}>Skip this one</button>
        </div>
        <button type="button" className="btn btn-ghost btn-sm" onClick={justStart}>Just start</button>
      </div>
      <p className="cap" id="clarifyCountdown">{countdownText(secondsLeft(check.deadlineAt, now))}</p>
    </div>
  );
}
