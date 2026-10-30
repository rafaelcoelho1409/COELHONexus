import { describe, expect, it } from "vitest";

import type { RawHit } from "./entities";
import { QUERY_GRAPH_NODE_HEIGHT, QUERY_GRAPH_NODE_WIDTH, QUERY_GRAPH_SPACING } from "./params";
import {
  AI_IDLE,
  aiText,
  applyAiEvent,
  cellKind,
  cellText,
  clip,
  clipToNode,
  collectGraph,
  filterRows,
  filterSchema,
  flattenRows,
  groupHistory,
  inferColumns,
  labelColors,
  layoutGraph,
  middleClip,
  neighborhood,
  nestedLabel,
  nextSort,
  planColumns,
  runStateOf,
  runStatus,
  schemaOutline,
  sortRows,
  toCsv,
  whenLabel,
} from "./query";

const hit = (raw: Record<string, unknown>): RawHit => ({ summary: "", raw });

describe("flattenRows", () => {
  it("puts the store's own fields in front of the document's", () => {
    const es = flattenRows("elasticsearch", [
      hit({ _index: "i", _id: "1", _score: 2, _source: { title: "A" } }),
    ]);
    expect(es).toEqual([{ _index: "i", _id: "1", _score: 2, title: "A" }]);
    const qdrant = flattenRows("qdrant", [hit({ id: "p", score: 0.5, payload: { video_id: "v" } })]);
    expect(qdrant).toEqual([{ id: "p", score: 0.5, video_id: "v" }]);
    const neo4j = flattenRows("neo4j", [hit({ title: "T" })]);
    expect(neo4j).toEqual([{ title: "T" }]);
  });

  it("survives a hit with no _source or payload", () => {
    expect(flattenRows("elasticsearch", [hit({ _id: "1" })])).toEqual([
      { _index: undefined, _id: "1", _score: undefined },
    ]);
  });
});

describe("cells", () => {
  it("classifies and prints values", () => {
    expect(cellKind(null)).toBe("null");
    expect(cellKind("")).toBe("empty");
    expect(cellKind([1])).toBe("array");
    expect(cellKind({ a: 1 })).toBe("object");
    expect(cellText(1.23456)).toBe("1.2346");
    expect(cellText(7)).toBe("7");
    expect(cellText({ a: 1 })).toBe('{"a":1}');
    expect(cellText(null)).toBe("");
    expect(nestedLabel([1, 2, 3])).toBe("Array[3]");
    expect(nestedLabel({ a: 1, b: 2 })).toBe("{2}");
  });

  it("clips long text with an ellipsis", () => {
    expect(clip("abcdef", 4)).toBe("abc…");
    expect(clip("abc", 4)).toBe("abc");
  });

  it("takes the union of keys in first-seen order", () => {
    expect(inferColumns([{ a: 1 }, { b: 2, a: 3 }, { c: 4 }])).toEqual(["a", "b", "c"]);
  });

  it("cuts an identifier in the middle so its start and its end still read", () => {
    expect(middleClip("01884202-8de8-5d83-946a-7082a2bffa6f", 20)).toBe("01884202-8…2a2bffa6f");
    expect(middleClip("short", 20)).toBe("short");
    expect(middleClip("abcdefghij", 6)).toBe("abc…ij");
  });
});

describe("planColumns", () => {
  const text = "A long transcript sentence that runs well past the eighty characters a table cell may show.";

  it("puts the score first, the fields next and identifiers last", () => {
    const plan = planColumns([
      { _index: "a", _id: "1", _score: 2, title: "T", video_id: "v", content: text },
      { _index: "a", _id: "2", _score: 1, title: "U", video_id: "w", content: text },
    ]);
    expect(plan.map((entry) => entry.column)).toEqual([
      "_score",
      "title",
      "content",
      "_index",
      "_id",
      "video_id",
    ]);
  });

  it("classifies columns by how they should be drawn", () => {
    const plan = planColumns([{ id: "x", views: 3, title: "T", body: text, tags: ["a"] }]);
    const kinds = Object.fromEntries(plan.map((entry) => [entry.column, entry.kind]));
    expect(kinds).toEqual({ id: "id", views: "number", title: "plain", body: "text", tags: "plain" });
  });

  it("starts a column that is empty in every row hidden, and `_index` when every hit shares it", () => {
    const hiddenOf = (rows: Array<Record<string, unknown>>) =>
      planColumns(rows)
        .filter((entry) => entry.hidden)
        .map((entry) => entry.column);
    expect(
      hiddenOf([
        { score: null, title: "A" },
        { score: null, title: "B" },
      ]),
    ).toEqual(["score"]);
    expect(
      hiddenOf([
        { _index: "i", title: "A" },
        { _index: "i", title: "B" },
      ]),
    ).toEqual(["_index"]);
    expect(hiddenOf([{ _index: "i" }, { _index: "j" }])).toEqual([]);
    expect(hiddenOf([{ title: "A", note: "" }])).toEqual(["note"]);
  });
});

describe("sort and filter", () => {
  const rows = [
    { name: "b", n: 10 },
    { name: "a", n: 2 },
    { name: null, n: null },
    { name: "c", n: 33 },
  ];

  it("cycles asc, desc, off", () => {
    const asc = nextSort(null, "n");
    expect(asc).toEqual({ column: "n", dir: "asc" });
    const desc = nextSort(asc, "n");
    expect(desc).toEqual({ column: "n", dir: "desc" });
    expect(nextSort(desc, "n")).toBeNull();
    expect(nextSort(desc, "name")).toEqual({ column: "name", dir: "asc" });
  });

  it("sorts numbers numerically and leaves missing values last either way", () => {
    expect(sortRows(rows, { column: "n", dir: "asc" }).map((r) => r.n)).toEqual([2, 10, 33, null]);
    expect(sortRows(rows, { column: "n", dir: "desc" }).map((r) => r.n)).toEqual([33, 10, 2, null]);
  });

  it("sorts text with embedded numbers naturally", () => {
    const named = [{ t: "v10" }, { t: "v9" }, { t: "v1" }];
    expect(sortRows(named, { column: "t", dir: "asc" }).map((r) => r.t)).toEqual(["v1", "v9", "v10"]);
  });

  it("does not mutate the input", () => {
    const copy = [...rows];
    sortRows(rows, { column: "n", dir: "asc" });
    expect(rows).toEqual(copy);
  });

  it("filters case-insensitively across several columns", () => {
    expect(filterRows(rows, { name: "A" })).toEqual([{ name: "a", n: 2 }]);
    expect(filterRows(rows, { name: "", n: "3" })).toEqual([{ name: "c", n: 33 }]);
    expect(filterRows(rows, {})).toHaveLength(4);
  });
});

describe("toCsv", () => {
  it("quotes commas, quotes and newlines and prints nested values as JSON", () => {
    const csv = toCsv(
      ["a", "b"],
      [
        { a: 'say "hi", ok', b: { x: 1 } },
        { a: "line\nbreak", b: null },
      ],
    );
    expect(csv).toBe('a,b\n"say ""hi"", ok","{""x"":1}"\n"line\nbreak",');
  });
});

describe("graph", () => {
  const node = (id: string, label: string, props: Record<string, unknown> = {}) => ({
    _kind: "node",
    id,
    labels: [label],
    properties: props,
  });
  const rel = (id: string, start: string, end: string, type: string) => ({
    _kind: "relationship",
    id,
    start,
    end,
    type,
    properties: {},
  });

  it("collects distinct nodes and edges from columns and paths, and drops dangling edges", () => {
    const hits = [
      hit({
        a: node("1", "Video", { title: "Intro" }),
        r: rel("e1", "1", "2", "BELONGS_TO"),
        b: node("2", "Channel", { name: "Ch" }),
      }),
      hit({
        p: {
          _kind: "path",
          nodes: [node("2", "Channel"), node("3", "Video")],
          rels: [rel("e2", "3", "2", "BELONGS_TO")],
        },
      }),
      hit({ lonely: rel("e3", "1", "99", "MENTIONS") }),
    ];
    const graph = collectGraph(hits);
    expect(graph.nodes.map((n) => [n.id, n.label, n.display])).toEqual([
      ["1", "Video", "Intro"],
      ["2", "Channel", "Ch"],
      ["3", "Video", "3"],
    ]);
    expect(graph.edges.map((e) => e.id)).toEqual(["e1", "e2"]);
  });

  it("colours labels in first-seen order and wraps the palette", () => {
    const nodes = ["A", "B", "C", "D", "E", "F"].map((label, i) => ({
      id: String(i),
      label,
      display: label,
      properties: {},
    }));
    const colors = labelColors(nodes);
    expect(colors.get("A")).toBe("var(--chart-1)");
    expect(colors.get("E")).toBe("var(--chart-5)");
    expect(colors.get("F")).toBe("var(--chart-1)");
  });

  it("lays out the same graph the same way and keeps linked nodes closer than strangers", () => {
    const hits = [
      hit({
        a: node("1", "X"),
        b: node("2", "X"),
        c: node("3", "X"),
        d: node("4", "X"),
        r: rel("e", "1", "2", "T"),
      }),
    ];
    const graph = collectGraph(hits);
    const first = layoutGraph(graph);
    const second = layoutGraph(graph);
    expect([...first.entries()]).toEqual([...second.entries()]);
    const dist = (a: string, b: string) => {
      const p = first.get(a);
      const q = first.get(b);
      return Math.hypot((p?.x ?? 0) - (q?.x ?? 0), (p?.y ?? 0) - (q?.y ?? 0));
    };
    expect(dist("1", "2")).toBeLessThan(dist("1", "4"));
  });

  it("keeps separate clusters on one canvas instead of flinging them apart", () => {
    // Two stars of three nodes and one linked pair: ten nodes in three clusters.
    const star = (prefix: string) => [
      node(`${prefix}0`, "Channel"),
      node(`${prefix}1`, "Video"),
      node(`${prefix}2`, "Video"),
      node(`${prefix}3`, "Video"),
      rel(`${prefix}e1`, `${prefix}1`, `${prefix}0`, "BELONGS_TO"),
      rel(`${prefix}e2`, `${prefix}2`, `${prefix}0`, "BELONGS_TO"),
      rel(`${prefix}e3`, `${prefix}3`, `${prefix}0`, "BELONGS_TO"),
    ];
    const hits = [
      hit({ items: star("a") }),
      hit({ items: star("b") }),
      hit({ items: [node("p", "Document"), node("q", "Entity"), rel("pq", "p", "q", "MENTIONS")] }),
    ];
    const graph = collectGraph(hits);
    expect(graph.nodes).toHaveLength(10);
    const farthest = Math.max(...[...layoutGraph(graph).values()].map((p) => Math.hypot(p.x, p.y)));
    expect(farthest).toBeLessThan(QUERY_GRAPH_SPACING * 4);
    // ...and no two cards overlap.
    const points = [...layoutGraph(graph).values()];
    for (const [i, a] of points.entries()) {
      for (const b of points.slice(i + 1)) {
        const apart =
          Math.abs(a.x - b.x) >= QUERY_GRAPH_NODE_WIDTH || Math.abs(a.y - b.y) >= QUERY_GRAPH_NODE_HEIGHT;
        expect(apart, `cards at (${a.x},${a.y}) and (${b.x},${b.y}) overlap`).toBe(true);
      }
    }
  });

  it("handles zero and one node", () => {
    expect(layoutGraph({ nodes: [], edges: [] }).size).toBe(0);
    const single = layoutGraph({ nodes: [{ id: "1", label: "X", display: "1", properties: {} }], edges: [] });
    expect(single.get("1")).toEqual({ x: 0, y: 0 });
  });

  it("finds a node's neighbourhood", () => {
    const graph = collectGraph([
      hit({
        r1: rel("e1", "1", "2", "T"),
        r2: rel("e2", "2", "3", "T"),
        a: node("1", "X"),
        b: node("2", "X"),
        c: node("3", "X"),
      }),
    ]);
    expect([...neighborhood(graph, "1")].sort()).toEqual(["1", "2"]);
    expect([...neighborhood(graph, "2")].sort()).toEqual(["1", "2", "3"]);
  });

  it("clips a line to the card's border", () => {
    expect(clipToNode({ x: 0, y: 0 }, { x: 100, y: 0 }, 20, 10)).toEqual({ x: 10, y: 0 });
    expect(clipToNode({ x: 0, y: 0 }, { x: 0, y: -50 }, 20, 10)).toEqual({ x: 0, y: -5 });
    expect(clipToNode({ x: 0, y: 0 }, { x: 0, y: 0 }, 20, 10)).toEqual({ x: 0, y: 0 });
  });
});

describe("AI frames", () => {
  it("walks start, repair and a good done", () => {
    let state = applyAiEvent(AI_IDLE, { event: "start" });
    expect(state).toEqual({ phase: "running", message: "Generating…" });
    state = applyAiEvent(state, { event: "repair", error: "bad" });
    expect(state.phase).toBe("repair");
    state = applyAiEvent(state, { event: "chunk", data: "{}" });
    expect(state.phase).toBe("repair"); // a chunk after a repair does not look like a fresh start
    state = applyAiEvent(state, { event: "done", ok: true, final: "{}" });
    expect(state).toEqual({ phase: "done", message: "Generated" });
  });

  it("reports a rejected done and an error frame", () => {
    expect(applyAiEvent(AI_IDLE, { event: "done", ok: false, error: "no writes" })).toEqual({
      phase: "error",
      message: "Rejected: no writes",
    });
    expect(applyAiEvent(AI_IDLE, { event: "error", error: "boom" }).message).toBe("boom");
    expect(applyAiEvent(AI_IDLE, { event: "model" })).toBe(AI_IDLE);
  });

  it("takes editor text from chunks and from a non-empty final", () => {
    expect(aiText({ event: "chunk", data: "abc" })).toBe("abc");
    expect(aiText({ event: "done", ok: true, final: " x " })).toBe(" x ");
    expect(aiText({ event: "done", ok: false, final: "  " })).toBeNull();
    expect(aiText({ event: "start" })).toBeNull();
  });
});

describe("run state", () => {
  it("maps a response to a state and a status word", () => {
    const ok = runStateOf({ ok: true, notes: [], took_ms: 12, total: 1, hits: [] });
    expect(runStatus(ok)).toBe("Done · 12 ms");
    const bad = runStateOf({ ok: false, error: "nope", notes: ["n"], took_ms: 1, total: null, hits: [] });
    expect(bad).toEqual({ kind: "failed", message: "nope", notes: ["n"] });
    expect(runStatus(bad)).toBe("Error");
    expect(runStatus({ kind: "idle" })).toBe("Ready");
    expect(runStatus({ kind: "running" })).toBe("Running…");
  });
});

describe("schemaOutline", () => {
  it("lists Elasticsearch fields, including nested ones", () => {
    const groups = schemaOutline("elasticsearch", {
      indices: {
        videos: {
          doc_count: 1200,
          mappings: {
            properties: { title: { type: "text" }, stats: { properties: { views: { type: "long" } } } },
          },
        },
      },
    });
    expect(groups).toHaveLength(1);
    expect(groups[0]?.title).toBe("videos");
    expect(groups[0]?.note).toBe("1,200 documents");
    expect(groups[0]?.items.map((item) => [item.name, item.detail])).toEqual([
      ["title", "text"],
      ["stats", "object"],
      ["stats.views", "long"],
    ]);
  });

  it("writes a runnable Elasticsearch query for an index and for a field", () => {
    const [group] = schemaOutline("elasticsearch", {
      indices: { videos: { mappings: { properties: { title: { type: "text" } } } } },
    });
    expect(JSON.parse(group?.query ?? "{}")).toEqual({ query: { match_all: {} }, size: 25 });
    expect(JSON.parse(group?.items[0]?.query ?? "{}")).toEqual({
      query: { exists: { field: "title" } },
      _source: ["title"],
      size: 25,
    });
  });

  it("lists Qdrant vectors and payload keys", () => {
    const [group] = schemaOutline("qdrant", {
      collections: [
        {
          name: "chunks",
          points_count: 9,
          vectors_config: { dense: { size: 2048, distance: "Cosine" } },
          payload_schema: { video_id: { data_type: "keyword" } },
          observed_payload_keys: ["title", "video_id"],
        },
      ],
    });
    expect(group?.title).toBe("chunks");
    expect(group?.note).toBe("9 points");
    expect(group?.items.map((item) => [item.name, item.detail])).toEqual([
      ["vector · dense", "2048 dims, Cosine"],
      ["title", "payload"],
      ["video_id", "keyword (indexed)"],
    ]);
    expect(JSON.parse(group?.query ?? "{}")).toMatchObject({ op: "scroll", with_payload: true });
  });

  it("lists Neo4j labels with properties and the relationship patterns", () => {
    const groups = schemaOutline("neo4j", {
      labels: ["Video"],
      node_properties: { Video: [{ name: "title", types: ["String"] }] },
      relationship_patterns: [
        { src: "Video", rel: "BELONGS_TO", dst: "Channel", count: 4 },
        { src: "Document", rel: "MENTIONS", dst: "Entity", count: null },
      ],
    });
    expect(groups[0]).toMatchObject({ title: "(:Video)", note: "1 property" });
    expect(groups[0]?.items.map((item) => [item.name, item.detail])).toEqual([["title", "String"]]);
    expect(groups[1]?.items.map((item) => [item.name, item.detail])).toEqual([
      ["(:Video)-[:BELONGS_TO]->(:Channel)", "4 seen"],
      ["(:Document)-[:MENTIONS]->(:Entity)", "declared"],
    ]);
  });

  it("writes Cypher for a label, a property and a relationship, quoting names that need it", () => {
    const groups = schemaOutline("neo4j", {
      labels: ["Video", "AI Model"],
      node_properties: { Video: [{ name: "title", types: ["String"] }], "AI Model": [] },
      relationship_patterns: [{ src: "Video", rel: "BELONGS_TO", dst: "AI Model", count: 1 }],
    });
    expect(groups[0]?.query).toBe("MATCH (n:Video)\nRETURN n\nLIMIT 25");
    expect(groups[0]?.items[0]?.query).toBe(
      "MATCH (n:Video)\nWHERE n.title IS NOT NULL\nRETURN n.title AS title\nLIMIT 25",
    );
    expect(groups[1]?.query).toBe("MATCH (n:`AI Model`)\nRETURN n\nLIMIT 25");
    expect(groups[2]?.query).toBeNull();
    expect(groups[2]?.items[0]?.query).toBe(
      "MATCH (a:Video)-[r:BELONGS_TO]->(b:`AI Model`)\nRETURN a, r, b\nLIMIT 25",
    );
  });

  it("filters groups by title, and keeps only the matching fields of the rest", () => {
    const groups = schemaOutline("neo4j", {
      labels: ["Video", "Channel"],
      node_properties: {
        Video: [{ name: "title" }, { name: "upload_date" }],
        Channel: [{ name: "name" }, { name: "title" }],
      },
    });
    expect(filterSchema(groups, "").map((group) => group.title)).toEqual(["(:Video)", "(:Channel)"]);
    const byTitle = filterSchema(groups, "video");
    expect(byTitle.map((group) => group.title)).toEqual(["(:Video)"]);
    expect(byTitle[0]?.items).toHaveLength(2);
    const byField = filterSchema(groups, "UPLOAD");
    expect(byField.map((group) => [group.title, group.items.map((item) => item.name)])).toEqual([
      ["(:Video)", ["upload_date"]],
    ]);
    expect(filterSchema(groups, "zzz")).toEqual([]);
  });

  it("tolerates an empty or odd schema", () => {
    expect(schemaOutline("elasticsearch", {})).toEqual([]);
    expect(schemaOutline("qdrant", { collections: "nope" })).toEqual([]);
    expect(schemaOutline("neo4j", { labels: 3 })).toEqual([]);
  });
});

describe("groupHistory", () => {
  const entry = (id: number, body: string, prompt = "") => ({
    id,
    backend: "neo4j",
    body,
    prompt,
    created_at: `2026-10-30T10:0${id}:00Z`,
  });

  it("folds identical runs into their newest and keeps every id", () => {
    const groups = groupHistory([entry(4, "A"), entry(3, "B"), entry(2, "A"), entry(1, "A")]);
    expect(groups.map((group) => [group.id, group.ids, group.runs])).toEqual([
      [4, [4, 2, 1], 3],
      [3, [3], 1],
    ]);
    expect(groups[0]?.created_at).toBe("2026-10-30T10:04:00Z");
  });

  it("treats the same query with a different assistant prompt as another entry", () => {
    const groups = groupHistory([entry(2, "A", "one video"), entry(1, "A")]);
    expect(groups).toHaveLength(2);
  });
});

describe("whenLabel", () => {
  it("formats a timestamp and passes through one that does not parse", () => {
    expect(whenLabel("2026-10-29T10:00:00Z")).toMatch(/2026/);
    expect(whenLabel("not a date")).toBe("not a date");
  });
});
