// AC10: app/globals.css begins with docs/design/prototype/index.html lines 7-1137, line for line
// (CRLF-normalised), and anything after that sits under the app-only header with no colour literal.
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";

const web = process.cwd();
const html = path.join(web, "..", "docs", "design", "prototype", "index.html");
const css = path.join(web, "app", "globals.css");
const lines = (file) => readFileSync(file, "utf8").replace(/\r\n/g, "\n").split("\n");
if (!existsSync(css)) { console.log("MISMATCH: app/globals.css does not exist"); process.exit(1); }
const expected = lines(html).slice(6, 1137); // 1-based lines 7..1137 → 1131 lines
const actual = lines(css);
for (let i = 0; i < expected.length; i++) {
  if (actual[i] !== expected[i]) {
    console.log(`MISMATCH at globals.css line ${i + 1} (index.html line ${i + 7})`);
    console.log(`  expected: ${JSON.stringify(expected[i])}`);
    console.log(`  actual:   ${JSON.stringify(actual[i])}`);
    process.exit(1);
  }
}
const rest = actual.slice(expected.length).join("\n").trim();
const HEADER = "/* ═══ 2026-09-27: app-only additions ═══ */";
if (rest && !rest.startsWith(HEADER)) { console.log("MISMATCH: text after line 1131 is not under the app-only header"); process.exit(1); }
if (/#[0-9a-fA-F]{3,8}\b|\b(?:rgb|rgba|hsl|oklch)\(/.test(rest)) { console.log("MISMATCH: colour literal in the app-only block"); process.exit(1); }
console.log("OK");
