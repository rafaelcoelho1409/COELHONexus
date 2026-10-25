import { describe, expect, it } from "vitest";

import { markdownToc } from "@/shared/markdown";

import { CHAPTER_PLUGINS, unwrapChapterFence } from "./chapter-markdown";

describe("unwrapChapterFence", () => {
  it("removes a wrapper fence and its closing fence around a chapter", () => {
    expect(unwrapChapterFence("```markdown\n# Title\n\ntext\n```\n")).toBe("# Title\n\ntext\n");
    expect(unwrapChapterFence("~~~\n\n# Title\ntext\n~~~")).toBe("\n# Title\ntext");
  });

  it("leaves a chapter that starts with a heading, or with a fence around real code, alone", () => {
    expect(unwrapChapterFence("# Title\n\n```js\nx\n```")).toBe("# Title\n\n```js\nx\n```");
    expect(unwrapChapterFence("```js\nconst a = 1;\n```\n\n# Later")).toBe(
      "```js\nconst a = 1;\n```\n\n# Later",
    );
  });

  it("keeps a closing fence that has a language, because that one is real code", () => {
    const md = "```\n# Title\n\n```js\nx\n```js";
    expect(unwrapChapterFence(md)).toBe("# Title\n\n```js\nx\n```js");
  });
});

describe("chapter plugins", () => {
  const chapter = [
    "# Chapter",
    "",
    "## Contents",
    "",
    "- [One](#one)",
    "- [Two](#two)",
    "",
    "---",
    "",
    "## One",
    "",
    "Text.",
    "",
    "Sources for this section:",
    "",
    "- `0001-intro.md` — Intro",
    "- `0002-setup.md` — Setup",
    "",
    "## Two",
    "",
  ].join("\n");

  it("removes the inline Contents block, its list and the divider from the outline too", () => {
    expect(markdownToc(chapter, CHAPTER_PLUGINS).map((entry) => entry.text)).toEqual(["One", "Two"]);
    expect(markdownToc(chapter).map((entry) => entry.text)).toEqual(["Contents", "One", "Two"]);
  });
});
