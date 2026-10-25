import { describe, expect, it } from "vitest";

import { type ChapterIndexEntry, indexChapter, searchChapters } from "./study-search";

const heading = (text: string, id: string) => ({ level: 2 as const, text, id });

const INDEX: ChapterIndexEntry[] = [
  indexChapter(
    { id: "ch-1", title: "Hallucinations" },
    "# Hallucinations\n\n## Verifiers\n\nVerifiers cut rates.\n\n## Retrieval\n\nGround answers.",
    [heading("Verifiers", "verifiers"), heading("Retrieval", "retrieval")],
  ),
  indexChapter(
    { id: "ch-2", title: "Evaluation" },
    "# Evaluation\n\nMeasure hallucination rates with a judge.",
    [],
  ),
];

describe("searchChapters", () => {
  it("finds nothing for an empty query", () => {
    expect(searchChapters(INDEX, "  ")).toEqual([]);
  });

  it("ranks a title hit above a heading hit above a body hit", () => {
    const hits = searchChapters(INDEX, "hallucination");
    expect(hits.map((hit) => [hit.chapterId, hit.score])).toEqual([
      ["ch-1", 3], // title
      ["ch-2", 1], // only in the body
    ]);
  });

  it("returns each matching heading with the id to scroll to", () => {
    const hits = searchChapters(INDEX, "verifiers");
    expect(hits[0]).toMatchObject({ chapterId: "ch-1", sub: "Verifiers", headingId: "verifiers", score: 2 });
  });

  it("needs every word for a title or body match, but any word for a heading", () => {
    expect(searchChapters(INDEX, "judge hallucination").map((hit) => hit.chapterId)).toEqual(["ch-2"]);
    expect(searchChapters(INDEX, "retrieval zzz").map((hit) => hit.sub)).toEqual(["Retrieval"]);
  });

  it("is case-insensitive and caps the number of hits", () => {
    expect(searchChapters(INDEX, "EVALUATION")[0]?.chapterId).toBe("ch-2");
    const many = Array.from({ length: 50 }, (_, i) =>
      indexChapter({ id: `c${i}`, title: `Topic ${i}` }, "", []),
    );
    expect(searchChapters(many, "topic")).toHaveLength(30);
  });
});
