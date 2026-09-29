// Issue 1 (human-reported 2026-09-28): a long, left-aligned question left a gap on the right —
// measured on a live 136-char question, the dashed box spanned 572-1292 but the text ended at
// 1057, ~215px short. Cause: the global `h1{text-wrap:balance}` rule (globals.css:64, verbatim
// from the prototype) balances the two wrapped lines to similar widths instead of filling the
// frame. `.report-q` already carried `text-wrap:pretty` (verbatim, prototype line 893); fix:
// `.ask-q{text-wrap:pretty}` in the app-only block.
//
// The replay middleware rewrites every submitted query to the chosen case's own question
// (src/deep_research/api/replay.py:136-193), so a long question cannot be driven through the
// composer directly — the real status body is intercepted here and its `query` replaced with a
// 142-character one, per the brief's own suggested approach. reducedMotion:'reduce' skips the
// (separately tested) idle→running flight box entirely, so this test is only ever exercising the
// wrap, not the handoff.
import { expect, test } from "@playwright/test";
import { submit } from "./support";

const LONG_QUESTION =
  "What measurable long-term effects have large-scale reforestation and afforestation programs had on regional precipitation patterns since 2010?";

test.describe("long question wrap (Issue 1)", () => {
  test.use({ viewport: { width: 1568, height: 843 }, reducedMotion: "reduce" });

  test("a long left-aligned question fills the frame instead of stopping short of its right edge", async ({ page, context }) => {
    await context.route(/\/api\/research\/[^/]+\/status$/, async (route) => {
      const response = await route.fetch();
      const body = await response.json();
      body.query = LONG_QUESTION;
      await route.fulfill({ response, json: body });
    });
    await submit(page, "q");
    const h1 = page.locator("#running-h");
    await expect(h1).toHaveText(LONG_QUESTION, { timeout: 15_000 });
    // Short (<=80 char, q-center) questions stay centred — confirm this one took the other path.
    await expect(h1).not.toHaveClass(/q-center/);

    const gap = await h1.evaluate((el) => {
      const style = getComputedStyle(el);
      const box = el.getBoundingClientRect();
      const contentRight = box.right - parseFloat(style.paddingRight) - parseFloat(style.borderRightWidth);
      const range = document.createRange();
      range.selectNodeContents(el.firstChild!);
      const rects = [...range.getClientRects()];
      const textRight = Math.max(...rects.map((r) => r.right));
      return contentRight - textRight;
    });
    // Balanced (the pre-fix behaviour): ~215px short. Pretty: within ~60px of the frame's own
    // content-right edge, per the brief's own tolerance.
    expect(gap).toBeLessThanOrEqual(60);
    expect(gap).toBeGreaterThanOrEqual(0);
  });
});
