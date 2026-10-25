import { describe, expect, it } from "vitest";

import { parseEnv } from "./env";
import { buildRewrites } from "./rewrites";

const rewrites = (source: Record<string, string>) => buildRewrites(parseEnv(source));

describe("buildRewrites", () => {
  it("proxies only the API by default — no docs, no legacy UI", () => {
    const { afterFiles, fallback } = rewrites({ SERVER_URL: "http://coelhonexus-server:8000" });
    expect(afterFiles).toEqual([
      { source: "/api/health", destination: "http://coelhonexus-server:8000/health" },
      { source: "/api/:path*", destination: "http://coelhonexus-server:8000/api/:path*" },
    ]);
    expect(fallback).toEqual([]);
  });

  it("exposes the API docs only when asked", () => {
    const { afterFiles } = rewrites({ EXPOSE_API_DOCS: "true" });
    expect(afterFiles.map((rule) => rule.source)).toEqual([
      "/api/health",
      "/api/:path*",
      "/docs",
      "/docs/:path*",
      "/openapi.json",
    ]);
  });

  it("falls back to the legacy UI for everything this app does not serve", () => {
    const { afterFiles, fallback } = rewrites({ FASTHTML_URL: "http://coelhonexus-fasthtml:3000/" });
    expect(fallback).toEqual([{ source: "/:path*", destination: "http://coelhonexus-fasthtml:3000/:path*" }]);
    // The API proxy still wins over the fallback: it sits earlier in Next's resolution order.
    expect(afterFiles[0]?.source).toBe("/api/health");
  });

  it("ignores empty values, as an unset Docker build arg arrives empty", () => {
    const { fallback, afterFiles } = rewrites({ FASTHTML_URL: "", EXPOSE_API_DOCS: "" });
    expect(fallback).toEqual([]);
    expect(afterFiles).toHaveLength(2);
  });
});
