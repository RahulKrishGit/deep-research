import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

const require = createRequire(import.meta.url);
const viteMajor = Number(require("vite/package.json").version.split(".")[0]);
// Next's tsconfig keeps jsx: "preserve"; tests need the automatic runtime. Vite ≤ 7 transforms
// with esbuild, Vite 8 with oxc — the option lives under a different key in each.
const jsxRuntime =
  viteMajor >= 8
    ? { oxc: { jsx: { runtime: "automatic" } } }
    : { esbuild: { jsx: "automatic" } };

export default defineConfig({
  ...(jsxRuntime as object),
  // Vite 8 defaults resolve.tsconfigPaths to false, so it never reads tsconfig.json's
  // "@/*" path mapping; the components import via "@/…", so the alias is set here too.
  resolve: { alias: { "@": fileURLToPath(new URL("./", import.meta.url)) } },
  test: {
    environment: "jsdom",
    setupFiles: ["test/setup.ts"],
    include: ["test/**/*.test.ts", "test/**/*.test.tsx"],
  },
});
