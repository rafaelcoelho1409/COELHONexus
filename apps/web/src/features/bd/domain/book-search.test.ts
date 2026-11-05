import { describe, expect, it } from "vitest";

import type { TocEntry } from "@/shared/markdown";

import type { BookPageIndexEntry } from "./book-search";
import { searchBookPages } from "./book-search";

const headings: TocEntry[] = [{ id: "durability", level: 2, text: "Durability" }];
const index: BookPageIndexEntry[] = [
  { page: 4, title: "Reliability", headings, text: "Continuing to work correctly when things go wrong." },
  { page: 8, title: "Storage", headings: [], text: "A durable system preserves committed records." },
];

describe("searchBookPages", () => {
  it("ranks titles and headings before body matches", () => {
    expect(searchBookPages(index, "reliability", 30).map((hit) => hit.page)).toEqual([4]);
    expect(searchBookPages(index, "durability", 30).map((hit) => hit.sub)).toContain("Durability");
    expect(searchBookPages(index, "committed records", 30)[0]).toMatchObject({
      page: 8,
      sub: "Match in the text",
    });
  });

  it("carries the heading to scroll to for a heading hit, and none for a title or body hit", () => {
    expect(searchBookPages(index, "durability", 30)[0]).toMatchObject({ page: 4, headingId: "durability" });
    expect(searchBookPages(index, "reliability", 30)[0]?.headingId).toBeUndefined();
    expect(searchBookPages(index, "committed records", 30)[0]?.headingId).toBeUndefined();
  });

  it("requires every term in a title or body and caps results", () => {
    expect(searchBookPages(index, "storage not-present", 30)).toEqual([]);
    expect(searchBookPages(index, "system", 1)).toHaveLength(1);
    expect(searchBookPages(index, "  ", 30)).toEqual([]);
  });
});
