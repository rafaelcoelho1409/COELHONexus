import { describe, expect, it } from "vitest";

import { indexPage, searchPages, snippetAround } from "./materials-search";

const heading = (id: string, text: string) => ({ id, text, level: 2 as const });

const INDEX = [
  indexPage({ idx: 0, title: "Quickstart" }, "# Quickstart\n\nInstall the CLI, then run it.", [
    heading("install", "Install"),
  ]),
  indexPage({ idx: 1, title: "Deployment" }, "# Deployment\n\nPush to production with the CLI.", []),
  indexPage({ idx: 2, title: "Changelog" }, "# Changelog\n\nFixed a rare crash on shutdown.", []),
];

const search = (query: string) => searchPages(INDEX, query, 30, 12);

describe("searchPages", () => {
  it("finds nothing for an empty query", () => {
    expect(search("   ")).toEqual([]);
  });

  it("ranks a title above a heading above a match in the text", () => {
    const hits = search("quickstart");
    expect(hits.map((hit) => [hit.idx, hit.score])).toEqual([[0, 3]]);

    const ranked = search("cli");
    expect(ranked.map((hit) => hit.idx)).toEqual([0, 1]);
    expect(ranked.every((hit) => hit.score === 1)).toBe(true);

    const mixed = searchPages(
      [...INDEX, indexPage({ idx: 3, title: "CLI reference" }, "x", [])],
      "cli",
      30,
      12,
    );
    expect(mixed[0]).toMatchObject({ idx: 3, score: 3 });
  });

  it("points a heading hit at the heading", () => {
    expect(search("install")).toEqual([
      { idx: 0, title: "Quickstart", sub: "Install", headingId: "install", score: 2 },
    ]);
  });

  it("shows the words around a match in the text, in the case the page has them", () => {
    const [hit] = search("crash");
    expect(hit).toMatchObject({ idx: 2, score: 1 });
    expect(hit?.sub).toBe("…a rare crash on…");
  });

  it("needs every word of the query in a title or the text", () => {
    expect(search("cli zzz")).toEqual([]);
    expect(search("production cli").map((hit) => hit.idx)).toEqual([1]);
  });

  it("stops at the most hits asked for", () => {
    expect(searchPages(INDEX, "a", 2, 12)).toHaveLength(2);
  });
});

describe("snippetAround", () => {
  it("cuts between words with an ellipsis where the text goes on, and puts it on one line", () => {
    expect(snippetAround("aaaa\n\nneedle\n\nbbbb", "NEEDLE", 3)).toBe("…needle…");
    expect(snippetAround("one two needle three four", "needle", 5)).toBe("…two needle…");
    expect(snippetAround("needle", "needle", 10)).toBe("needle");
  });

  it("is empty when the term is not there, and takes a regex character as a letter", () => {
    expect(snippetAround("nothing", "zzz", 5)).toBe("");
    expect(snippetAround("call f(x) now", "f(x)", 2)).toBe("…f(x)…");
  });
});
