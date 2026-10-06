// The unreachable-banner registry is keyed by owner: the banner shows while any key is registered,
// and Retry repeats every registered retry, not just the last one noted. A page loaded while the
// API is down (SessionScreen) therefore cannot sit behind a banner that the sidebar's own later
// success silently dismissed.
//
// In a real outage every owner registers at once (sidebar included), but only SessionScreen's own
// key has an automatic retry (its ladder). The sidebar's key has none — its 5 s poll only runs
// while the *last successfully loaded* list showed a running session, which a failed read can
// never produce. The banner disappears on the first success: when any owner's read succeeds (or
// gets a definite 404) and other keys are still registered, the provider gives each of them one
// more attempt right away, cascading until nothing is left broken.
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ConsoleProvider, useConsole } from "../../components/ConsoleProvider";

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
afterEach(() => vi.unstubAllGlobals());

function Harness({ retryA, retryB, retryA2 }: { retryA: () => void; retryB: () => void; retryA2?: () => void }) {
  const { unreachable, noteUnreachable, clearUnreachable } = useConsole();
  return (
    <div>
      <span data-testid="banner">{unreachable ? unreachable.target : "none"}</span>
      <button onClick={() => noteUnreachable("a", "http://x:1", retryA)}>note-a</button>
      <button onClick={() => noteUnreachable("a", "http://x:1", retryA2 ?? retryA)}>note-a-again</button>
      <button onClick={() => noteUnreachable("b", "http://x:1", retryB)}>note-b</button>
      <button onClick={() => clearUnreachable("a")}>clear-a</button>
      <button onClick={() => clearUnreachable("b")}>clear-b</button>
      <button onClick={() => unreachable?.retry()}>retry</button>
    </div>
  );
}

describe("ConsoleProvider — keyed unreachable registry", () => {
  it("shows the banner while any key is registered, and clears only once every key clears", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json(200, { sessions: [] })));
    const retryA = vi.fn();
    const retryB = vi.fn();
    render(<ConsoleProvider><Harness retryA={retryA} retryB={retryB} /></ConsoleProvider>);
    expect(screen.getByTestId("banner").textContent).toBe("none");
    fireEvent.click(screen.getByText("note-a"));
    expect(screen.getByTestId("banner").textContent).toBe("http://x:1");
    fireEvent.click(screen.getByText("note-b"));
    expect(screen.getByTestId("banner").textContent).toBe("http://x:1");
    // retryB would fire a cascade attempt here (see the cascade tests below); make it a no-op
    // clear so this test still proves the plain "both still registered" case on its own.
    expect(screen.getByTestId("banner").textContent).toBe("http://x:1");
    fireEvent.click(screen.getByText("clear-b"));
    expect(screen.getByTestId("banner").textContent).toBe("http://x:1"); // "a" is still registered
    fireEvent.click(screen.getByText("clear-a"));
    expect(screen.getByTestId("banner").textContent).toBe("none");
  });

  it("the banner's Retry runs every registered retry, not just the last one noted", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json(200, { sessions: [] })));
    const retryA = vi.fn();
    const retryB = vi.fn();
    render(<ConsoleProvider><Harness retryA={retryA} retryB={retryB} /></ConsoleProvider>);
    fireEvent.click(screen.getByText("note-a"));
    fireEvent.click(screen.getByText("note-b"));
    fireEvent.click(screen.getByText("retry"));
    expect(retryA).toHaveBeenCalledTimes(1);
    expect(retryB).toHaveBeenCalledTimes(1);
  });

  it("re-noting the same key replaces its retry: only the newer function ever runs", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json(200, { sessions: [] })));
    const first = vi.fn();
    const second = vi.fn();
    render(<ConsoleProvider><Harness retryA={first} retryA2={second} retryB={vi.fn()} /></ConsoleProvider>);
    fireEvent.click(screen.getByText("note-a"));
    fireEvent.click(screen.getByText("note-a-again")); // same key "a", a genuinely different function this time
    fireEvent.click(screen.getByText("retry"));
    expect(second).toHaveBeenCalledTimes(1);
    expect(first).not.toHaveBeenCalled();
  });

  it("clearing one key gives every other still-registered key one more attempt, with no click", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json(200, { sessions: [] })));
    const retryA = vi.fn();
    const retryB = vi.fn();
    render(<ConsoleProvider><Harness retryA={retryA} retryB={retryB} /></ConsoleProvider>);
    fireEvent.click(screen.getByText("note-a"));
    fireEvent.click(screen.getByText("note-b"));
    // "a"'s own read just succeeded (e.g. SessionScreen's ladder, or a 404) — nobody clicked Retry.
    fireEvent.click(screen.getByText("clear-a"));
    expect(retryB).toHaveBeenCalledTimes(1);
  });

  it("clearing the last key never calls a retry — there is nothing left to cascade to", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json(200, { sessions: [] })));
    const retryA = vi.fn();
    render(<ConsoleProvider><Harness retryA={retryA} retryB={vi.fn()} /></ConsoleProvider>);
    fireEvent.click(screen.getByText("note-a"));
    fireEvent.click(screen.getByText("clear-a"));
    expect(retryA).not.toHaveBeenCalled(); // the key that just cleared is never re-run on its own clear
  });
});
