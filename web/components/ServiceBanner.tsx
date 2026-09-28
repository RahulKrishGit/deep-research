// ServiceBanner.tsx — S4 "API unreachable": the sentence (I) and a Retry; nothing is disabled.
"use client";
export function ServiceBanner({ target, onRetry }: { target: string; onRetry: () => void }) {
  return (
    <div className="note bad" role="alert" data-od-id="service-banner" style={{ marginBottom: "var(--space-5)" }}>
      <div className="note-head"><span className="mk">service</span><span>Research service not reachable at {target}</span></div>
      <p className="sm">The console keeps working. Retry checks the service again.</p>
      <button className="btn btn-ghost btn-sm" type="button" onClick={onRetry}>Retry</button>
    </div>
  );
}
