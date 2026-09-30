// live-briefs spec §4.6-§4.8 (D8, D9, D11a; AC15, AC19, AC20): reader notes on the replay server.
// Replay's interpreter restates a note as written, as an emphasis. Replay runs the graph at full
// speed and paces only the stream, so a note the page sends reaches the run's board after the
// engine has finished: these specs prove the note's flow through the API and the page; the engine's
// use of a note is proven offline by pytest (tests/test_graph/test_reader_notes_replay.py).
import { expect, test, type Page } from "@playwright/test";
import { API, installMotionRecorder, installNoteRecorder, motion, noteRecord, submit, waitTerminal } from "./support";

const QUESTION = "What is the current state of grid-scale battery storage?";
const PLACEHOLDER = "Add a note — something to focus on, leave out or change";
const field = (page: Page) => page.getByLabel("Add a note for this research");
/* Researching is the running row and its hand-off from Planning has settled. */
const researching = (page: Page) => page.locator('#spine li[data-stage="researcher"][data-state="active"]:not([data-handoff])').waitFor({ timeout: 15_000 });
async function note(page: Page, text: string) {
  await field(page).fill(text);
  await field(page).press("Enter");
  await expect(field(page)).toHaveValue("");
}

test.beforeEach(async ({ context }) => {
  // The longest case: its paced stream keeps the run going long enough to add notes to it.
  await context.setExtraHTTPHeaders({ "X-Replay-Case": "missing-target-triggers-one-extra-pass" });
});

test("a note sent while Researching is received, read and acknowledged in the running row within 1 s (AC15)", async ({ page, request }) => {
  await installNoteRecorder(page);
  await installMotionRecorder(page);
  const id = await submit(page, QUESTION);
  await researching(page);
  const card = page.locator("#stage-running .card");
  await expect(card.locator("> *").last()).toHaveId("noteLine");
  await expect(card.locator(".btn-primary")).toHaveCount(0);
  await note(page, "More on fire-safety standards");
  const ack = page.locator('#spine li[data-state="active"] .ack', { hasText: "Got it — More on fire-safety standards, from each topic's next search" });
  await expect(ack).toBeVisible();
  await expect(ack.locator(".said")).toHaveText("More on fire-safety standards");
  await expect.poll(async () => (await noteRecord(page)).acks.length).toBe(1);
  const record = await noteRecord(page);
  // Timed from the send, which the interpreted event can only follow: an upper bound on AC15's measure.
  expect(record.acks[0].at - record.sent[0]).toBeLessThan(1000);
  // It rises in over 200 ms, with no wait, in the row that is already open.
  const rise = (await motion(page)).filter((m) => m.part === "ack");
  expect(rise.map((m) => [m.prop, m.delay, m.duration]).sort()).toEqual([["opacity", 0, 200], ["transform", 0, 200]]);
  await waitTerminal(request, id);
  const stream = await (await request.get(`${API}/research/${id}/stream`)).text();
  const received = stream.indexOf("event: session.note.received"), read = stream.indexOf("event: session.note.interpreted");
  expect(received).toBeGreaterThan(-1);
  expect(read).toBeGreaterThan(received);
  expect(stream).toContain('"restatement":"More on fire-safety standards","kinds":["emphasis"],"replaces":null,"fallback":false');
});

test("under reduced motion an acknowledgement fades in place, with no travel (§4.8)", async ({ page, request }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await installMotionRecorder(page);
  const id = await submit(page, QUESTION);
  await researching(page);
  await note(page, "Leave out pumped hydro");
  await expect(page.locator(".ack", { hasText: "Got it — Leave out pumped hydro" })).toBeVisible();
  const fade = (await motion(page)).filter((m) => m.part === "ack");
  expect(fade.map((m) => [m.prop, m.delay, m.duration])).toEqual([["opacity", 0, 160]]);
  await waitTerminal(request, id);
});

test("after the tenth note the line is disabled with nothing added, and an eleventh is refused (AC20, D11a)", async ({ page, request }) => {
  const id = await submit(page, QUESTION);
  await researching(page);
  for (let k = 1; k <= 9; k++) expect((await request.post(`${API}/research/${id}/notes`, { data: { text: `note ${k}` } })).status()).toBe(202);
  await note(page, "the tenth note");
  await expect(field(page)).toBeDisabled();
  await expect(page.getByRole("button", { name: "Add note" })).toBeDisabled();
  await expect(field(page)).toHaveAttribute("placeholder", PLACEHOLDER);
  await expect(page.locator("#noteLine")).toHaveText("Add note");
  const eleventh = await request.post(`${API}/research/${id}/notes`, { data: { text: "one too many" } });
  expect(eleventh.status()).toBe(409);
  expect((await eleventh.json()).error.code).toBe("note_limit_reached");
  await expect(page.locator("#spine .ack")).toHaveText([/^Got it — note 9/, /^Got it — the tenth note/, "and 8 earlier notes"]);
  expect((await (await request.get(`${API}/research/${id}/status`)).json()).notes_remaining).toBe(0);
  await waitTerminal(request, id);
});

test("once publishing has begun a note is refused and one caption takes the line's place (AC19, §4.8)", async ({ page, request }) => {
  await installNoteRecorder(page);
  const id = await submit(page, QUESTION);
  await page.locator('#spine li[data-stage="finalize_report"][data-state="active"]').waitFor({ timeout: 60_000 });
  await field(page).fill("Is it too late?");
  await field(page).press("Enter");
  await expect.poll(async () => (await noteRecord(page)).closed).toEqual(["Notes are closed — the report is being published"]);
  await waitTerminal(request, id);
  const after = await request.post(`${API}/research/${id}/notes`, { data: { text: "after the end" } });
  expect(after.status()).toBe(409);
  expect((await after.json()).error.code).toBe("notes_closed");
  expect((await (await request.get(`${API}/research/${id}/status`)).json()).notes).toEqual([]);
});

test("the report lists every note above the prose, each with its outcome (AC19)", async ({ page, request }) => {
  const id = await submit(page, QUESTION);
  await researching(page);
  await note(page, "More on fire-safety standards");
  expect((await request.post(`${API}/research/${id}/notes`, { data: { text: "Only the United States" } })).status()).toBe(202);
  await waitTerminal(request, id);
  const card = page.locator("#stage-report article.card.stack");
  await expect(card.locator("> section.reader-notes")).toBeVisible({ timeout: 20_000 });
  expect(await card.evaluate((el) => [...el.children].map((c) => c.className))).toEqual(["reader-notes", "prose"]);
  await expect(card.locator(".reader-notes h2")).toHaveText("Your notes");
  // Replay's engine finished before these notes arrived, so no review judged them.
  await expect(card.locator(".reader-notes li")).toHaveText(["More on fire-safety standards not checked", "Only the United States not checked"]);
  const status = await (await request.get(`${API}/research/${id}/status`)).json();
  expect(status.notes).toEqual([
    { note_id: "n1", text: "More on fire-safety standards", restatement: "More on fire-safety standards", outcome: "pending" },
    { note_id: "n2", text: "Only the United States", restatement: "Only the United States", outcome: "pending" },
  ]);
  expect([status.notes_remaining, status.note_passes]).toEqual([8, 0]);
});

test.describe("at phone width", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test("the note line spans the card, its send is a 44 px target, and nothing scrolls sideways (§4.8)", async ({ page, request }) => {
    const id = await submit(page, QUESTION);
    await researching(page);
    await note(page, "More on fire-safety standards");
    await expect(page.locator(".ack", { hasText: "Got it" })).toBeVisible();
    const geometry = await page.evaluate(() => {
      const card = document.querySelector<HTMLElement>("#stage-running .card")!;
      const line = document.getElementById("noteLine")!.getBoundingClientRect(), send = document.getElementById("noteSend")!.getBoundingClientRect();
      const box = card.getBoundingClientRect(), cs = getComputedStyle(card);
      const inner = box.width - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight) - parseFloat(cs.borderLeftWidth) - parseFloat(cs.borderRightWidth);
      return { line: line.width, inner, send: [send.width, send.height], scroll: document.scrollingElement!.scrollWidth - window.innerWidth };
    });
    expect(Math.abs(geometry.line - geometry.inner)).toBeLessThanOrEqual(1);
    expect(geometry.send).toEqual([44, 44]);
    expect(geometry.scroll).toBeLessThanOrEqual(0);
    await waitTerminal(request, id);
  });
});
