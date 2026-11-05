import { describe, expect, it } from "vitest";

import { diffPages } from "./page-diff";

describe("diffPages", () => {
  it("reports identical text as identical, with nothing to show", () => {
    expect(diffPages("A\n\nB", "A\n\nB")).toEqual({ rows: [], changedLines: 0, identical: true });
  });

  it("marks the changed words of a replaced line and keeps the rest of the line as it was", () => {
    const diff = diffPages("## I NTRODUC T I ON\n\nBody", "## INTRODUCTION\n\nBody", 0);
    expect(diff.identical).toBe(false);
    expect(diff.changedLines).toBe(2);
    const [removed, added] = diff.rows;
    expect(removed?.kind).toBe("removed");
    expect(added?.kind).toBe("added");
    if (removed?.kind !== "removed" || added?.kind !== "added") throw new Error("expected a replaced line");
    expect(removed.tokens.filter((token) => !token.changed).map((token) => token.text.trim())).toContain(
      "##",
    );
    expect(removed.tokens.some((token) => token.changed && token.text === "NTRODUC")).toBe(true);
    expect(added.tokens.some((token) => token.changed && token.text === "INTRODUCTION")).toBe(true);
  });

  it("shows a removed paragraph and an added one without pairing them with anything", () => {
    const removedOnly = diffPages("one\n\ntwo\n\nthree", "one\n\nthree", 0);
    expect(removedOnly.rows.filter((row) => row.kind === "removed")).toHaveLength(2);
    expect(removedOnly.rows.some((row) => row.kind === "added")).toBe(false);
    const addedOnly = diffPages("one", "one\n\nextra", 0);
    expect(addedOnly.rows.filter((row) => row.kind === "added")).toHaveLength(2);
  });

  it("collapses long unchanged stretches to a skip row and keeps context around the change", () => {
    const lines = Array.from({ length: 30 }, (_, index) => `line ${index}`);
    const changed = [...lines];
    changed[15] = "line fifteen";
    const diff = diffPages(lines.join("\n"), changed.join("\n"), 2);
    expect(diff.rows[0]).toMatchObject({ kind: "skip", count: 13 });
    expect(diff.rows.at(-1)).toMatchObject({ kind: "skip", count: 12 });
    expect(new Set(diff.rows.map((row) => row.id)).size).toBe(diff.rows.length);
    expect(diff.rows.filter((row) => row.kind === "same")).toHaveLength(4);
    expect(diff.changedLines).toBe(2);
  });

  it("falls back to a coarse diff when the exact table would be too large", () => {
    const before = Array.from({ length: 2500 }, (_, index) => `a${index}`).join("\n");
    const after = Array.from({ length: 2500 }, (_, index) => `b${index}`).join("\n");
    const diff = diffPages(before, after, 0);
    expect(diff.changedLines).toBe(5000);
    expect(diff.identical).toBe(false);
  });
});
