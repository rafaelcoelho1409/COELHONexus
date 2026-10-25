import { describe, expect, it } from "vitest";

import { normalizeMath } from "./math";

describe("normalizeMath", () => {
  it("leaves ordinary markdown and dollar-fenced math alone", () => {
    const md = "# Title\n\nInline $x^2$ and\n\n$$\ny = mx + b\n$$\n";
    expect(normalizeMath(md)).toBe(md);
  });

  it("turns \\[ \\] into display math and \\( \\) into inline math", () => {
    expect(normalizeMath("a \\[x+y\\] b")).toBe("a $$x+y$$ b");
    expect(normalizeMath("see \\( a_1 \\) here")).toBe("see $a_1$ here");
    expect(normalizeMath("\\[\nx\n\\]")).toBe("$$\nx\n$$");
  });

  it("fences a bare environment and renames align to aligned, which KaTeX knows", () => {
    expect(normalizeMath("\\begin{align}\na &= b \\\\\nc &= d\n\\end{align}")).toBe(
      "$$\n\\begin{aligned}\na &= b \\\\\nc &= d\n\\end{aligned}\n$$",
    );
    expect(normalizeMath("\\begin{align*}x\\end{align*}")).toContain("\\begin{aligned}x\\end{aligned}");
  });

  it("does not wrap an environment that is already inside $$ twice", () => {
    const out = normalizeMath("$$\n\\begin{align}a\\end{align}\n$$");
    expect(out).toBe("$$\n\\begin{aligned}a\\end{aligned}\n$$");
    expect(out.match(/\$\$/g)).toHaveLength(2);
  });

  it("keeps matrices and other environments, fenced", () => {
    expect(normalizeMath("\\begin{pmatrix}1&2\\end{pmatrix}")).toBe(
      "$$\n\\begin{pmatrix}1&2\\end{pmatrix}\n$$",
    );
  });

  it("never touches fenced or inline code", () => {
    const fenced = "```tex\n\\[x\\]\n\\begin{align}a\\end{align}\n```";
    expect(normalizeMath(fenced)).toBe(fenced);
    expect(normalizeMath("use `\\(x\\)` literally")).toBe("use `\\(x\\)` literally");
    const tilde = "~~~\n\\[x\\]\n~~~\n\nthen \\[y\\]";
    expect(normalizeMath(tilde)).toBe("~~~\n\\[x\\]\n~~~\n\nthen $$y$$");
  });

  it("only closes a fence with the same character", () => {
    const md = "```\n~~~\n\\[x\\]\n```\n\\[y\\]";
    expect(normalizeMath(md)).toBe("```\n~~~\n\\[x\\]\n```\n$$\ny\n$$"); // alone on its line: display math
  });
});
