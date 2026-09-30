"use client";
import type { SubmittedSettings } from "@/lib/session-store";

export function SettingsStrip({ settings, id }: { settings: SubmittedSettings | null; id?: string }) {
  if (!settings) {
    return (
      <div className="opts" id={id}>
        <span className="avail">settings as submitted: not recorded</span>
      </div>
    );
  }
  return (
    <div className="opts" id={id}>
      <span className="opt"><span className="k">model</span>{settings.model}</span>
      <span className="opt"><span className="k">thinking</span>{settings.thinking}</span>
      <span className="opt"><span className="k">effort</span>{settings.thinking === "enabled" ? "per agent" : "not sent"}</span>
      {settings.outputDir ? <span className="opt"><span className="k">out</span>{settings.outputDir}</span> : null}
    </div>
  );
}
