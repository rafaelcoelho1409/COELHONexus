import { describe, expect, it } from "vitest";

import { parseEnv } from "./env";
import { buildRewrites } from "./rewrites";

const rewrites = (source: Record<string, string>) => buildRewrites(parseEnv(source));

describe("buildRewrites", () => {
  it("proxies only the API by default — no docs", () => {
    expect(rewrites({ SERVER_URL: "http://coelhonexus-server:8000" })).toEqual([
      { source: "/api/health", destination: "http://coelhonexus-server:8000/health" },
      { source: "/api/:path*", destination: "http://coelhonexus-server:8000/api/:path*" },
    ]);
  });

  it("exposes the API docs only when asked", () => {
    expect(rewrites({ EXPOSE_API_DOCS: "true" }).map((rule) => rule.source)).toEqual([
      "/api/health",
      "/api/:path*",
      "/docs",
      "/docs/:path*",
      "/openapi.json",
    ]);
  });

  it("ignores an empty flag, as an unset Docker build arg arrives empty", () => {
    expect(rewrites({ EXPOSE_API_DOCS: "" })).toHaveLength(2);
  });
});
