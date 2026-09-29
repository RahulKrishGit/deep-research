"use client";
import { useLayoutEffect, useRef, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { ApiError, ApiUnreachableError, startResearch, type ResearchRequest } from "@/lib/api";
import { armIdleToRunningFlight, beginIdleToRunningClear, cancelIdleToRunningClear, submittedBeatBudgetMs } from "@/lib/handoff";
import { recordSubmission, type SubmittedSettings } from "@/lib/session-store";
import { useConsole } from "./ConsoleProvider";
import { SettingsPopover, shortModel } from "./SettingsPopover";

// page.tsx's <section id="stage-idle">: the host the Clear beat drains (DESIGN.md:1371-1377).
const IDLE_STAGE_ID = "stage-idle";

export const STARTERS = [
  "What are the current constraints on grid-scale battery storage deployment?",
  "How mature is quantum error correction?",
  "What is the current state of sodium-ion battery energy density?",
  "What limits perovskite solar cell commercial lifetime?",
  "Does congestion pricing reduce particulate pollution?",
  "How reliable are consumer-grade air quality sensors?",
];
export const DEFAULT_SETTINGS: SubmittedSettings = { model: "deepseek-flash", thinking: "enabled", extraPasses: 1, outputDir: "output/" };

/* Exactly the body the design specifies: max_iterations always present (0 included). */
export function buildRequest(question: string, s: SubmittedSettings): ResearchRequest {
  const config_overrides: Record<string, unknown> = { llm: { model: s.model, thinking_mode: s.thinking } };
  if (s.outputDir.trim()) config_overrides.output = { directory: s.outputDir.trim() };
  return { query: question.trim(), max_iterations: s.extraPasses, output_format: "markdown", config_overrides };
}

/* K19 (governing rule, spec §4.4): an unavailable value is never invented — a configuration error
   with no enumerated reason reads as the bare sentence, never "· unknown". */
function configurationErrorText(reason: string | null): string {
  return reason === null ? "Service configuration error" : `Service configuration error · ${reason}`;
}

export function Composer() {
  const router = useRouter();
  const { refreshSessions, noteMode, noteUnreachable, clearUnreachable } = useConsole();
  const [question, setQuestion] = useState("");
  const [settings, setSettings] = useState<SubmittedSettings>(DEFAULT_SETTINGS);
  const [error, setError] = useState<string | null>(null);
  const [invalid, setInvalid] = useState(false);
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(false);
  const plus = useRef<HTMLButtonElement>(null);
  const prompt = useRef<HTMLTextAreaElement>(null);

  /* index.html:2303-2317, DESIGN.md:371-376 — the box fits what has been typed, capped at the
     stylesheet's own min(232px,32vh) (.composer textarea's max-height), read back rather than
     repeated here so the viewport-relative half of the cap keeps working unmodified. */
  useLayoutEffect(() => {
    const el = prompt.current;
    if (!el) return;
    el.style.height = "auto";
    const want = el.scrollHeight;
    if (!want) return;
    let cap = parseFloat(getComputedStyle(el).maxHeight);
    if (!Number.isFinite(cap) || cap <= 0) cap = 232;
    const edges = el.offsetHeight - el.clientHeight || 0;
    el.style.height = `${Math.min(want + edges, cap)}px`;
  }, [question]);

  async function submit(raw: string) {
    const text = raw.trim();
    if (!text) {
      // index.html:2404-2410 — an empty submit never reaches the network.
      setError("A question is required.");
      setInvalid(true);
      prompt.current?.focus();
      return;
    }
    if (busy) return;
    setBusy(true); setError(null); setInvalid(false);
    // Beat one (DESIGN.md:1371-1377): drain page 1 around the composer now, in parallel with the
    // POST below — the deadline is what beat two waits out, whichever finishes last.
    const clearDeadline = beginIdleToRunningClear(IDLE_STAGE_ID);
    try {
      const result = await startResearch(buildRequest(text, settings)); // one fetch; never retried
      noteMode(result.mode);
      clearUnreachable("composer");
      recordSubmission(result.data.session_id, settings, submittedBeatBudgetMs());
      void refreshSessions();
      // result.data.query, not `text`: replay's ReplayCaseMiddleware rewrites the request body's
      // query before the handler ever sees it, so the POST response already carries whatever
      // #submitted-h will end up showing — using the locally-typed text here would show the flight
      // box a different question than the one it lands on.
      await armIdleToRunningFlight({ sessionId: result.data.session_id, question: result.data.query, composerEl: document.getElementById("composer"), clearDeadline });
      router.push(`/research/${result.data.session_id}`);
    } catch (e) {
      // A failed POST restores the composer intact, with its error shown — nothing was ever handed
      // off to a flight that a route change would strand.
      cancelIdleToRunningClear(IDLE_STAGE_ID);
      // C1: "composer" is this tab's own key. Retry never re-POSTs (M2) — it only re-checks
      // reachability via the sidebar's own read; once that succeeds, this key clears too, so the
      // banner doesn't outlive the outage it reported just because a resubmit never happened.
      if (e instanceof ApiUnreachableError) noteUnreachable("composer", e.target, () => { void refreshSessions().then(() => clearUnreachable("composer")); });
      else if (e instanceof ApiError && e.status === 422) setError(`The service rejected the request: ${e.body.issues.map((i) => `${i.location} (${i.type})`).join(", ")}`);
      else if (e instanceof ApiError && e.body.code === "configuration_error") setError(configurationErrorText(e.body.reason));
      else if (e instanceof ApiError) setError(`${e.body.code}: ${e.body.message}`);
      else setError("The request could not be sent.");
    } finally { setBusy(false); }
  }
  const onSubmit = (e: FormEvent) => { e.preventDefault(); void submit(question); };
  // index.html:2381-2385 — any error (the empty-required message included) clears on the next keystroke.
  const onQuestionChange = (value: string) => { setQuestion(value); setError(null); setInvalid(false); };
  return (
    <>
      <div className="composer-wrap" id="composerIdleHost">
        <form className="composer" id="composer" noValidate onSubmit={onSubmit}>
          <label className="sr" htmlFor="prompt">Research question</label>
          <textarea id="prompt" rows={2} placeholder="Ask anything." value={question} ref={prompt}
            aria-invalid={invalid ? "true" : undefined} onChange={(e) => onQuestionChange(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void submit(question); } }} />
          <div className="composer-bar">
            <span className="pop-anchor">
              <button className="plus" id="plusBtn" type="button" aria-expanded={open} aria-controls="settingsPop" ref={plus} onClick={() => setOpen((o) => !o)}>
                <span className="sr">Run settings</span>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="square" aria-hidden="true"><path d="M12 5v14M5 12h14" /></svg>
              </button>
              <SettingsPopover open={open} settings={settings} onChange={setSettings} onClose={() => setOpen(false)} anchor={plus} />
            </span>
            <span className="setting-pill" id="pillModel"><span className="v" id="pillModelV">{shortModel(settings.model)}</span></span>
            <span className="setting-pill" id="pillThinking"><span className="k">thinking</span><span className="v" id="pillThinkingV">{settings.thinking}</span></span>
            <span className="setting-pill" id="pillExtra"><span className="k">extra passes</span><span className="v" id="pillExtraV">{settings.extraPasses}</span></span>
            <span className="spacer"></span>
            <button className="send" id="sendBtn" type="submit">
              <span className="sr">Start research</span>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="square" aria-hidden="true"><path d="M12 19V5M6 11l6-6 6 6" /></svg>
            </button>
          </div>
          <div className="composer-hint"><span className="hint err" id="composerError" role="alert">{error ?? ""}</span></div>
        </form>
      </div>
      <div className="starters">
        <p className="cap">Try</p>
        <div className="starter-list" id="starterList">
          {STARTERS.map((q) => (
            /* A starter is a complete question: it is submitted in the same click (index.html:2325-2333). */
            <button key={q} type="button" className="starter" data-question={q} onClick={() => { setQuestion(q); void submit(q); }}>
              <span className="arw" aria-hidden="true">→</span><span className="q">{q}</span>
            </button>
          ))}
        </div>
      </div>
    </>
  );
}
