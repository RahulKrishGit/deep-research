import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { NoteLine } from "../../components/NoteLine";
import { NOTES_CLOSED, NOTE_PLACEHOLDER, NOTE_SEND_FAILED } from "../../lib/notes";

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const refusal = (code: string) => json(409, { error: { code, message: "Refused.", reason: null, issues: [] } });
const field = () => screen.getByRole<HTMLInputElement>("textbox", { name: "Add a note for this research" });
const send = () => screen.getByRole<HTMLButtonElement>("button", { name: "Add note" });
const posts = (fetchMock: ReturnType<typeof vi.fn>) => fetchMock.mock.calls.filter(([, init]) => (init as RequestInit | undefined)?.method === "POST");
async function settle() { await act(async () => { await Promise.resolve(); await Promise.resolve(); }); }

afterEach(() => { vi.unstubAllGlobals(); });

describe("NoteLine — the quiet line at the foot of the pipeline card", () => {
  it("is a borderless field with its placeholder and a neutral icon send, never the purple primary", () => {
    const { container } = render(<NoteLine sessionId="s1" remaining={10} />);
    expect(field().className).toBe("tx");
    expect(field().placeholder).toBe(NOTE_PLACEHOLDER);
    expect(field().placeholder).toBe("Add a note — something to focus on, leave out or change");
    expect(send().className).toBe("icon-btn");
    expect(send().querySelector("svg path")!.getAttribute("d")).toBe("M8 13V3M3.5 7.5 8 3l4.5 4.5");
    expect(send().querySelector(".sr")!.textContent).toBe("Add note");
    expect(container.querySelector(".btn-primary")).toBeNull();
  });

  it("sends the trimmed note once on Enter, reads only while in flight, then clears", async () => {
    let answer: (r: Response) => void = () => {};
    const fetchMock = vi.fn(() => new Promise<Response>((resolve) => { answer = resolve; }));
    vi.stubGlobal("fetch", fetchMock);
    render(<NoteLine sessionId="s 1" remaining={10} />);
    fireEvent.change(field(), { target: { value: "  More on fire safety  " } });
    fireEvent.keyDown(field(), { key: "Enter" });
    fireEvent.keyDown(field(), { key: "Enter" });
    expect(posts(fetchMock)).toHaveLength(1);
    const [url, init] = posts(fetchMock)[0] as [string, RequestInit];
    expect(url).toBe("/api/research/s%201/notes");
    expect(JSON.parse(init.body as string)).toEqual({ text: "More on fire safety" });
    expect(field().readOnly).toBe(true);
    await act(async () => { answer(json(202, { note_id: "n1", status: "received" })); });
    await settle();
    expect(field().readOnly).toBe(false);
    expect(field().value).toBe("");
  });

  it("sends from the button too, and never sends an empty note", async () => {
    const fetchMock = vi.fn(async () => json(202, { note_id: "n1", status: "received" }));
    vi.stubGlobal("fetch", fetchMock);
    render(<NoteLine sessionId="s1" remaining={10} />);
    fireEvent.change(field(), { target: { value: "   " } });
    fireEvent.click(send());
    expect(posts(fetchMock)).toHaveLength(0);
    fireEvent.change(field(), { target: { value: "Only the US" } });
    fireEvent.click(send());
    await settle();
    expect(posts(fetchMock)).toHaveLength(1);
  });

  it("gives way to one caption once publishing has begun (409 notes_closed)", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => refusal("notes_closed")));
    const { container } = render(<NoteLine sessionId="s1" remaining={10} />);
    fireEvent.change(field(), { target: { value: "too late" } });
    fireEvent.keyDown(field(), { key: "Enter" });
    await settle();
    expect(container.querySelector("input")).toBeNull();
    expect(container.querySelector("#noteClosed")!.className).toBe("cap");
    expect(container.querySelector("#noteClosed")!.textContent).toBe(NOTES_CLOSED);
    expect(NOTES_CLOSED).toBe("Notes are closed — the report is being published");
  });

  it("announces the closed caption as a status and moves focus to it only when the note line had focus", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => refusal("notes_closed")));
    const first = render(<NoteLine sessionId="s1" remaining={10} />);
    field().focus();
    fireEvent.change(field(), { target: { value: "too late" } });
    fireEvent.keyDown(field(), { key: "Enter" });
    await settle();
    const caption = screen.getByRole("status");
    expect([caption.id, caption.textContent]).toEqual(["noteClosed", NOTES_CLOSED]);
    expect(caption.tabIndex).toBe(-1);
    expect(document.activeElement).toBe(caption);
    first.unmount();

    /* the send button had focus: it goes with the line, and focus follows to the caption too */
    const second = render(<NoteLine sessionId="s1" remaining={10} />);
    fireEvent.change(field(), { target: { value: "too late" } });
    send().focus();
    fireEvent.click(send());
    await settle();
    expect(document.activeElement).toBe(screen.getByRole("status"));
    second.unmount();

    /* the reader has moved on to something else: the caption appears without taking their place */
    const elsewhere = render(<><button type="button">Elsewhere</button><NoteLine sessionId="s1" remaining={10} /></>);
    fireEvent.change(field(), { target: { value: "too late" } });
    fireEvent.keyDown(field(), { key: "Enter" });
    screen.getByRole("button", { name: "Elsewhere" }).focus();
    await settle();
    expect(screen.getByRole("status").textContent).toBe(NOTES_CLOSED);
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Elsewhere" }));
    elsewhere.unmount();
  });

  it("disables the field and the send with no message and the same placeholder: none left, or a 409 note_limit_reached", async () => {
    const { container, unmount } = render(<NoteLine sessionId="s1" remaining={0} />);
    expect([field().disabled, send().disabled]).toEqual([true, true]);
    expect(field().placeholder).toBe(NOTE_PLACEHOLDER);
    expect(container.textContent).toBe("Add note");
    unmount();
    vi.stubGlobal("fetch", vi.fn(async () => refusal("note_limit_reached")));
    const again = render(<NoteLine sessionId="s1" remaining={1} />);
    fireEvent.change(field(), { target: { value: "an eleventh" } });
    fireEvent.keyDown(field(), { key: "Enter" });
    await settle();
    expect([field().disabled, send().disabled]).toEqual([true, true]);
    expect(field().placeholder).toBe(NOTE_PLACEHOLDER);
    expect(again.container.querySelector(".cap")).toBeNull();
  });

  it("keeps the text after any other failure — a 5xx or a network error — and says it couldn't send until the next edit", async () => {
    const fetchMock = vi.fn(async () => json(500, { error: { code: "internal_error", message: "Oops.", reason: null, issues: [] } }));
    vi.stubGlobal("fetch", fetchMock);
    const { container } = render(<NoteLine sessionId="s1" remaining={10} />);
    fireEvent.change(field(), { target: { value: "keep me" } });
    fireEvent.keyDown(field(), { key: "Enter" });
    await settle();
    expect(field().value).toBe("keep me");
    expect([field().readOnly, field().disabled]).toEqual([false, false]);
    const failed = screen.getByRole("status");
    expect([failed.id, failed.className, failed.textContent]).toEqual(["noteFailed", "cap", NOTE_SEND_FAILED]);
    expect(NOTE_SEND_FAILED).toBe("Couldn't send — try again");
    fireEvent.change(field(), { target: { value: "keep me, edited" } });
    expect(container.querySelector("#noteFailed")).toBeNull();
    fetchMock.mockImplementation(async () => { throw new TypeError("Failed to fetch"); });
    fireEvent.keyDown(field(), { key: "Enter" });
    await settle();
    expect(field().value).toBe("keep me, edited");
    expect(screen.getByRole("status").textContent).toBe(NOTE_SEND_FAILED);
    expect(container.querySelector("#noteClosed")).toBeNull();
  });
});
