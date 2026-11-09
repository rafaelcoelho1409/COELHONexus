/** Book Distiller Agentic RAG — Ask and Query route state, copy, and workbench limits. */

export const ASK_THREAD_PARAM = "thread";

export function askHref(bookId?: string | null, threadId?: string): string {
  const params = new URLSearchParams();
  if (bookId) params.set("book", bookId);
  if (threadId) params.set(ASK_THREAD_PARAM, threadId);
  const query = params.toString();
  return query ? `/bd/ask?${query}` : "/bd/ask";
}

export const THREAD_LABEL_MAX = 28;
export const ASK_FAILED_ANSWER_PREFIX = "Error generating answer:";
export const ASK_STAGES: ReadonlyArray<{ id: string; label: string }> = [
  { id: "retrieve", label: "Retrieve" },
  { id: "grade", label: "Grade" },
  { id: "generate", label: "Generate" },
  { id: "verify", label: "Verify" },
];
export const ASK_MODES: ReadonlyArray<{ id: string; label: string }> = [
  { id: "", label: "Auto" },
  { id: "fast", label: "Fast" },
  { id: "standard", label: "Standard" },
  { id: "deep", label: "Deep" },
];
export const ASK_EXAMPLES: ReadonlyArray<string> = [
  "Summarize the book's central argument.",
  "Which ideas connect the main chapters?",
  "What evidence supports the author's conclusions?",
];
export const HISTORY_POLL_MS = 2500;
export const RAG_INDEX_REFRESH_MS = 4000;
export const STALL_TICKS_THRESHOLD = 6;
export const SNIPPET_CAP = 220;

export type QueryBackend = "elasticsearch" | "qdrant" | "neo4j";
export type QueryLanguage = "json" | "cypher";

export const QUERY_BACKENDS: ReadonlyArray<{
  id: QueryBackend;
  label: string;
  language: QueryLanguage;
}> = [
  { id: "elasticsearch", label: "Elasticsearch", language: "json" },
  { id: "qdrant", label: "Qdrant", language: "json" },
  { id: "neo4j", label: "Neo4j", language: "cypher" },
];

export const QUERY_SCAFFOLDS: Record<QueryBackend, string> = {
  elasticsearch: [
    "{",
    '  "query": {',
    '    "multi_match": {',
    '      "query": "distributed systems",',
    '      "fields": ["title^3", "chapter", "content", "summary"],',
    '      "type": "best_fields"',
    "    }",
    "  },",
    '  "size": 10',
    "}",
  ].join("\n"),
  qdrant: JSON.stringify({ op: "scroll", limit: 10, with_payload: true }, null, 2),
  neo4j: [
    "// Browse the Book Distiller knowledge graph.",
    "MATCH (a)-[r]->(b)",
    "RETURN a, r, b",
    "LIMIT 25",
  ].join("\n"),
};

export const QUERY_INSERT_LIMIT = 25;
export const QUERY_SCHEMA_OPEN_UNDER = 4;
export const QUERY_AI_PLACEHOLDER = "e.g. Find passages about consistency across all books";
export const QUERY_HISTORY_LIMIT = 50;
export const QUERY_ROWS_STEP = 50;
export const QUERY_CELL_MAX = 160;
export const QUERY_WIDE_TEXT = 80;
export const QUERY_ID_CLIP = 20;
export const QUERY_TREE_OPEN_DEPTH = 2;
export const QUERY_GRAPH_MAX_NODES = 300;
export const QUERY_GRAPH_ITERATIONS = 260;
export const QUERY_GRAPH_SPACING = 240;
export const QUERY_GRAPH_GRAVITY = 0.2;
export const QUERY_GRAPH_REPULSION = 0.5;
export const QUERY_GRAPH_GAP = 56;
export const QUERY_GRAPH_NODE_WIDTH = 176;
export const QUERY_GRAPH_NODE_HEIGHT = 52;
export const QUERY_GRAPH_COLORS: ReadonlyArray<string> = [
  "var(--chart-1)",
  "var(--chart-2)",
  "var(--chart-3)",
  "var(--chart-4)",
  "var(--chart-5)",
];
