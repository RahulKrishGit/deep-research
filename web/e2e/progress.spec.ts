// notes-progress-report spec §6.3-§6.10 (AC14-AC18, AC20, AC21): each step's brief on the replay
// stream, held after a named event with X-Replay-Hold-After (§6.10 item 2), then stopped with Phase
// D's POST /stop so the next test starts on an idle server. The default case is
// missing-target-triggers-one-extra-pass: three topics, four sources, four findings, two parts.
import { expect, test, type APIRequestContext, type BrowserContext, type Page } from "@playwright/test";
import { API, submit } from "./support";

/* The browser's POST /research carries the header through the proxy's allowlist. */
const holdAt = (context: BrowserContext, hold: string) => context.setExtraHTTPHeaders({ "X-Replay-Hold-After": hold });
const stop = async (request: APIRequestContext, id: string) => expect((await request.post(`${API}/research/${id}/stop`)).status()).toBe(202);
const row = (page: Page, id: string) => page.locator(`#spine li[data-stage="${id}"]`);
const animations = (page: Page) => page.evaluate(() => document.getAnimations().map((a) => (a as CSSAnimation).animationName).filter(Boolean));

test("Planning: the status line, four skeleton slots that say 'drafting', the elapsed time (AC14)", async ({ page, context, request }) => {
  await holdAt(context, "planner.progress");
  const id = await submit(page, "q");
  const planning = row(page, "planner");
  await expect(planning.locator(".b-now.xf > [data-on='1']")).toHaveText("Drafting a plan for your question…", { timeout: 10_000 });
  await expect(planning.locator(".ps-slots > .ln")).toHaveCount(4);
  await expect(planning.locator(".ps-slots > .ln .sk[data-on='1']")).toHaveCount(4);
  await expect(planning.locator(".ps-slots > .ln .tf")).toHaveText(["drafting", "drafting", "drafting", "drafting"]);
  await expect(planning.locator(".m-live")).toHaveText(/^\dm \d\ds$/);
  await stop(request, id);
});

test("Planning: titles fill the slots and the check runs; the surplus skeleton leaves (AC14)", async ({ page, context, request }) => {
  await holdAt(context, "planner.progress#2");
  const id = await submit(page, "q");
  const planning = row(page, "planner");
  await expect(planning.locator(".b-now.xf > [data-on='1']")).toHaveText("Checking the plan covers everything you asked…", { timeout: 10_000 });
  await expect(planning.locator(".ps-slots > .ln:not([data-gone]) .tt")).toHaveText(["1Adoption rate", "2Widget funding", "3Widget exports"]);
  await expect(planning.locator(".ps-slots > .ln[data-topic='running']")).toHaveCount(3);
  await expect(planning.locator(".ps-slots > [data-gone='1']")).toBeHidden();
  // The leaving skeleton fades at once; it does not inherit the open row's per-line stagger (about 520 ms
  // for the fourth line). The built stylesheet writes 0ms as 0s.
  await expect(planning.locator(".ps-slots > [data-gone='1']")).toHaveCSS("--d-c", "0s");
  await stop(request, id);
});

test("Evaluating: the lead, the full bar and the Strong / Fair / Weak split once the batch lands (AC15)", async ({ page, context, request }) => {
  await holdAt(context, "source_evaluator.progress#2");
  const id = await submit(page, "q");
  const evaluating = row(page, "source_evaluator");
  await expect(evaluating).toHaveAttribute("data-open", "1", { timeout: 20_000 });
  await expect(evaluating.locator(".b-now")).toHaveText("Rating 4 sources for trustworthiness and relevance");
  await expect(evaluating.locator(".stat .eyebrow")).toHaveText(["Rated", "Strong", "Fair", "Weak"]);
  await expect(evaluating.locator(".stat .v")).toHaveText(["4 of 4", "4", "0", "0"]);
  await expect(evaluating.locator(".pb > i")).toHaveAttribute("style", /scaleX\(1\)/);
  await expect(evaluating.locator(".m-live")).toHaveText("4 of 4 rated");
  await stop(request, id);
});

test("Verifying: a real finding with its verdict and its source, the bar and the tally (AC16)", async ({ page, context, request }) => {
  await holdAt(context, "evidence_verifier.progress#2");
  const id = await submit(page, "q");
  const verifying = row(page, "evidence_verifier");
  await expect(verifying).toHaveAttribute("data-open", "1", { timeout: 20_000 });
  await expect(verifying.locator(".eyebrow")).toHaveText("Just checked");
  await expect(verifying.locator(".tickbox .xf > div[data-on='1'] .qt")).toHaveText("the Acme widget funding round in the United States was 12 million dollars in 2024");
  await expect(verifying.locator(".tickbox .xf > div[data-on='1'] .vd")).toHaveText("verified · an original report");
  await expect(verifying.locator(".tickbox .vd[data-kept='1']")).toHaveCount(1);
  await expect(verifying.locator(".b-facts")).toHaveText("4 of 4 checked · 4 verified · 0 corrected · 0 dropped");
  await expect(verifying.locator(".m-live")).toHaveText("4 of 4 checked");
  await stop(request, id);
});

test("Writing: the tally grows as the sections return; the placeholder waits for the first checked sentence (AC17)", async ({ page, context, request }) => {
  await holdAt(context, "report_writer.progress#3");
  const id = await submit(page, "q");
  const writing = row(page, "report_writer");
  await expect(writing).toHaveAttribute("data-open", "1", { timeout: 20_000 });
  await expect(writing.locator(".eyebrow")).toHaveText("Just written");
  await expect(writing.locator(".tickbox .b-sub")).toHaveText("The first sentences are being checked…");
  await expect(writing.locator(".b-facts")).toHaveText("0 of 4 sentences checked · ✓ 0 backed · ✗ 0 removed · section 2 of 2");
  await expect(writing.locator(".m-live")).toHaveText("2 of 2 sections written");
  await stop(request, id);
});

test("Writing: a drafted sentence with its Statement Check verdict and its section (AC17)", async ({ page, context, request }) => {
  await holdAt(context, "report_writer.progress#4");
  const id = await submit(page, "q");
  const writing = row(page, "report_writer");
  await expect(writing).toHaveAttribute("data-open", "1", { timeout: 20_000 });
  await expect(writing.locator(".tickbox .xf > div[data-on='1'] .qt")).toHaveText("Acme Institute 2 reports 12 million dollars for 2024.");
  await expect(writing.locator(".tickbox .xf > div[data-on='1'] .vd")).toHaveText("✓ backed by 1 finding · Widget funding");
  await expect(writing.locator(".b-facts")).toHaveText("2 of 4 sentences checked · ✓ 2 backed · ✗ 0 removed · section 2 of 2");
  await stop(request, id);
});

test("Reviewing: five checks land with no score; the status line waits for the route (AC18)", async ({ page, context, request }) => {
  await holdAt(context, "graph.report.reviewed");
  const id = await submit(page, "q");
  const reviewing = row(page, "report_reviewer");
  await expect(reviewing.locator(".b-now.xf > [data-on='1']")).toHaveText("Review done · deciding what happens next…", { timeout: 30_000 });
  await expect(reviewing.locator(".rv-list > .ln .tt")).toHaveText([
    "Covers your whole question", "Rests on strong evidence", "Every claim is credited correctly", "Honest about what is uncertain", "Easy to read",
  ]);
  await expect(reviewing.locator(".rv-list > .ln[data-topic='done']")).toHaveCount(5);
  await expect(reviewing.locator(".pb.ind")).toHaveAttribute("data-on", "0");
  await expect(reviewing.locator(".rv-notes-h")).toHaveCount(0);
  await expect(reviewing).not.toContainText(/\d\.\d\d/);
  await expect(reviewing.locator(".m-live")).toHaveText(/^reading the draft · \dm \d\ds$/);
  await stop(request, id);
});

test("a loop route holds Reviewing open on its checks and verdict, then hands over; Reviewing reopens (D39, AC18)", async ({ page, context, request }) => {
  await context.setExtraHTTPHeaders({ "X-Replay-Case": "scoped-redraft-after-a-named-defect", "X-Replay-Hold-After": "graph.route.decided" });
  const id = await submit(page, "q");
  const reviewing = row(page, "report_reviewer"), writing = row(page, "report_writer");
  await expect(reviewing.locator(".b-now.xf > [data-on='1']")).toHaveText("1 thing to fix · sending the draft back to the writer", { timeout: 30_000 });
  await expect(reviewing.locator(".rv-list > .ln[data-topic='fail'] .tt")).toHaveText(["Easy to read"]);
  await expect(writing).toHaveAttribute("data-open", "0");
  await page.waitForTimeout(2_300); // HANDOFF_HOLD_MS (2000 ms, components/BriefSpine.tsx) and a margin
  await expect(writing).toHaveAttribute("data-open", "1");
  await expect(reviewing).toHaveAttribute("data-open", "0");
  await reviewing.locator("button.ps-toggle").click();
  await expect(reviewing).toHaveAttribute("data-open", "1");
  await expect(reviewing.locator(".b-now.xf > [data-on='1']")).toHaveText("1 thing to fix · sending the draft back to the writer");
  await stop(request, id);
});

test.describe("the two loops (D21, AC20)", () => {
  test("the sheen runs only on Planning's skeleton slots, and the drift only while Reviewing's call runs", async ({ page, context, request }) => {
    await holdAt(context, "planner.progress");
    let id = await submit(page, "q");
    await expect(row(page, "planner").locator(".ps-slots .sk[data-on='1']")).toHaveCount(4, { timeout: 10_000 });
    await expect.poll(() => animations(page)).toContain("sheen");
    expect(await animations(page)).not.toContain("drift");
    await stop(request, id);

    await holdAt(context, "graph.node.started#6"); // the sixth start is Reviewing's (planner … report_reviewer)
    id = await submit(page, "q");
    await expect(row(page, "report_reviewer").locator(".pb.ind")).toHaveAttribute("data-on", "1", { timeout: 30_000 });
    await expect.poll(() => animations(page)).toContain("drift");
    expect(await animations(page)).not.toContain("sheen");
    await stop(request, id);

    await holdAt(context, "graph.report.reviewed");
    id = await submit(page, "q");
    await expect(row(page, "report_reviewer").locator(".pb.ind")).toHaveAttribute("data-on", "0", { timeout: 30_000 });
    await expect.poll(() => animations(page)).not.toContain("drift");
    expect(await animations(page)).not.toContain("sheen");
    await stop(request, id);
  });

  test.describe("under reduced motion", () => {
    test.use({ reducedMotion: "reduce" });
    test("neither loop runs: the skeleton bar is still and Reviewing's bar is a static full-width line", async ({ page, context, request }) => {
      await holdAt(context, "planner.progress");
      let id = await submit(page, "q");
      await expect(row(page, "planner").locator(".ps-slots .sk[data-on='1']")).toHaveCount(4, { timeout: 10_000 });
      await page.waitForTimeout(300);
      expect(await animations(page)).not.toContain("sheen");
      await stop(request, id);

      await holdAt(context, "graph.node.started#6");
      id = await submit(page, "q");
      const bar = row(page, "report_reviewer").locator(".pb.ind[data-on='1'] > i");
      await expect(bar).toHaveCount(1, { timeout: 30_000 });
      await page.waitForTimeout(300);
      expect(await animations(page)).not.toContain("drift");
      expect(await bar.evaluate((i) => [getComputedStyle(i).opacity, Math.round(i.getBoundingClientRect().width) === Math.round(i.parentElement!.getBoundingClientRect().width)]))
        .toEqual(["0.35", true]);
      await stop(request, id);
    });
  });
});

for (const [label, viewport] of [["1252×853", { width: 1252, height: 853 }], ["390×844", { width: 390, height: 844 }]] as const) {
  test.describe(label, () => {
    test.use({ viewport });
    test("no step's brief scrolls the page sideways (AC21)", async ({ page, context, request }) => {
      for (const [hold, stage] of [["planner.progress#2", "planner"], ["source_evaluator.progress#2", "source_evaluator"], ["evidence_verifier.progress#2", "evidence_verifier"], ["report_writer.progress#4", "report_writer"], ["graph.report.reviewed", "report_reviewer"]] as const) {
        await holdAt(context, hold);
        const id = await submit(page, "q");
        await expect(row(page, stage)).toHaveAttribute("data-open", "1", { timeout: 30_000 });
        await page.waitForTimeout(1_500); // the opening row's lines have risen
        const [scroll, inner] = await page.evaluate(() => [document.scrollingElement!.scrollWidth, window.innerWidth]);
        expect(scroll, `${hold}: no page-wide horizontal scroll`).toBeLessThanOrEqual(inner);
        await stop(request, id);
      }
    });
  });
}
