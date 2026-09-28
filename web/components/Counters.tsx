"use client";
import { COUNTER_ROWS, type Counters as CounterValues } from "@/lib/run-state";

export function Counters({ counters, absentText, pass, id = "runCounters" }: { counters: CounterValues; absentText: "not yet" | "not reached"; pass: number; id?: string }) {
  return (
    <div className="counters">
      <div className="row-between">
        <p className="eyebrow" style={{ margin: 0 }}>counted from the event stream</p>
        <span className="avail-mono" id="runCountersPass">pass {pass}</span>
      </div>
      <dl className="kv kv-2" id={id}>
        {COUNTER_ROWS.map((row) => {
          const v = row.value(counters);
          return [
            <dt key={`${row.key}-k`}>{row.label} <span className="cap">{row.scope}</span></dt>,
            v === null ? <dd key={row.key} data-counter={row.key} className="avail">{absentText}</dd>
              : typeof v === "object" ? <dd key={row.key} data-counter={row.key} className="avail">{v.muted}</dd>
              : <dd key={row.key} data-counter={row.key}>{v}</dd>,
          ];
        })}
      </dl>
    </div>
  );
}
