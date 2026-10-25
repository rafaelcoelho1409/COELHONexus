import { defineConfig } from "@hey-api/openapi-ts";

/**
 * Typed client for the Nexus API, generated from the committed spec (`openapi/openapi.json`, produced by
 * `scripts/server/export_openapi.py`). Regenerate with `npm run api:generate`; CI fails on drift.
 * The generated folder is committed so a backend change shows up as a reviewable diff.
 */
export default defineConfig({
  // The leading "./" matters: a bare "openapi/openapi.json" is parsed as a hey-api "org/project" shorthand.
  input: "./openapi/openapi.json",
  // Same-origin: the browser calls /api/*, which next.config.ts proxies to the server (baseUrl "" below).
  output: { path: "src/shared/api/generated" },
  plugins: [
    "@hey-api/typescript",
    { name: "@hey-api/sdk", operations: { strategy: "flat" } },
    { name: "@hey-api/client-fetch", baseUrl: "" },
    { name: "@tanstack/react-query", queryOptions: true, mutationOptions: true },
  ],
});
