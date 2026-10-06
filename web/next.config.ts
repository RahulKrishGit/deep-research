import type { NextConfig } from "next";

// compress: false — the app is local and an SSE body must never wait in a gzip buffer.
// No rewrites: /api/* is a route handler that streams (app/api/[...path]/route.ts).
const config: NextConfig = { compress: false, reactStrictMode: true };

export default config;
