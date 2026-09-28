// C1: the banner used to hold a single { target, retry } pair — whichever component noted it
// last "owned" the slot, and any component's success cleared it for everyone. A page loaded
// while the API is down (SessionScreen) could sit behind a banner the sidebar's own later
// success silently dismissed, still stuck. The registry below is keyed by owner: the banner
// shows while any key is registered, and Retry repeats every registered retry, not just the
// last one noted.
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ConsoleProvider, useConsole } from "../../components/ConsoleProvider";

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
afterEach(() => vi.unstubAllGlobals());

function Harness({ retryA, retryB }: { retryA: () => void; retryB: () => void }) {
  const { unreachable, noteUnreachable, clearUnreachable } = useConsole();
  return (
    <div>
      <span data-testid="banner">{unreachable ? unreachable.target : "none"}</span>
      <button onClick={() => noteUnreachable("a", "http://x:1", retryA)}>note-a</button>
      <button onClick={() => noteUnreachable("b", "http://x:1", retryB)}>note-b</button>
      <button onClick={() => clearUnreachable("a")}>clear-a</button>
      <button onClick={() => clearUnreachable("b")}>clear-b</button>
      <button onClick={() => unreachable?.retry()}>retry</button>
    </div>
  );
}

describe("ConsoleProvider — keyed unreachable registry (C1)", () => {
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
    fireEvent.click(screen.getByText("clear-a"));
    // "b" is still registered: the banner must stay up. This is the exact C1 bug — a single
    // shared slot let one component's success hide another component's still-broken read.
    expect(screen.getByTestId("banner").textContent).toBe("http://x:1");
    fireEvent.click(screen.getByText("clear-b"));
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

  it("re-noting the same key replaces its retry without duplicating it in the run-every-retry set", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json(200, { sessions: [] })));
    const first = vi.fn();
    const second = vi.fn();
    render(<ConsoleProvider><Harness retryA={first} retryB={second} /></ConsoleProvider>);
    fireEvent.click(screen.getByText("note-a"));
    fireEvent.click(screen.getByText("note-a")); // same key "a", but this harness always passes `first`
    fireEvent.click(screen.getByText("retry"));
    expect(first).toHaveBeenCalledTimes(1);
  });
});
