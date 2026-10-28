import { describe, expect, it } from "vitest";

import {
  cellsFromIds,
  cellsFromPlan,
  chapterCounter,
  chapterFacts,
  harmonizeDoneLabel,
  humanizeChapterId,
  proposedTitles,
  stringList,
  synthStartLabel,
  toChapterStatus,
} from "./chapters";

describe("chapter cells", () => {
  const plan = {
    study_total_wall_ms: 0,
    chapters: [
      { id: "02_eval", title: null, order: 2, rendered: false, wall_ms: null, thread_id: null },
      { id: "01_hall", title: "Hallucinations", order: 1, rendered: true, wall_ms: 2000, thread_id: "t1" },
    ],
  };

  it("builds cells in plan order with the durable render status", () => {
    const cells = cellsFromPlan(plan);
    expect(cells.map((cell) => [cell.id, cell.status, cell.wallMs, cell.threadId])).toEqual([
      ["01_hall", "done", 2000, "t1"],
      ["02_eval", "pending", null, null],
    ]);
    expect(cells[1]?.title).toBe("02 eval"); // no title yet: humanised from the id
  });

  it("builds placeholder cells from bare ids", () => {
    expect(cellsFromIds(["ch-01-intro", "b"]).map((cell) => [cell.title, cell.status])).toEqual([
      ["Intro", "pending"],
      ["B", "pending"],
    ]);
  });

  it("counts done chapters, naming failures", () => {
    const cells = cellsFromIds(["a", "b", "c"]).map((cell, index) => ({
      ...cell,
      status: (["done", "failed", "pending"] as const)[index] ?? "pending",
    }));
    expect(chapterCounter(cells)).toBe("1 done, 1 failed / 3");
    expect(chapterCounter(cells.slice(0, 1))).toBe("1 / 1");
  });

  it("offers Resume only for a partly rendered book", () => {
    const [a, b] = cellsFromIds(["a", "b"]);
    if (!a || !b) throw new Error("fixture");
    expect(synthStartLabel([a, b])).toBe("Start");
    expect(synthStartLabel([{ ...a, status: "done" }, b])).toBe("Resume");
    expect(
      synthStartLabel([
        { ...a, status: "done" },
        { ...b, status: "done" },
      ]),
    ).toBe("Start");
  });

  it("maps server statuses onto cell statuses", () => {
    expect(toChapterStatus("failed")).toBe("failed");
    expect(toChapterStatus("cancelled")).toBe("cancelled");
    expect(toChapterStatus("done")).toBe("done");
    expect(toChapterStatus("something-new")).toBe("done");
  });

  it("humanises chapter ids", () => {
    expect(humanizeChapterId("ch-02-hallucination_control")).toBe("Hallucination control");
    expect(humanizeChapterId("07")).toBe("07");
  });

  it("keeps only strings from an open payload list", () => {
    expect(stringList(["a", 1, null, "b"])).toEqual(["a", "b"]);
    expect(stringList("a")).toEqual([]);
  });

  it("lists what is known about a finished chapter, with singular forms and a reading time", () => {
    const chapter = {
      id: "a",
      title: "A",
      order: 1,
      rendered: true,
      wall_ms: null,
      thread_id: null,
      audit_passed: true,
      n_sections: 4,
      n_sources: 1,
      rendered_chars: 24_600,
    };
    expect(chapterFacts(chapter)).toEqual(["4 sections", "1 source", "24.6k chars", "25 min read"]);
    expect(chapterFacts({ ...chapter, n_sections: null, n_sources: null, rendered_chars: 640 })).toEqual([
      "640 chars",
      "1 min read",
    ]);
    expect(
      chapterFacts({ ...chapter, n_sections: undefined, n_sources: undefined, rendered_chars: null }),
    ).toEqual([]);
  });

  it("reads the planner's proposed chapter titles from its stats, whatever else the state holds", () => {
    expect(
      proposedTitles({ propose_stats: { n_proposals: 2, titles: ["Foundations", "Agents", 7, null] } }),
    ).toEqual(["Foundations", "Agents"]);
    expect(proposedTitles({ propose_stats: { skipped: "no_files" } })).toEqual([]);
    expect(proposedTitles({ propose_stats: "oops" })).toEqual([]);
    expect(proposedTitles({})).toEqual([]);
  });

  it("words the volume audit outcome", () => {
    expect(harmonizeDoneLabel(2, 3)).toBe("2/3 patched");
    expect(harmonizeDoneLabel(0, 0)).toBe("Verified, no patches");
    expect(harmonizeDoneLabel(0, 4)).toBe("4 issues, 0 patched");
    expect(harmonizeDoneLabel(2, 3, true)).toBe("2/3 patched (cache)");
    expect(harmonizeDoneLabel(0, 0, true)).toBe("Verified, no patches (cache)");
  });
});
