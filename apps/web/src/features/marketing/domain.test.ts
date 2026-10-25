import { describe, expect, it } from "vitest";

import {
  countActiveRuns,
  countDocumentationSets,
  countIndexedVideos,
  formatCount,
  isHealthy,
} from "./domain";

describe("platform status narrowing", () => {
  it("accepts only a healthy status", () => {
    expect(isHealthy({ status: "healthy" })).toBe(true);
    expect(isHealthy({ status: "degraded" })).toBe(false);
    expect(isHealthy(null)).toBe(false);
  });

  it("counts documentation sets from a list", () => {
    expect(countDocumentationSets([{}, {}, {}])).toBe(3);
    expect(countDocumentationSets({})).toBeNull();
  });

  it("counts active runs and rejects an unexpected shape", () => {
    expect(countActiveRuns({ active: [{ id: "a" }] })).toBe(1);
    expect(countActiveRuns({ active: [] })).toBe(0);
    expect(countActiveRuns({ runs: [] })).toBeNull();
  });

  it("reads the indexed-video total", () => {
    expect(countIndexedVideos({ items: [], total: 1204 })).toBe(1204);
    expect(countIndexedVideos({ items: [] })).toBeNull();
    expect(countIndexedVideos({ total: -1 })).toBeNull();
  });

  it("formats counts and falls back to the placeholder", () => {
    expect(formatCount(12345, "—")).toBe("12,345");
    expect(formatCount(0, "—")).toBe("0");
    expect(formatCount(null, "—")).toBe("—");
    expect(formatCount(undefined, "…")).toBe("…");
  });
});
