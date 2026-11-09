import { describe, expect, it } from "vitest";

import type { RawHit } from "./entities";
import {
  applyAiEvent,
  collectGraph,
  filterRows,
  flattenRows,
  runStateOf,
  schemaOutline,
  sortRows,
} from "./query";

const hit = (raw: Record<string, unknown>): RawHit => ({ summary: "row", raw });

describe("Book Distiller Query domain", () => {
  it("flattens and filters backend records without losing book fields", () => {
    const rows = flattenRows("elasticsearch", [
      hit({
        _index: "bd_pages",
        _id: "book-1:42",
        _score: 3.2,
        _source: { book_id: "book-1", chapter: "Distributed Data", content: "Consensus" },
      }),
    ]);
    expect(filterRows(rows, { chapter: "distributed" })).toHaveLength(1);
    expect(filterRows(rows, { chapter: "storage" })).toHaveLength(0);
    expect(rows[0]).toMatchObject({ book_id: "book-1", content: "Consensus" });
  });

  it("sorts numeric page fields and preserves an unsuccessful run message", () => {
    expect(sortRows([{ page: 9 }, { page: 2 }], { column: "page", dir: "asc" })).toEqual([
      { page: 2 },
      { page: 9 },
    ]);
    expect(
      runStateOf({
        ok: false,
        error: "Read-only queries only",
        notes: [],
        took_ms: 0,
        total: null,
        hits: [],
      }),
    ).toEqual({ kind: "failed", message: "Read-only queries only", notes: [] });
  });

  it("collects a book graph and exposes schema starter queries", () => {
    const graph = collectGraph([
      hit({
        book: { _kind: "node", id: "b1", labels: ["Book"], properties: { title: "Systems" } },
        rel: {
          _kind: "relationship",
          id: "r1",
          type: "HAS_CHAPTER",
          start: "b1",
          end: "c1",
          properties: {},
        },
        chapter: { _kind: "node", id: "c1", labels: ["Chapter"], properties: { title: "Storage" } },
      }),
    ]);
    expect(graph.nodes).toHaveLength(2);
    expect(graph.edges).toHaveLength(1);

    const groups = schemaOutline("neo4j", {
      labels: ["Book"],
      node_properties: { Book: [{ name: "title", types: ["String"] }] },
      relationship_patterns: [],
    });
    expect(groups[0]?.query).toContain("MATCH (n:Book)");
  });

  it("moves the text-to-query trace through generation and completion", () => {
    const running = applyAiEvent(
      { phase: "idle", message: "" },
      { event: "start", data: null, ok: null, final: null, error: null },
    );
    expect(running.phase).toBe("running");
    expect(
      applyAiEvent(running, {
        event: "done",
        data: null,
        ok: true,
        final: '{"size": 10}',
        error: null,
      }),
    ).toEqual({ phase: "done", message: "Generated" });
  });
});
