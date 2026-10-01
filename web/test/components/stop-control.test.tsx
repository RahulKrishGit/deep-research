// notes-progress-report spec §8.5 (D17, D18, D24; Stop.dc.html columns 1-2): Stop and its one
// confirmation. The control is rendered on its own inside the provider; POST /stop is scripted per test.
import { act, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ConsoleProvider } from "../../components/ConsoleProvider";
import { StopControl } from "../../components/StopControl";
import type { ResearchSessionResponse } from "../../lib/api";

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", "x-deep-research-mode": "replay" } });
const STOPPED = { session_id: "s1", query: "q", status: "stopped", stopped_step: "researcher" } as unknown as ResearchSessionResponse;
const REFUSED = { error: { code: "not_stoppable", message: "Research session can no longer be stopped.", reason: "publishing", issues: [] } };
const text = (el: Element | null) => (el?.textContent ?? "").replace(/\s+/g, " ").trim();
afterEach(() => vi.unstubAllGlobals());

/* `answer` scripts POST /stop; every other request is the sidebar's list read. */
function mount(answer: () => Promise<Response> | Response) {
  const stops: RequestInit[] = [];
  let lists = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).endsWith("/stop")) { stops.push(init ?? {}); return answer(); }
    lists++;
    return json(200, { sessions: [] });
  }));
  const onStopped = vi.fn();
  render(<ConsoleProvider><button type="button" id="elsewhere">elsewhere</button><StopControl target={{ sessionId: "s1", onStopped }} /></ConsoleProvider>);
  return { stops, onStopped, lists: () => lists };
}
const stopBtn = () => document.getElementById("stopBtn") as HTMLButtonElement;
const dialog = () => document.querySelector('[role="dialog"]');
const button = (name: string) => [...document.querySelectorAll<HTMLButtonElement>(".stop-confirm button")].find((b) => b.textContent === name)!;

describe("StopControl (notes-progress-report spec §8.5; D18, D24)", () => {
  it("is a small ghost button with a square, closed until pressed", () => {
    mount(() => json(202, STOPPED));
    expect(stopBtn().className).toBe("btn btn-ghost btn-sm btn-stop");
    expect(text(stopBtn())).toBe("Stop");
    expect(stopBtn().querySelector(".stop-sq")!.getAttribute("aria-hidden")).toBe("true");
    expect([stopBtn().getAttribute("aria-haspopup"), stopBtn().getAttribute("aria-expanded")]).toEqual(["dialog", "false"]);
    expect(dialog()).toBeNull();
  });

  it("asks once, in the canvas's words, with Keep going focused", () => {
    mount(() => json(202, STOPPED));
    fireEvent.click(stopBtn());
    const box = dialog()!;
    expect([box.id, box.className, box.getAttribute("aria-labelledby")]).toEqual(["stopConfirm", "stop-confirm", "stopConfirmT"]);
    expect(text(document.getElementById("stopConfirmT"))).toBe("Stop this research?");
    expect(text(box.querySelector(".b-sub"))).toBe("It stops right away and nothing more is spent. What's done so far stays here, but no report is written.");
    expect([...box.querySelectorAll("button")].map((b) => [b.textContent, b.className])).toEqual([
      ["Keep going", "btn btn-quiet btn-sm"], ["Stop research", "btn btn-sm btn-danger"],
    ]);
    expect(document.activeElement).toBe(button("Keep going"));
    expect([stopBtn().getAttribute("aria-expanded"), stopBtn().getAttribute("aria-controls")]).toEqual(["true", "stopConfirm"]);
  });

  it("Keep going, Escape and a click outside each close it and give focus back to Stop, sending nothing", () => {
    const { stops } = mount(() => json(202, STOPPED));
    fireEvent.click(stopBtn());
    fireEvent.click(button("Keep going"));
    expect(dialog()).toBeNull();
    expect(document.activeElement).toBe(stopBtn());
    fireEvent.click(stopBtn());
    fireEvent.keyDown(document, { key: "Escape" });
    expect(dialog()).toBeNull();
    expect(document.activeElement).toBe(stopBtn());
    fireEvent.click(stopBtn());
    // The press outside is cancelled (`false`): a browser would otherwise move focus to what was
    // pressed, or to the page itself, after the handler gave it back to Stop (review round 1, I1).
    expect(fireEvent.mouseDown(document.getElementById("elsewhere")!)).toBe(false);
    expect(dialog()).toBeNull();
    expect(document.activeElement).toBe(stopBtn());
    expect(stops).toEqual([]);
  });

  it("Stop research posts once and disables both buttons while it waits; a 202 hands the stopped session over", async () => {
    let release!: (response: Response) => void;
    const { stops, onStopped, lists } = mount(() => new Promise<Response>((resolve) => { release = resolve; }));
    await waitFor(() => expect(lists()).toBe(1));
    fireEvent.click(stopBtn());
    fireEvent.click(button("Stop research"));
    expect([button("Keep going").disabled, button("Stop research").disabled]).toEqual([true, true]);
    fireEvent.keyDown(document, { key: "Escape" });
    fireEvent.mouseDown(document.getElementById("elsewhere")!);
    expect(dialog()).not.toBeNull();
    await act(async () => { release(json(202, STOPPED)); });
    await waitFor(() => expect(onStopped).toHaveBeenCalledWith(STOPPED));
    await waitFor(() => expect(dialog()).toBeNull());
    expect(stops.map((init) => [init.method, init.body])).toEqual([["POST", undefined]]);
    await waitFor(() => expect(lists()).toBe(2));
  });

  it("a 409 says it is too late, with Close focused, and hands nothing over", async () => {
    const { onStopped } = mount(() => json(409, REFUSED));
    fireEvent.click(stopBtn());
    fireEvent.click(button("Stop research"));
    await waitFor(() => expect(text(document.getElementById("stopTooLate"))).toBe("Too late to stop — the research is finishing."));
    expect(text(document.getElementById("stopConfirmT"))).toBe("Stop this research?");
    expect([...dialog()!.querySelectorAll("button")].map((b) => b.textContent)).toEqual(["Close"]);
    // Focus follows the face in an effect, which runs after the render that shows it.
    await waitFor(() => expect(document.activeElement).toBe(button("Close")));
    fireEvent.click(button("Close"));
    expect(dialog()).toBeNull();
    expect(document.activeElement).toBe(stopBtn());
    expect(onStopped).not.toHaveBeenCalled();
  });

  it("any other failure says so and lets the reader try again, one POST per press", async () => {
    let calls = 0;
    const { stops, onStopped } = mount(() => {
      calls++;
      return calls === 1 ? json(500, { error: { code: "http_500", message: "x", reason: null, issues: [] } }) : json(202, STOPPED);
    });
    fireEvent.click(stopBtn());
    fireEvent.click(button("Stop research"));
    await waitFor(() => expect(text(document.getElementById("stopFailed"))).toBe("Couldn't stop — try again"));
    expect(document.getElementById("stopFailed")!.getAttribute("role")).toBe("alert");
    expect([button("Keep going").disabled, button("Stop research").disabled]).toEqual([false, false]);
    await waitFor(() => expect(document.activeElement).toBe(button("Stop research")));
    fireEvent.click(button("Stop research"));
    await waitFor(() => expect(onStopped).toHaveBeenCalledTimes(1));
    expect(stops).toHaveLength(2);
  });
});
