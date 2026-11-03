import { describe, expect, it } from "vitest";

import { CHAPTERS, OUTLINE } from "../test-fixtures";
import { activeChapterIndex, activeEntryIndex, chapterTree } from "./outline";

const chapters = CHAPTERS.map((chapter) => ({ ...chapter }));
const outline = OUTLINE.map((entry) => ({ ...entry }));

describe("chapterTree", () => {
  it("puts each outline entry under the chapter that holds its page, leaving out the chapter's own heading", () => {
    const tree = chapterTree(chapters, outline);
    expect(tree.map((chapter) => chapter.title)).toEqual(["Foundations of Data Systems", "Distributed Data"]);
    expect(tree[0]?.entries.map((entry) => entry.title)).toEqual(["Reliability", "Scalability"]);
    expect(tree[1]?.entries).toEqual([]);
  });

  it("groups a bare outline into one list, and says nothing for a PDF without either", () => {
    expect(chapterTree([], outline)).toEqual([
      { title: "Outline", firstPage: 1, lastPage: 6, entries: outline },
    ]);
    expect(chapterTree([], [])).toEqual([]);
  });
});

describe("what is being read", () => {
  const tree = chapterTree(chapters, outline);

  it("finds the chapter and the section for a page", () => {
    expect(activeChapterIndex(tree, 3)).toBe(0);
    expect(activeChapterIndex(tree, 7)).toBe(1);
    expect(activeChapterIndex(tree, 99)).toBe(-1);
    expect(activeChapterIndex(tree, null)).toBe(-1);
    const first = tree[0];
    if (!first) throw new Error("fixture has a first chapter");
    expect(activeEntryIndex(first, 1)).toBe(-1); // before its first section
    expect(activeEntryIndex(first, 3)).toBe(0);
    expect(activeEntryIndex(first, 5)).toBe(1);
  });
});
