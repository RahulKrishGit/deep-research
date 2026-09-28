"use client";
import { useEffect, useLayoutEffect, useRef, type RefObject } from "react";
import type { SubmittedSettings } from "@/lib/session-store";

export const MODELS = ["deepseek-flash", "deepseek-v4-flash", "deepseek-v4-pro"] as const; // product data (the design's three buttons)
export const EXTRA_MIN = 0, EXTRA_MAX = 2;
export const shortModel = (m: string) => m.replace(/^deepseek-/, "");
export const effortLine = (thinking: SubmittedSettings["thinking"]) =>
  thinking === "enabled" ? "effort per agent: planner max · reviewer max · others high" : "effort: not sent (thinking disabled)";

interface Props { open: boolean; settings: SubmittedSettings; onChange(next: SubmittedSettings): void; onClose(): void; anchor: RefObject<HTMLElement | null> }

export function SettingsPopover({ open, settings, onChange, onClose, anchor }: Props) {
  const pop = useRef<HTMLDivElement>(null);
  /* Where the panel can actually be read: above the bar when it fits, else below (index.html:2150-2189). */
  useLayoutEffect(() => {
    const el = pop.current;
    if (!open || !el) return;
    const place = () => {
      const bar = anchor.current?.closest(".composer-bar") ?? anchor.current;
      const r = bar?.getBoundingClientRect();
      const vh = window.innerHeight, vw = window.innerWidth;
      const top = r ? r.top : vh / 2, bottom = r ? r.bottom : top + 44, left = r ? r.left : 16;
      const GAP = 10, EDGE = 12;
      el.style.maxHeight = ""; el.style.top = ""; el.style.left = "";
      el.setAttribute("data-place", "above");
      const h = el.offsetHeight;
      const roomAbove = top - GAP - EDGE, roomBelow = vh - bottom - GAP - EDGE;
      if (h <= roomAbove || roomAbove >= roomBelow) { el.style.maxHeight = `${Math.max(EDGE, Math.round(roomAbove))}px`; return; }
      el.setAttribute("data-place", "below");
      const w = el.offsetWidth || 344;
      el.style.top = `${Math.round(bottom + GAP)}px`;
      el.style.left = `${Math.round(Math.max(EDGE, Math.min(left, vw - w - EDGE)))}px`;
      el.style.maxHeight = `${Math.max(EDGE, Math.round(roomBelow))}px`;
    };
    place();
    window.addEventListener("resize", place);
    return () => window.removeEventListener("resize", place);
  }, [open, anchor]);
  useEffect(() => {
    // index.html:1908-1912, :2386-2392 — Escape or a click outside (the anchor button excepted) closes the panel.
    if (!open) return;
    const onKeyDown = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    const onClickAway = (e: MouseEvent) => {
      const target = e.target as Node;
      if (pop.current?.contains(target) || anchor.current?.contains(target)) return;
      onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    document.addEventListener("click", onClickAway);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.removeEventListener("click", onClickAway);
    };
  }, [open, onClose, anchor]);
  const seg = (pressed: boolean) => ({ "aria-pressed": pressed } as const);
  return (
    <div className="popover" id="settingsPop" role="dialog" aria-label="Run settings" data-open={open ? "true" : "false"} ref={pop}>
      <div className="pop-head">
        <h2 className="card-title" style={{ fontSize: "var(--text-base)" }}>Run settings</h2>
        <button className="btn btn-quiet btn-sm" type="button" id="popClose" onClick={onClose}>Close</button>
      </div>
      <div className="pop-row">
        <span className="lbl" id="lblModel">Model</span>
        <div className="seg" role="group" aria-labelledby="lblModel" id="segModel">
          {MODELS.map((m) => <button key={m} type="button" data-model={m} {...seg(settings.model === m)} onClick={() => onChange({ ...settings, model: m })}>{shortModel(m)}</button>)}
        </div>
      </div>
      <div className="pop-row">
        <span className="lbl" id="lblThinking">Thinking</span>
        <div className="seg" role="group" aria-labelledby="lblThinking" id="segThinking">
          {(["enabled", "disabled"] as const).map((t) => <button key={t} type="button" data-thinking={t} {...seg(settings.thinking === t)} onClick={() => onChange({ ...settings, thinking: t })}>{t}</button>)}
        </div>
        <p className="pop-note" id="effortLine" aria-live="polite">{effortLine(settings.thinking)}</p>
      </div>
      <div className="pop-row">
        <label className="lbl" htmlFor="outputDir">Output directory</label>
        <input className="input mono-in" id="outputDir" value={settings.outputDir} onChange={(e) => onChange({ ...settings, outputDir: e.target.value })} />
      </div>
      <div className="pop-row">
        <span className="lbl" id="lblExtra">Extra passes</span>
        <div className="stepper" role="group" aria-labelledby="lblExtra" id="stepExtra">
          <button type="button" id="extraMinus" aria-label="Fewer extra passes" disabled={settings.extraPasses <= EXTRA_MIN} onClick={() => onChange({ ...settings, extraPasses: settings.extraPasses - 1 })}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="square" aria-hidden="true"><path d="M5 12h14" /></svg>
          </button>
          <span className="stepper-v mono" id="extraValue" role="status" aria-live="polite">{settings.extraPasses}</span>
          <button type="button" id="extraPlus" aria-label="More extra passes" disabled={settings.extraPasses >= EXTRA_MAX} onClick={() => onChange({ ...settings, extraPasses: settings.extraPasses + 1 })}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="square" aria-hidden="true"><path d="M12 5v14M5 12h14" /></svg>
          </button>
        </div>
      </div>
    </div>
  );
}
