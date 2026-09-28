import { Composer } from "@/components/Composer";

export default function IdlePage() {
  return (
    <section className="stage is-on" id="stage-idle" aria-labelledby="idle-h">
      <div className="stack" style={{ gap: "var(--space-8)", maxWidth: "var(--reading-max)", marginInline: "auto" }}>
        <div className="stack-2" style={{ paddingTop: "var(--space-8)" }}>
          <h1 className="display" id="idle-h">Ask anything.</h1>
          <p className="lead">
            A run takes a few minutes; harder questions take longer. You can leave — the run keeps going, and its
            report will be here when you return.
          </p>
        </div>
        <Composer />
      </div>
    </section>
  );
}
