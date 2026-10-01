// notes-progress-report spec §7.5-§7.6 (D16, D28, D32; AC27): the report as section cards with a
// contents list, on the replay server. The default case (missing-target-triggers-one-extra-pass)
// publishes seven sections: the bottom line, three topics, Key figures, What we couldn't confirm and
// Sources. Replay never applies a reader note (api-gaps 3.9), so the note lines' look is checked on a
// report served through a route.
import { expect, test, type Page } from "@playwright/test";
import { submit, waitTerminal } from "./support";

const CARD_IDS = ["rep-bottom-line", "rep-topic-1", "rep-topic-2", "rep-topic-3", "rep-key-figures", "rep-not-confirmed", "rep-sources"];
const LABELS = ["Bottom line", "Adoption rate", "Widget funding", "Widget exports", "Key figures", "Not confirmed", "Sources"];
const noSideScroll = async (page: Page) => expect(await page.evaluate(() => document.scrollingElement!.scrollWidth <= window.innerWidth)).toBe(true);
const contents = (page: Page) => page.locator('nav.rep-contents[aria-label="Report contents"]');
const current = (page: Page) => contents(page).locator('a[aria-current="true"] .rc-l');
async function openReport(page: Page, request: Parameters<typeof waitTerminal>[0]) {
  const id = await submit(page, "q");
  await waitTerminal(request, id);
  await expect(page.locator("#rep-bottom-line")).toBeVisible({ timeout: 20_000 });
  return id;
}

test.describe("at 1252 px", () => {
  test.use({ viewport: { width: 1252, height: 853 }, reducedMotion: "reduce" });

  test("each section is its own card beside the Review rail, with a contents row of chips and no Your notes block", async ({ page, request }) => {
    await openReport(page, request);
    await expect(page.locator(".rep-cards > section.card.rsec")).toHaveCount(7);
    expect(await page.locator(".rep-cards > section.card.rsec").evaluateAll((cards) => cards.map((card) => card.id))).toEqual(CARD_IDS);
    await expect(page.locator(".rsec .rsec-eb")).toHaveText(["Topic 1 of 3", "Topic 2 of 3", "Topic 3 of 3"]);
    await expect(page.locator("#stage-report .reader-notes")).toHaveCount(0);
    await expect(page.locator(".report-main .rail")).toBeVisible();
    await expect(page.locator(".rep-layout")).toHaveAttribute("data-contents", "chips");
    await expect(contents(page).locator("a .rc-l")).toHaveText(LABELS);
    await expect(contents(page).locator("a .tn")).toHaveText(["", "1", "2", "3", "", "", ""]);
    await expect(current(page)).toHaveText("Bottom line");
    expect(await contents(page).evaluate((nav) => [getComputedStyle(nav).position, getComputedStyle(nav).overflowX])).toEqual(["sticky", "auto"]);
    const nav = (await contents(page).boundingBox())!, first = (await page.locator("#rep-bottom-line").boundingBox())!;
    expect(nav.y + nav.height).toBeLessThanOrEqual(first.y);
    await noSideScroll(page);
  });

  test("a click jumps to its card and marks it; the current entry then follows the reader's scroll", async ({ page, request }) => {
    await openReport(page, request);
    await contents(page).locator('a[href="#rep-topic-2"]').click();
    await expect(current(page)).toHaveText("Widget funding");
    await expect(page.locator("#rep-topic-2-h")).toBeFocused();
    // The card's top lands on the line the current card is read from: var(--topbar) + 56px + var(--space-4).
    await expect.poll(() => page.locator("#rep-topic-2").evaluate((card) => Math.round(card.getBoundingClientRect().top))).toBe(128);
    // The reader's own scrolling (a wheel) releases the click's hold, and the line rule takes over.
    await page.mouse.move(700, 500);
    await page.mouse.wheel(0, -20_000);
    await expect(current(page)).toHaveText("Bottom line");
    const top = await page.locator("#rep-topic-3").evaluate((card) => card.getBoundingClientRect().top);
    await page.mouse.wheel(0, top - 100);
    await expect(current(page)).toHaveText("Widget exports");
    await noSideScroll(page);
  });

  test("a run that answered the one-time check shows its answers on the evidence line; Key figures keep their Source column", async ({ page, request, context }) => {
    await context.setExtraHTTPHeaders({ "X-Replay-Clarify": "on" });
    const id = await submit(page, "q");
    await page.locator("#clarifyCard").getByRole("button", { name: "Just start" }).click({ timeout: 10_000 });
    await waitTerminal(request, id);
    await expect(page.locator("#rep-bottom-line")).toBeVisible({ timeout: 20_000 });
    await expect(page.locator("#reportEvidence")).toHaveText(/^Evidence as of \d{4}-\d{2}-\d{2} · \d+ sources? · Global · Since 2023 · General understanding$/);
    const figures = page.locator("#rep-key-figures table.tbl");
    await expect(figures.locator("th.kf-source")).toBeVisible();
    await expect(figures.locator(".kf-src").first()).toBeHidden();
  });
});

for (const [width, mode] of [[1920, "rail"], [1568, "chips"], [1252, "chips"]] as const) {
  test.describe(`at ${width} px with the sidebar expanded`, () => {
    test.use({ viewport: { width, height: 853 }, reducedMotion: "reduce" });

    test(`the contents list is ${mode === "rail" ? "a rail left of the cards" : "a row of chips above the cards"} (D28)`, async ({ page, request }) => {
      await openReport(page, request);
      await expect(page.locator("#app")).toHaveAttribute("data-sidebar", "expanded");
      await expect(page.locator(".rep-layout")).toHaveAttribute("data-contents", mode);
      const nav = (await contents(page).boundingBox())!, first = (await page.locator("#rep-bottom-line").boundingBox())!;
      if (mode === "rail") {
        await expect(contents(page).locator(".rc-h")).toHaveText("Contents");
        expect(Math.round(nav.width)).toBe(176);
        expect(Math.abs(nav.x + nav.width + 32 - first.x)).toBeLessThanOrEqual(1);
        expect(Math.round(first.width)).toBe(770);
      } else {
        await expect(contents(page).locator(".rc-h")).toHaveCount(0);
        expect(nav.y + nav.height).toBeLessThanOrEqual(first.y);
      }
      await noSideScroll(page);
    });
  });
}

test.describe("at 390 px", () => {
  test.use({ viewport: { width: 390, height: 844 }, reducedMotion: "reduce" });

  test("chips scroll sideways, the evidence line reads '{date} · {n} sources' and Key figures show their Source under What", async ({ page, request, context }) => {
    await context.setExtraHTTPHeaders({ "X-Replay-Clarify": "on" });
    const id = await submit(page, "q");
    await page.locator("#clarifyCard").getByRole("button", { name: "Just start" }).click({ timeout: 10_000 });
    await waitTerminal(request, id);
    await expect(page.locator("#rep-bottom-line")).toBeVisible({ timeout: 20_000 });
    await expect(page.locator(".rep-layout")).toHaveAttribute("data-contents", "chips");
    await expect(page.locator("#reportEvidence .ev-ans")).toHaveText([" · Global", " · Since 2023", " · General understanding"]);
    expect(await page.locator("#reportEvidence").evaluate((line) => (line as HTMLElement).innerText)).toMatch(/^\d{4}-\d{2}-\d{2} · \d+ sources?$/);
    const figures = page.locator("#rep-key-figures table.tbl");
    await expect(figures.locator("th.kf-source")).toBeHidden();
    await expect(figures.locator("tbody tr").first().locator("td").first().locator(".kf-src")).toBeVisible();
    expect(await contents(page).evaluate((nav) => nav.scrollWidth > nav.clientWidth)).toBe(true);
    await contents(page).getByRole("link", { name: "Sources" }).click();
    await expect(current(page)).toHaveText("Sources");
    expect(await contents(page).evaluate((nav) => {
      const chip = nav.querySelector<HTMLElement>('a[aria-current="true"]')!;
      return chip.offsetLeft >= nav.scrollLeft && chip.offsetLeft + chip.offsetWidth <= nav.scrollLeft + nav.clientWidth;
    })).toBe(true);
    await noSideScroll(page);
  });
});

// Replay finishes its run before a note the page sends can reach it (api-gaps 3.9), so the bottom
// line's note lines are served here: the report and its outline, exactly as the API would publish
// them for a run that took three notes.
const NOTED_REPORT = `# q

Evidence as of 2026-09-16 · 2 sources

## Bottom line

Acme Institute 17 reports 40 percent for 2024 [1].

- **Adoption rate:** Acme Institute 17 reports 40 percent for 2024 [1].
- **Your note · recycling:** ✓ Independent Bureau 2 reports 12 million dollars for 2024 [2].
- **Your note · safety:** ✗ Not followed in this report: more weight on safety standards
- **Your note · exports:** Not checked: only exports

## Adoption rate

- Acme Institute 17 reports 40 percent for 2024 [1].

## Sources

1. agency17.example.test — [Adoption survey](https://agency17.example.test/adoption-2024)
2. bureau2.example.test — [Widget funding (independent panel)](https://bureau2.example.test/widget-funding-2024-panel)

How this was researched: [evidence log](report-noted-evidence.md)
`;
const NOTED_OUTLINE = [
  { heading: "Bottom line", kind: "bottom_line", label: "Bottom line", topic_index: null, topic_count: null, note_id: null },
  { heading: "Adoption rate", kind: "topic", label: "Adoption rate", topic_index: 1, topic_count: 1, note_id: null },
  { heading: "Sources", kind: "sources", label: "Sources", topic_index: null, topic_count: null, note_id: null },
];

test.describe("the bottom line's note lines", () => {
  test.use({ viewport: { width: 1252, height: 853 }, reducedMotion: "reduce" });

  test("print one row per note, the mark in its key: ✓ ok, ✗ warn, none when not checked (AC23)", async ({ page, request, context }) => {
    const id = await submit(page, "q");
    await waitTerminal(request, id);
    await context.route(new RegExp(`/api/research/${id}/report$`), (route) => route.fulfill({ body: NOTED_REPORT, contentType: "text/markdown; charset=utf-8" }));
    await context.route(new RegExp(`/api/research/${id}/status$`), async (route) => {
      const response = await route.fetch();
      route.fulfill({ response, json: { ...(await response.json()), report_outline: NOTED_OUTLINE } });
    });
    await page.goto(`/research/${id}`);
    const rows = page.locator("#rep-bottom-line ul.bl-list > li");
    await expect(rows).toHaveCount(4);
    await expect(rows.locator(":scope > .k")).toHaveText(["Adoption rate:", "✓Your note · recycling:", "✗Your note · safety:", "Your note · exports:"]);
    await expect(rows.nth(1).locator(".k .ok")).toHaveText("✓");
    await expect(rows.nth(2).locator(".k .no")).toHaveText("✗");
    await expect(rows.nth(3).locator(".k .ok, .k .no")).toHaveCount(0);
    const colours = await page.evaluate(() => {
      const probe = (token: string) => {
        const span = document.createElement("span");
        span.style.color = `var(${token})`;
        document.body.append(span);
        const colour = getComputedStyle(span).color;
        span.remove();
        return colour;
      };
      const mark = (selector: string) => getComputedStyle(document.querySelector(selector)!).color;
      return { ok: mark("#rep-bottom-line .k .ok"), no: mark("#rep-bottom-line .k .no"), statusOk: probe("--status-ok"), statusWarn: probe("--status-warn") };
    });
    expect([colours.ok, colours.no]).toEqual([colours.statusOk, colours.statusWarn]);
    expect(await rows.first().evaluate((li) => getComputedStyle(li).gridTemplateColumns.split(" ")[0])).toBe("132px");
  });
});
