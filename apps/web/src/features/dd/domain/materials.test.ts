import remarkParse from "remark-parse";
import { unified } from "unified";
import { describe, expect, it } from "vitest";

import { manifestSchema } from "../entities";
import { dropLeadingTitle, pageFacts, pageNeighbours, pageTitle, sourceHref } from "./materials";

function entries(...overrides: Array<Record<string, unknown>>) {
  return manifestSchema.parse({
    entries: overrides.map((override, idx) => ({
      idx,
      key: null,
      title: `Page ${idx}`,
      slug: `page-${idx}`,
      tier: "llms_full",
      bytes: 2_048,
      url: "https://docs.test/llms-full.txt",
      source_path: null,
      section: null,
      notes: null,
      ...override,
    })),
  }).entries;
}

describe("pageTitle", () => {
  it("is the heading, then the slug, then the page's number", () => {
    const [titled, untitled, bare] = entries(
      { title: "  Quickstart " },
      { title: null },
      { title: null, slug: null },
    );
    expect(titled && pageTitle(titled)).toBe("Quickstart");
    expect(untitled && pageTitle(untitled)).toBe("page-1");
    expect(bare && pageTitle(bare)).toBe("Page 3");
  });
});

describe("pageFacts", () => {
  it("names the tier, the size and the section, leaving out what is missing", () => {
    const [full, bare] = entries({ section: " Guides " }, { tier: null, bytes: null });
    expect(full && pageFacts(full)).toEqual(["llms_full", "2.0 KB", "Guides"]);
    expect(bare && pageFacts(bare)).toEqual(["0 B"]);
  });
});

describe("sourceHref", () => {
  it("prefers the section's own address, then the page's, and only ever a web address", () => {
    const [section, page, odd, none] = entries(
      { source_path: "https://docs.test/guide" },
      {},
      { url: "javascript:alert(1)", source_path: "not a url" },
      { url: null },
    );
    expect(section && sourceHref(section)).toBe("https://docs.test/guide");
    expect(page && sourceHref(page)).toBe("https://docs.test/llms-full.txt");
    expect(odd && sourceHref(odd)).toBeNull();
    expect(none && sourceHref(none)).toBeNull();
  });
});

describe("pageNeighbours", () => {
  const list = entries({}, {}, {});

  it("gives the page before and after, none at either end", () => {
    expect(pageNeighbours(list, 1)).toEqual({ position: 1, previous: 0, next: 2 });
    expect(pageNeighbours(list, 0)).toEqual({ position: 0, previous: null, next: 1 });
    expect(pageNeighbours(list, 2)).toEqual({ position: 2, previous: 1, next: null });
  });

  it("sends a page the filter took out on to the first one on show", () => {
    expect(pageNeighbours(list.slice(1), 0)).toEqual({ position: -1, previous: null, next: 1 });
    expect(pageNeighbours([], null)).toEqual({ position: -1, previous: null, next: null });
  });
});

describe("dropLeadingTitle", () => {
  /** The kinds of block the page is left with after the plugin has run. */
  const blocks = (markdown: string, title: string) => {
    const processor = unified().use(remarkParse).use(dropLeadingTitle(title));
    const tree = processor.parse(markdown);
    processor.runSync(tree);
    return tree.children.map((node) => node.type);
  };

  it("drops a leading title that says what the page's title says", () => {
    expect(blocks("# Quick   Start\n\nBody", "quick start")).toEqual(["paragraph"]);
  });

  it("keeps a heading that says something else, or one that is not first", () => {
    expect(blocks("# Other\n\nBody", "Quickstart")).toEqual(["heading", "paragraph"]);
    expect(blocks("Intro\n\n# Quickstart", "Quickstart")).toEqual(["paragraph", "heading"]);
  });
});
