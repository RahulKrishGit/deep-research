// live-briefs spec §4.5 and §4.8 (D5-D7; AC10-AC12): the one-time check on the replay server.
// Replay's scripted checker asks its three fixed questions only when POST /research carried
// X-Replay-Clarify: on (api/clarify.py), so every other spec sees no check at all.
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { API, clarifyRecord, installClarifyRecorder, submit, waitTerminal } from "./support";

const ALL_BEST_GUESSES = "Starting research with: Region: Global (best guess) · Period: Since 2023 (best guess) · For: General understanding (best guess)";
const streamText = async (request: APIRequestContext, id: string) => (await request.get(`${API}/research/${id}/stream`)).text();
const top = (page: Page, selector: string) => page.locator(selector).evaluate((el) => el.getBoundingClientRect().top);

test("a clear question skips the check: no card and no needs_input (AC10)", async ({ page, request }) => {
  await installClarifyRecorder(page);
  const id = await submit(page, "q");
  await expect(page.locator("#stage-running")).toBeVisible({ timeout: 10_000 });
  await waitTerminal(request, id);
  expect((await clarifyRecord(page)).stage).toBe(false);
  expect(await streamText(request, id)).not.toContain("session.clarification");
});

test("the check asks one question at a time, posts the answers once and the run starts on them (AC11)", async ({ page, context, request }) => {
  await installClarifyRecorder(page);
  await context.setExtraHTTPHeaders({ "X-Replay-Clarify": "on" });
  const posts: string[] = [];
  page.on("request", (r) => { if (r.method() === "POST" && /\/api\/research\/[0-9a-f]+\/answers$/.test(r.url())) posts.push(r.postData() ?? ""); });
  const id = await submit(page, "q");
  const card = page.locator("#clarifyCard");
  await expect(card).toBeVisible({ timeout: 10_000 });
  expect((await (await request.get(`${API}/research/${id}/status`)).json()).status).toBe("needs_input");
  await expect(page.locator("#clarifyStep")).toHaveText("Question 1 of 3");
  await expect(page.locator("#topbarStatus .chip")).toHaveText("Waiting for you · a few quick questions");
  await expect(page.locator("#topbarStatus .chip .dot")).toHaveClass(/dot-warn/);
  await expect(page.locator(`.sb-item[data-session="${id}"]`)).toHaveAttribute("data-run", "1");
  await expect(card.locator(".btn-primary")).toHaveCount(0);
  await card.getByRole("button", { name: "United States" }).click();
  await expect(page.locator("#clarifyStep")).toHaveText("Question 2 of 3");
  await card.getByRole("button", { name: "Other…" }).click();
  await page.getByLabel("Your own answer").fill("since 2021");
  await page.getByLabel("Your own answer").press("Enter");
  await expect(page.locator("#clarifyStep")).toHaveText("Question 3 of 3");
  await card.getByRole("button", { name: "Skip this one" }).click();
  await expect(page.locator("#stage-running")).toBeVisible({ timeout: 10_000 });
  expect(posts).toHaveLength(1);
  expect(JSON.parse(posts[0])).toEqual({ answers: [{ question_id: "q1", choice: "United States" }, { question_id: "q2", text: "since 2021" }], skip: false });
  expect((await clarifyRecord(page)).summaries).toContain(
    "Starting research with: Region: United States (you said) · Period: since 2021 (you said) · For: General understanding (best guess)",
  );
  await waitTerminal(request, id);
  expect(await streamText(request, id)).toContain('"reason":"answered"');
});

test("with no answer the check starts on best guesses when its wait ends (AC12)", async ({ page, request }) => {
  await installClarifyRecorder(page);
  // The wait starts when the POST lands, so the app is warmed first: the card must still be asking
  // when the page gets there. 8 s leaves several seconds of margin on a slow VM.
  await page.goto("/");
  const created = await request.post(`${API}/research`, { headers: { "X-Replay-Clarify": "on" }, data: { query: "q", config_overrides: { hitl: { answer_wait_s: 8 } } } });
  const id = ((await created.json()) as { session_id: string }).session_id;
  await page.goto(`/research/${id}`);
  await expect(page.locator("#clarifyCountdown")).toHaveText(/^Starts with best guesses in 0:0[0-8] if you don't answer$/, { timeout: 10_000 });
  await expect(page.locator("#stage-running")).toBeVisible({ timeout: 20_000 });
  expect((await clarifyRecord(page)).summaries.at(-1)).toBe(ALL_BEST_GUESSES);
  await waitTerminal(request, id);
  expect(await streamText(request, id)).toContain('"reason":"timed_out"');
});

test("the check follows the Submitted beat and the question never moves across either hand-off (§4.5)", async ({ page, context, request }) => {
  await context.setExtraHTTPHeaders({ "X-Replay-Clarify": "on" });
  const id = await submit(page, "What limits grid-scale battery storage?");
  // Measured once the Submitted beat's header has settled (its reveal rises 6px, globals.css:220-224).
  await expect(page.locator("#stage-submitted")).toHaveClass(/is-revealing/);
  await page.locator("#submitted-h").evaluate((el) => Promise.all(el.getAnimations().map((a) => a.finished)).then(() => null));
  const submitted = await top(page, "#submitted-h");
  await expect(page.locator("#stage-clarify")).toHaveClass(/is-arriving/, { timeout: 10_000 });
  await expect(page.locator("#clarifyCard")).toBeVisible();
  await expect(page.locator(".q-flight")).toHaveCount(0);
  expect(Math.abs((await top(page, "#clarify-h")) - submitted)).toBeLessThanOrEqual(1);
  await page.locator("#clarifyCard").getByRole("button", { name: "Just start" }).click();
  await expect(page.locator("#stage-running")).toBeVisible({ timeout: 10_000 });
  expect(Math.abs((await top(page, "#running-h")) - submitted)).toBeLessThanOrEqual(1);
  await waitTerminal(request, id);
  expect(await streamText(request, id)).toContain('"reason":"skipped"');
});

test("a reload while the check waits rebuilds the card from the stream (§4.8)", async ({ page, context, request }) => {
  await context.setExtraHTTPHeaders({ "X-Replay-Clarify": "on" });
  const id = await submit(page, "q");
  await expect(page.locator("#clarifyCard")).toBeVisible({ timeout: 10_000 });
  await page.reload();
  await expect(page.locator("#clarifyStep")).toHaveText("Question 1 of 3");
  await expect(page.locator("#stage-submitted")).toHaveCount(0);
  await page.locator("#clarifyCard").getByRole("button", { name: "Just start" }).click();
  await expect(page.locator("#stage-running")).toBeVisible({ timeout: 10_000 });
  await waitTerminal(request, id);
});

test.describe("reduced motion", () => {
  test.use({ reducedMotion: "reduce" });
  test("the next question fades in place, with no travel (§4.8)", async ({ page, context, request }) => {
    await context.setExtraHTTPHeaders({ "X-Replay-Clarify": "on" });
    const id = await submit(page, "q");
    await expect(page.locator("#clarifyCard")).toBeVisible({ timeout: 10_000 });
    await page.locator("#clarifyCard").getByRole("button", { name: "United States" }).click();
    await expect(page.locator("#clarifyStep")).toHaveText("Question 2 of 3");
    const animations = await page.locator("#clarifyCard .ck-q").evaluate((el) => el.getAnimations().map((a) => ({
      name: (a as CSSAnimation).animationName,
      duration: a.effect!.getTiming().duration,
      props: (a.effect as KeyframeEffect).getKeyframes().flatMap((k) => Object.keys(k)),
    })));
    expect(animations).toHaveLength(1);
    expect(animations[0].name).toBe("enter");
    expect(animations[0].duration).toBe(160);
    expect(animations[0].props).toContain("opacity");
    expect(animations[0].props).not.toContain("transform");
    await page.locator("#clarifyCard").getByRole("button", { name: "Just start" }).click();
    await waitTerminal(request, id);
  });
});

test.describe("390×844", () => {
  test.use({ viewport: { width: 390, height: 844 } });
  test("the card and its answers are full width, with no sideways scroll (§4.8)", async ({ page, context, request }) => {
    await context.setExtraHTTPHeaders({ "X-Replay-Clarify": "on" });
    const id = await submit(page, "q");
    await expect(page.locator("#clarifyCard")).toBeVisible({ timeout: 10_000 });
    const layout = await page.evaluate(() => {
      const card = document.getElementById("clarifyCard")!.getBoundingClientRect();
      const wrap = document.querySelector("#stage-clarify .run-wrap")!.getBoundingClientRect();
      const list = document.querySelector("#clarifyCard .choices")!.getBoundingClientRect();
      const widths = [...document.querySelectorAll("#clarifyCard .choice")].map((b) => b.getBoundingClientRect().width);
      return { card: card.width, wrap: wrap.width, list: list.width, widths, scroll: document.scrollingElement!.scrollWidth, inner: window.innerWidth };
    });
    expect(Math.abs(layout.card - layout.wrap)).toBeLessThanOrEqual(1);
    expect(layout.widths).toHaveLength(4);
    for (const width of layout.widths) expect(Math.abs(width - layout.list)).toBeLessThanOrEqual(1);
    expect(layout.scroll).toBeLessThanOrEqual(layout.inner);
    await page.locator("#clarifyCard").getByRole("button", { name: "Just start" }).click();
    await waitTerminal(request, id);
  });
});
