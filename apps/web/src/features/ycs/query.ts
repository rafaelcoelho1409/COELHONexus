/** YouTube Content Search — Query stage domain: flattening a backend's rows into table rows, column inference,
 * sort/filter/export, the Neo4j graph (collection + a fixed force layout), the AI frame reducer and the
 * schema outline. Pure — no React, no fetch. */

import type { QueryAiEvent, QueryHistoryEntry, RawHit, RawQueryResult } from "./entities";
import {
  QUERY_CELL_MAX,
  QUERY_GRAPH_COLORS,
  QUERY_GRAPH_GAP,
  QUERY_GRAPH_GRAVITY,
  QUERY_GRAPH_ITERATIONS,
  QUERY_GRAPH_NODE_HEIGHT,
  QUERY_GRAPH_NODE_WIDTH,
  QUERY_GRAPH_REPULSION,
  QUERY_GRAPH_SPACING,
  QUERY_ID_CLIP,
  QUERY_INSERT_LIMIT,
  QUERY_SCAFFOLDS,
  QUERY_WIDE_TEXT,
  type QueryBackend,
} from "./params";

export type Row = Record<string, unknown>;

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

// ---- Run state ----

/** Where the last Run stands. `failed` covers both a rejected or failed query (`ok: false`) and a lost connection. */
export type RunState =
  | { kind: "idle" }
  | { kind: "running" }
  | { kind: "failed"; message: string; notes: string[] }
  | { kind: "done"; result: RawQueryResult };

/** The one status word for the toolbar's live line. */
export function runStatus(state: RunState): string {
  switch (state.kind) {
    case "idle":
      return "Ready";
    case "running":
      return "Running…";
    case "failed":
      return "Error";
    case "done":
      return `Done · ${state.result.took_ms} ms`;
  }
}

/** The state a finished response puts the stage in. */
export function runStateOf(result: RawQueryResult): RunState {
  return result.ok
    ? { kind: "done", result }
    : { kind: "failed", message: result.error || "Unknown error", notes: result.notes };
}

// ---- Rows and columns ----

/** One backend hit as a flat table row: the store's own id/score first, then the document's fields. */
function flattenRow(backend: QueryBackend, hit: RawHit): Row {
  const raw = hit.raw;
  if (backend === "elasticsearch") {
    return { _index: raw._index, _id: raw._id, _score: raw._score, ...(asRecord(raw._source) ?? {}) };
  }
  if (backend === "qdrant") {
    return { id: raw.id, score: raw.score, ...(asRecord(raw.payload) ?? {}) };
  }
  return raw;
}

export function flattenRows(backend: QueryBackend, hits: ReadonlyArray<RawHit>): Row[] {
  return hits.map((hit) => flattenRow(backend, hit));
}

/** The union of every row's keys in first-seen order. */
export function inferColumns(rows: ReadonlyArray<Row>): string[] {
  const seen = new Set<string>();
  for (const row of rows) for (const key of Object.keys(row)) seen.add(key);
  return [...seen];
}

export type CellKind = "null" | "empty" | "number" | "boolean" | "text" | "array" | "object";

export function cellKind(value: unknown): CellKind {
  if (value === null || value === undefined) return "null";
  if (value === "") return "empty";
  if (Array.isArray(value)) return "array";
  if (typeof value === "object") return "object";
  if (typeof value === "number") return "number";
  if (typeof value === "boolean") return "boolean";
  return "text";
}

/** A cell as plain text — what filtering, sorting and the exports compare and print. */
export function cellText(value: unknown): string {
  const kind = cellKind(value);
  if (kind === "null" || kind === "empty") return "";
  if (kind === "array" || kind === "object") return JSON.stringify(value);
  if (kind === "number") return Number.isInteger(value) ? String(value) : (value as number).toFixed(4);
  return String(value);
}

/** The pill text for a nested value: `Array[7]` or `{4}`. */
export function nestedLabel(value: unknown): string {
  if (Array.isArray(value)) return `Array[${value.length}]`;
  const record = asRecord(value);
  return record ? `{${Object.keys(record).length}}` : "";
}

export function clip(text: string, max: number = QUERY_CELL_MAX): string {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

/** An identifier cut in the middle, so both its start and its end still read: `01884202-8de8…a40bae`. */
export function middleClip(text: string, max: number = QUERY_ID_CLIP): string {
  if (text.length <= max) return text;
  const keep = max - 1;
  const head = Math.ceil(keep / 2);
  return `${text.slice(0, head)}…${text.slice(text.length - (keep - head))}`;
}

// ---- Column plan ----

/** How a column is drawn: `text` gets room and clamps its cells, `id` is cut in the middle on one line, `number` stays
 * on one line, `plain` is everything else (short text, flags, nested-value pills). */
export type ColumnKind = "text" | "id" | "number" | "plain";
export type ColumnPlan = { column: string; kind: ColumnKind; hidden: boolean };

const isScore = (column: string) => column === "_score" || column === "score";
const isIdentifier = (column: string) =>
  column === "_index" || column === "_id" || column === "id" || column.endsWith("_id");

const isBlank = (value: unknown) => {
  const kind = cellKind(value);
  return kind === "null" || kind === "empty";
};

/** The table's columns in reading order, each with how to draw it and whether it starts hidden. The relevance score
 * comes first, the document's own fields next, identifiers last (the record sheet shows them whole). A column that is
 * empty in every row starts hidden, and so does `_index` when every hit comes from the same one. */
export function planColumns(rows: ReadonlyArray<Row>): ColumnPlan[] {
  const plan = inferColumns(rows).map((column): ColumnPlan => {
    const present = rows.map((row) => row[column]).filter((value) => !isBlank(value));
    const flat = present.filter((value) => cellKind(value) !== "array" && cellKind(value) !== "object");
    const longest = Math.max(0, ...flat.map((value) => cellText(value).length));
    let kind: ColumnKind = "plain";
    if (isIdentifier(column)) kind = "id";
    else if (flat.length > 0 && flat.every((value) => typeof value === "number")) kind = "number";
    else if (longest > QUERY_WIDE_TEXT) kind = "text";
    const constantIndex = column === "_index" && new Set(present.map(cellText)).size <= 1;
    return { column, kind, hidden: present.length === 0 || constantIndex };
  });
  const rank = (entry: ColumnPlan) => (isScore(entry.column) ? 0 : isIdentifier(entry.column) ? 2 : 1);
  return plan.sort((a, b) => rank(a) - rank(b));
}

// ---- Sort, filter, export ----

type SortDir = "asc" | "desc";
export type Sort = { column: string; dir: SortDir } | null;

/** Header click cycle: unsorted → ascending → descending → unsorted. */
export function nextSort(current: Sort, column: string): Sort {
  if (current?.column !== column) return { column, dir: "asc" };
  return current.dir === "asc" ? { column, dir: "desc" } : null;
}

export function sortRows(rows: ReadonlyArray<Row>, sort: Sort): Row[] {
  if (!sort) return [...rows];
  const sign = sort.dir === "asc" ? 1 : -1;
  const present = (row: Row) =>
    cellKind(row[sort.column]) !== "null" && cellKind(row[sort.column]) !== "empty";
  return [...rows].sort((a, b) => {
    // Missing values sink to the bottom whichever way the column is sorted.
    if (!present(a) || !present(b)) return Number(!present(a)) - Number(!present(b));
    const x = a[sort.column];
    const y = b[sort.column];
    if (typeof x === "number" && typeof y === "number") return (x - y) * sign;
    return cellText(x).localeCompare(cellText(y), undefined, { numeric: true, sensitivity: "base" }) * sign;
  });
}

/** Rows whose every filtered column contains the typed text (case-insensitive). Empty filters match all. */
export function filterRows(rows: ReadonlyArray<Row>, filters: Readonly<Record<string, string>>): Row[] {
  const active = Object.entries(filters)
    .map(([column, text]) => [column, text.trim().toLowerCase()] as const)
    .filter(([, text]) => text !== "");
  if (active.length === 0) return [...rows];
  return rows.filter((row) =>
    active.every(([column, text]) => cellText(row[column]).toLowerCase().includes(text)),
  );
}

function csvField(text: string): string {
  return /[",\n\r]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

export function toCsv(columns: ReadonlyArray<string>, rows: ReadonlyArray<Row>): string {
  const lines = [columns.map(csvField).join(",")];
  for (const row of rows) lines.push(columns.map((column) => csvField(cellText(row[column]))).join(","));
  return lines.join("\n");
}

export function toJsonText(rows: ReadonlyArray<Row>): string {
  return JSON.stringify(rows, null, 2);
}

// ---- Graph ----

export type GraphNode = {
  id: string;
  /** The first label — the node's colour and legend group. */
  label: string;
  display: string;
  properties: Record<string, unknown>;
};
type GraphEdge = {
  id: string;
  source: string;
  target: string;
  type: string;
  properties: Record<string, unknown>;
};
export type Graph = { nodes: GraphNode[]; edges: GraphEdge[] };

function nodeDisplay(id: string, properties: Record<string, unknown>): string {
  for (const key of ["title", "name", "id", "video_id", "channel_id"]) {
    const value = properties[key];
    if (typeof value === "string" && value) return value;
    if (typeof value === "number") return String(value);
  }
  return id;
}

/** Every distinct node and relationship anywhere in the rows — whether the Cypher returned `n`, `n, r, m` or a path.
 * A relationship whose end nodes were not returned is dropped (there is nothing to draw it between). */
export function collectGraph(hits: ReadonlyArray<RawHit>): Graph {
  const nodes = new Map<string, GraphNode>();
  const edges = new Map<string, GraphEdge>();

  const addNode = (value: Record<string, unknown>) => {
    const id = String(value.id ?? "");
    if (!id || nodes.has(id)) return;
    const labels = Array.isArray(value.labels) ? value.labels.map(String) : [];
    const properties = asRecord(value.properties) ?? {};
    nodes.set(id, { id, label: labels[0] ?? "Node", display: nodeDisplay(id, properties), properties });
  };
  const addEdge = (value: Record<string, unknown>) => {
    const id = String(value.id ?? "");
    if (!id || edges.has(id)) return;
    edges.set(id, {
      id,
      source: String(value.start ?? ""),
      target: String(value.end ?? ""),
      type: String(value.type ?? ""),
      properties: asRecord(value.properties) ?? {},
    });
  };
  const walk = (value: unknown) => {
    if (Array.isArray(value)) {
      for (const item of value) walk(item);
      return;
    }
    const record = asRecord(value);
    if (!record) return;
    if (record._kind === "node") addNode(record);
    else if (record._kind === "relationship") addEdge(record);
    else if (record._kind === "path") {
      walk(record.nodes);
      walk(record.rels);
    } else for (const item of Object.values(record)) walk(item);
  };
  for (const hit of hits) walk(hit.raw);

  return {
    nodes: [...nodes.values()],
    edges: [...edges.values()].filter((edge) => nodes.has(edge.source) && nodes.has(edge.target)),
  };
}

/** Label → colour token, in first-seen order; a sixth label starts the palette over (the legend and the node's
 * own text name the label, so colour never carries the meaning alone). */
export function labelColors(nodes: ReadonlyArray<GraphNode>): Map<string, string> {
  const colors = new Map<string, string>();
  for (const node of nodes) {
    if (!colors.has(node.label)) {
      colors.set(node.label, QUERY_GRAPH_COLORS[colors.size % QUERY_GRAPH_COLORS.length] ?? "var(--chart-5)");
    }
  }
  return colors;
}

export type Point = { x: number; y: number };

/** Fruchterman–Reingold for one connected cluster, from a deterministic spiral start: the same input always gives the
 * same picture. */
function layoutCluster(graph: Graph): Map<string, Point> {
  const { nodes, edges } = graph;
  const count = nodes.length;
  const k = QUERY_GRAPH_SPACING;
  const index = new Map(nodes.map((node, i) => [node.id, i] as const));
  const x = new Float64Array(count);
  const y = new Float64Array(count);
  for (let i = 0; i < count; i += 1) {
    const radius = k * 0.7 * Math.sqrt(i);
    const angle = i * 2.399963; // the golden angle, so the start has no overlaps
    x[i] = radius * Math.cos(angle);
    y[i] = radius * Math.sin(angle);
  }
  const links = edges.flatMap((edge) => {
    const from = index.get(edge.source);
    const to = index.get(edge.target);
    return from === undefined || to === undefined || from === to ? [] : [[from, to] as const];
  });

  const dx = new Float64Array(count);
  const dy = new Float64Array(count);
  for (let step = 0; step < QUERY_GRAPH_ITERATIONS && count > 1; step += 1) {
    const heat = k * 0.6 * (1 - step / QUERY_GRAPH_ITERATIONS) + 1;
    dx.fill(0);
    dy.fill(0);
    for (let i = 0; i < count; i += 1) {
      for (let j = i + 1; j < count; j += 1) {
        const ex = (x[i] ?? 0) - (x[j] ?? 0);
        const ey = (y[i] ?? 0) - (y[j] ?? 0);
        const dist = Math.max(Math.hypot(ex, ey), 0.01);
        const push = (QUERY_GRAPH_REPULSION * k * k) / dist / dist;
        dx[i] = (dx[i] ?? 0) + ex * push;
        dy[i] = (dy[i] ?? 0) + ey * push;
        dx[j] = (dx[j] ?? 0) - ex * push;
        dy[j] = (dy[j] ?? 0) - ey * push;
      }
    }
    for (const [from, to] of links) {
      const ex = (x[from] ?? 0) - (x[to] ?? 0);
      const ey = (y[from] ?? 0) - (y[to] ?? 0);
      const dist = Math.max(Math.hypot(ex, ey), 0.01);
      const pull = dist / k;
      dx[from] = (dx[from] ?? 0) - ex * pull;
      dy[from] = (dy[from] ?? 0) - ey * pull;
      dx[to] = (dx[to] ?? 0) + ex * pull;
      dy[to] = (dy[to] ?? 0) + ey * pull;
    }
    for (let i = 0; i < count; i += 1) {
      // A weak pull to the middle keeps unconnected nodes from flying off the canvas.
      const gx = (dx[i] ?? 0) - (x[i] ?? 0) * QUERY_GRAPH_GRAVITY;
      const gy = (dy[i] ?? 0) - (y[i] ?? 0) * QUERY_GRAPH_GRAVITY;
      const length = Math.hypot(gx, gy) || 1;
      const move = Math.min(length, heat);
      x[i] = (x[i] ?? 0) + (gx / length) * move;
      y[i] = (y[i] ?? 0) + (gy / length) * move;
    }
  }
  return new Map(
    nodes.map((node, i) => [node.id, { x: Math.round(x[i] ?? 0), y: Math.round(y[i] ?? 0) }] as const),
  );
}

/** The graph split into its connected pieces, in first-seen order. */
function clusters(graph: Graph): Graph[] {
  const parent = new Map(graph.nodes.map((node) => [node.id, node.id] as const));
  const find = (id: string): string => {
    let root = id;
    for (;;) {
      const up = parent.get(root);
      if (up === undefined || up === root) return root;
      root = up;
    }
  };
  for (const edge of graph.edges) {
    if (parent.has(edge.source) && parent.has(edge.target)) parent.set(find(edge.source), find(edge.target));
  }
  const byRoot = new Map<string, Graph>();
  for (const node of graph.nodes) {
    const root = find(node.id);
    const group = byRoot.get(root) ?? { nodes: [], edges: [] };
    group.nodes.push(node);
    byRoot.set(root, group);
  }
  for (const edge of graph.edges) byRoot.get(find(edge.source))?.edges.push(edge);
  return [...byRoot.values()];
}

/** Card centres for every node. Each connected cluster is laid out on its own, then the clusters are packed in rows
 * (tallest first, roughly landscape) — so separate pieces of a result sit side by side instead of drifting apart, and
 * the picture is as large as it can be on screen. Runs once per result, so nothing moves afterwards. */
export function layoutGraph(graph: Graph): Map<string, Point> {
  const halfW = QUERY_GRAPH_NODE_WIDTH / 2;
  const halfH = QUERY_GRAPH_NODE_HEIGHT / 2;
  const boxes = clusters(graph).map((cluster) => {
    const local = layoutCluster(cluster);
    const points = [...local.values()];
    const left = Math.min(...points.map((p) => p.x)) - halfW;
    const top = Math.min(...points.map((p) => p.y)) - halfH;
    const right = Math.max(...points.map((p) => p.x)) + halfW;
    const bottom = Math.max(...points.map((p) => p.y)) + halfH;
    return {
      local,
      left,
      top,
      width: right - left + QUERY_GRAPH_GAP,
      height: bottom - top + QUERY_GRAPH_GAP,
    };
  });
  boxes.sort((a, b) => b.height - a.height);
  const area = boxes.reduce((sum, box) => sum + box.width * box.height, 0);
  const rowWidth = Math.max(...boxes.map((box) => box.width), Math.sqrt(area * 1.6));

  const placed = new Map<string, Point>();
  let cursorX = 0;
  let cursorY = 0;
  let rowHeight = 0;
  for (const box of boxes) {
    if (cursorX > 0 && cursorX + box.width > rowWidth) {
      cursorX = 0;
      cursorY += rowHeight;
      rowHeight = 0;
    }
    for (const [id, p] of box.local)
      placed.set(id, { x: p.x - box.left + cursorX, y: p.y - box.top + cursorY });
    cursorX += box.width;
    rowHeight = Math.max(rowHeight, box.height);
  }

  // Centre the whole picture on the origin.
  const all = [...placed.values()];
  if (all.length === 0) return placed;
  const midX = (Math.min(...all.map((p) => p.x)) + Math.max(...all.map((p) => p.x))) / 2;
  const midY = (Math.min(...all.map((p) => p.y)) + Math.max(...all.map((p) => p.y))) / 2;
  return new Map(
    [...placed.entries()].map(
      ([id, p]) => [id, { x: Math.round(p.x - midX), y: Math.round(p.y - midY) }] as const,
    ),
  );
}

/** The ids of a node and everything one relationship away — what "focus" keeps lit. */
export function neighborhood(graph: Graph, id: string): Set<string> {
  const ids = new Set([id]);
  for (const edge of graph.edges) {
    if (edge.source === id) ids.add(edge.target);
    if (edge.target === id) ids.add(edge.source);
  }
  return ids;
}

/** Where a line from the centre of one node card toward another point leaves the card's rectangle — an edge starts
 * and ends at the card borders, so its arrowhead is never hidden under the card. */
export function clipToNode(center: Point, toward: Point, width: number, height: number): Point {
  const dx = toward.x - center.x;
  const dy = toward.y - center.y;
  if (dx === 0 && dy === 0) return center;
  const scale = Math.min(
    dx === 0 ? Number.POSITIVE_INFINITY : width / 2 / Math.abs(dx),
    dy === 0 ? Number.POSITIVE_INFINITY : height / 2 / Math.abs(dy),
  );
  return { x: center.x + dx * scale, y: center.y + dy * scale };
}

// ---- AI text-to-query ----

type AiPhase = "idle" | "running" | "repair" | "done" | "error";
export type AiState = { phase: AiPhase; message: string };

export const AI_IDLE: AiState = { phase: "idle", message: "" };
export const AI_STARTED: AiState = { phase: "running", message: "Generating…" };

/** The status line after one SSE frame. The query text itself is applied by the caller (`aiText`). */
export function applyAiEvent(state: AiState, event: QueryAiEvent): AiState {
  switch (event.event) {
    case "start":
    case "chunk":
      return state.phase === "repair" ? state : AI_STARTED;
    case "repair":
      return { phase: "repair", message: "Self-repairing…" };
    case "done":
      return event.ok
        ? { phase: "done", message: "Generated" }
        : { phase: "error", message: `Rejected: ${event.error || "unknown"}` };
    case "error":
      return { phase: "error", message: event.error || "The assistant failed" };
    default:
      return state;
  }
}

/** The editor text after one frame, or null to leave it alone: a chunk replaces the draft, and the final
 * (cleaned, possibly self-repaired) text wins when `done` arrives. */
export function aiText(event: QueryAiEvent): string | null {
  if (event.event === "chunk" && event.data) return event.data;
  if (event.event === "done" && event.final?.trim()) return event.final;
  return null;
}

// ---- Schema outline ----

/** `query` is a runnable starting query for the item, or null when there is no sensible one. */
type SchemaItem = { name: string; detail: string; query: string | null };
export type SchemaGroup = { title: string; note: string | null; query: string | null; items: SchemaItem[] };

/** A Cypher label, type or property name, quoted with backticks when it is not a plain identifier ("AI Model"). */
function cypherName(name: string): string {
  return /^[A-Za-z_][A-Za-z0-9_]*$/.test(name) ? name : `\`${name.replaceAll("`", "``")}\``;
}

const jsonQuery = (body: unknown) => JSON.stringify(body, null, 2);

function esFields(properties: Record<string, unknown>, prefix = ""): SchemaItem[] {
  return Object.entries(properties).flatMap(([name, spec]) => {
    const record = asRecord(spec) ?? {};
    const path = `${prefix}${name}`;
    const nested = asRecord(record.properties);
    const own: SchemaItem = {
      name: path,
      detail: typeof record.type === "string" ? record.type : "object",
      query: jsonQuery({ query: { exists: { field: path } }, _source: [path], size: QUERY_INSERT_LIMIT }),
    };
    return nested ? [own, ...esFields(nested, `${path}.`)] : [own];
  });
}

/** Groups that match the typed text: a group whose title matches keeps all its rows, otherwise only its matching rows
 * stay. Empty text returns everything. */
export function filterSchema(groups: ReadonlyArray<SchemaGroup>, text: string): SchemaGroup[] {
  const needle = text.trim().toLowerCase();
  if (needle === "") return [...groups];
  return groups.flatMap((group) => {
    if (group.title.toLowerCase().includes(needle)) return [group];
    const items = group.items.filter((item) => item.name.toLowerCase().includes(needle));
    return items.length > 0 ? [{ ...group, items }] : [];
  });
}

function countNote(count: unknown, noun: string): string | null {
  return typeof count === "number" ? `${count.toLocaleString("en-US")} ${noun}` : null;
}

/** A backend's schema as groups of name/detail rows, for authoring a query. Reads defensively: a field the server
 * adds later, or leaves out, only changes what is listed. */
export function schemaOutline(
  backend: QueryBackend,
  schema: Readonly<Record<string, unknown>>,
): SchemaGroup[] {
  if (backend === "elasticsearch") {
    return Object.entries(asRecord(schema.indices) ?? {}).map(([name, index]) => {
      const record = asRecord(index) ?? {};
      const mappings = asRecord(record.mappings);
      return {
        title: name,
        note: countNote(record.doc_count, "documents"),
        query: jsonQuery({ query: { match_all: {} }, size: QUERY_INSERT_LIMIT }),
        items: esFields(asRecord(mappings?.properties) ?? {}),
      };
    });
  }
  if (backend === "qdrant") {
    const collections = Array.isArray(schema.collections) ? schema.collections : [];
    return collections.map((collection) => {
      const record = asRecord(collection) ?? {};
      const payload = asRecord(record.payload_schema) ?? {};
      const vectors = Object.entries(asRecord(record.vectors_config) ?? {}).map(([name, spec]) => {
        const config = asRecord(spec) ?? {};
        const size = typeof config.size === "number" ? `${config.size} dims` : "";
        const distance = typeof config.distance === "string" ? config.distance : "";
        return {
          name: `vector · ${name}`,
          detail: [size, distance].filter(Boolean).join(", ") || "vector",
          query: null,
        };
      });
      const keys = Array.isArray(record.observed_payload_keys)
        ? record.observed_payload_keys.map(String)
        : [];
      const fields = [...new Set([...keys, ...Object.keys(payload)])].map((name) => {
        const type = asRecord(payload[name])?.data_type;
        return { name, detail: typeof type === "string" ? `${type} (indexed)` : "payload", query: null };
      });
      return {
        title: typeof record.name === "string" ? record.name : "collection",
        note: countNote(record.points_count, "points"),
        query: QUERY_SCAFFOLDS.qdrant,
        items: [...vectors, ...fields],
      };
    });
  }
  const properties = asRecord(schema.node_properties) ?? {};
  const labels = Array.isArray(schema.labels) ? schema.labels.map(String) : Object.keys(properties);
  const nodeGroups = labels.map((label): SchemaGroup => {
    const node = cypherName(label);
    const items = (Array.isArray(properties[label]) ? properties[label] : []).flatMap(
      (prop: unknown): SchemaItem[] => {
        const record = asRecord(prop);
        if (!record || typeof record.name !== "string") return [];
        const types = Array.isArray(record.types) ? record.types.map(String).join(" | ") : "";
        const name = cypherName(record.name);
        return [
          {
            name: record.name,
            detail: types || "property",
            query: `MATCH (n:${node})\nWHERE n.${name} IS NOT NULL\nRETURN n.${name} AS ${name}\nLIMIT ${QUERY_INSERT_LIMIT}`,
          },
        ];
      },
    );
    const noun = items.length === 1 ? "property" : "properties";
    return {
      title: `(:${label})`,
      note: `${items.length} ${noun}`,
      query: `MATCH (n:${node})\nRETURN n\nLIMIT ${QUERY_INSERT_LIMIT}`,
      items,
    };
  });
  const patterns = (Array.isArray(schema.relationship_patterns) ? schema.relationship_patterns : []).flatMap(
    (pattern: unknown): SchemaItem[] => {
      const record = asRecord(pattern);
      if (!record) return [];
      const count =
        typeof record.count === "number" ? `${record.count.toLocaleString("en-US")} seen` : "declared";
      const src = String(record.src);
      const rel = String(record.rel);
      const dst = String(record.dst);
      return [
        {
          name: `(:${src})-[:${rel}]->(:${dst})`,
          detail: count,
          query: `MATCH (a:${cypherName(src)})-[r:${cypherName(rel)}]->(b:${cypherName(dst)})\nRETURN a, r, b\nLIMIT ${QUERY_INSERT_LIMIT}`,
        },
      ];
    },
  );
  return patterns.length > 0
    ? [...nodeGroups, { title: "Relationships", note: null, query: null, items: patterns }]
    : nodeGroups;
}

// ---- History ----

/** Saved runs that wrote the same query (and the same assistant prompt), shown once. `id` is the newest run and `ids`
 * every run, so deleting the entry removes them all. */
export type HistoryGroup = {
  id: number;
  ids: number[];
  body: string;
  prompt: string;
  created_at: string;
  runs: number;
};

/** The entries (newest first, as the server sends them) with identical runs folded into their newest. */
export function groupHistory(entries: ReadonlyArray<QueryHistoryEntry>): HistoryGroup[] {
  const groups = new Map<string, HistoryGroup>();
  for (const entry of entries) {
    const key = `${entry.prompt}\u0000${entry.body}`;
    const group = groups.get(key);
    if (group) {
      group.ids.push(entry.id);
      group.runs += 1;
    } else {
      groups.set(key, {
        id: entry.id,
        ids: [entry.id],
        body: entry.body,
        prompt: entry.prompt,
        created_at: entry.created_at,
        runs: 1,
      });
    }
  }
  return [...groups.values()];
}

/** A saved query's timestamp in the reader's locale; the raw string when it does not parse. */
export function whenLabel(iso: string): string {
  const time = Date.parse(iso);
  return Number.isNaN(time)
    ? iso
    : new Date(time).toLocaleString("en-US", { dateStyle: "medium", timeStyle: "short" });
}
