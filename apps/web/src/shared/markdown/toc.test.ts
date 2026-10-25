import { describe, expect, it } from "vitest";

import { remarkAlerts } from "./remark-alerts";
import { markdownToc } from "./toc";

describe("markdownToc", () => {
  it("lists h2 and h3 headings with github-style ids and skips other levels", () => {
    const toc = markdownToc(
      "# Title\n\n## Getting started\n\ntext\n\n### Install it\n\n#### Deep\n\n## API & Tools\n",
    );
    expect(toc).toEqual([
      { level: 2, text: "Getting started", id: "getting-started" },
      { level: 3, text: "Install it", id: "install-it" },
      { level: 2, text: "API & Tools", id: "api--tools" },
    ]);
  });

  it("gives repeated headings distinct ids, counting every heading level like the renderer does", () => {
    const toc = markdownToc("# Setup\n\n## Setup\n\n## Setup\n");
    expect(toc.map((entry) => entry.id)).toEqual(["setup-1", "setup-2"]);
  });

  it("reads the text of a heading, not its markdown", () => {
    expect(markdownToc("## The `fit()` **method** and [docs](https://x.y)\n")[0]).toEqual({
      level: 2,
      text: "The fit() method and docs",
      id: "the-fit-method-and-docs",
    });
  });

  it("ignores heading-looking lines inside code fences", () => {
    expect(markdownToc("## Real\n\n```md\n## Not a heading\n```\n")).toHaveLength(1);
  });

  it("applies the same remark plugins as the rendering does", () => {
    const dropContents =
      () => (tree: { children: Array<{ type: string; children?: Array<{ value?: string }> }> }) => {
        tree.children = tree.children.filter(
          (node) => !(node.type === "heading" && node.children?.[0]?.value === "Contents"),
        );
      };
    expect(markdownToc("## Contents\n\n## Real\n", [dropContents]).map((entry) => entry.text)).toEqual([
      "Real",
    ]);
    expect(markdownToc("## Contents\n\n## Real\n")).toHaveLength(2);
  });

  it("works with the callout plugin installed", () => {
    expect(markdownToc("> [!NOTE]\n> hi\n\n## After\n", [remarkAlerts]).map((entry) => entry.id)).toEqual([
      "after",
    ]);
  });
});
