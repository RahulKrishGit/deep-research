// Full-page capture of one URL at 1252 or 390 px into visual/<checkpoint>/<name>.png (never viewport-height).
// usage: node scripts/capture-stage.mjs <checkpoint> <name> <url> [--phone] [--view evidence]
import { mkdirSync } from "node:fs";
import path from "node:path";
import { chromium } from "@playwright/test"; // @playwright/test re-exports the browser launchers; `playwright` itself is not a declared dependency

const [checkpoint, name, url, ...flags] = process.argv.slice(2);
if (!checkpoint || !name || !url) { console.error("usage: capture-stage.mjs <checkpoint> <name> <url> [--phone] [--view evidence]"); process.exit(2); }
const phone = flags.includes("--phone");
const view = flags.includes("--view") ? flags[flags.indexOf("--view") + 1] : null;
const dir = path.join("visual", checkpoint);
mkdirSync(dir, { recursive: true });
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: phone ? { width: 390, height: 844 } : { width: 1252, height: 853 } });
await page.goto(url, { waitUntil: "networkidle" });
if (view === "evidence") {
  // the Report | Evidence toggle is per page load: switch it here, after the report stage has rendered
  await page.locator("#segView button[data-view='evidence']").click();
  await page.locator(".ev-row").first().waitFor();
}
await page.waitForTimeout(700);
const file = path.join(dir, `${name}.png`);
await page.screenshot({ path: file, fullPage: true });
const size = await page.evaluate(() => [document.documentElement.scrollWidth, document.documentElement.scrollHeight]);
console.log(`${file} (${size[0]}x${size[1]} full page)`);
await browser.close();
