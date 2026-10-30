/** YouTube Content Search — stage table, source-mode table, URL builders and the per-browser preference
 * keys. The stage shape mirrors `features/dd/params.ts`; YCS has no per-framework slug, so hrefs take no
 * argument. */

export type YcsStage = "source" | "ingestion" | "ask" | "query";

export const YCS_STAGES: ReadonlyArray<{ key: YcsStage; label: string; href: string; tab: boolean }> = [
  { key: "source", label: "Source", href: "/ycs", tab: true },
  { key: "ingestion", label: "Ingestion", href: "/ycs/ingestion", tab: true },
  { key: "ask", label: "Ask", href: "/ycs/ask", tab: true },
  { key: "query", label: "Query", href: "/ycs/query", tab: true },
];

export function stageHref(stage: YcsStage): string {
  const found = YCS_STAGES.find((s) => s.key === stage);
  return found ? found.href : "/ycs";
}

export function stageByHref(href: string): YcsStage {
  const found = YCS_STAGES.find((s) => s.href === href);
  return found ? found.key : "source";
}

/** Where a finished pipeline hand-off lands: the ingestion stage carries all three phase ids. */
export function ingestionHref(phaseIds: { extract: string; qdrant: string; neo4j: string }): string {
  const query = new URLSearchParams({
    extract: phaseIds.extract,
    qdrant: phaseIds.qdrant,
    neo4j: phaseIds.neo4j,
  });
  return `/ycs/ingestion?${query.toString()}`;
}

export type SourceMode = "search" | "videos" | "playlist" | "channel";

export const SOURCE_MODES: ReadonlyArray<{ key: SourceMode; label: string }> = [
  { key: "search", label: "Search" },
  { key: "videos", label: "Videos" },
  { key: "playlist", label: "Playlist" },
  { key: "channel", label: "Channel" },
];

// Per-browser preferences — the only things localStorage is allowed to hold.
export const RESULTS_DENSITY_STORAGE_KEY = "ycs:results:density";
export const PAGE_SIZE_STORAGE_KEY = "ycs:results:page_size";
export const VIDEOS_BUFFER_STORAGE_KEY = "ycs:videos:buffer";
export const PIPELINE_ACTIVE_STORAGE_KEY = "ycs:pipeline:active";

export const DENSITY_DEFAULT = "compact";
export const PAGE_SIZE_DEFAULT = 25;
export const PAGE_SIZES: ReadonlyArray<number> = [10, 25, 50, 100, 200];
/** yt-dlp walks a channel/playlist page in ~3–6 s; 100 rows per page keeps the wait reasonable. */
export const PICKER_PAGE_SIZE = 100;
/** "Ingest all" hands a whole channel or playlist to the server; above this many videos it asks first, with the count. */
export const INGEST_ALL_CONFIRM_ABOVE = 50;

// ---- Pipeline panel ----
/** 700 ms (the legacy console's value): Phase 2 finishes in ~5 s, and at 1500 ms the poll only caught
 * 0–1 intermediate states, so the bars jumped 2%→100% instead of advancing. */
export const PIPELINE_POLL_MS = 700;
/** Slow runs ease off after 5 minutes — Phase 3/4 chains can sit for tens of minutes. */
export const PIPELINE_POLL_BACKOFF_MS = 3000;
export const PIPELINE_POLL_BACKOFF_AFTER_MS = 5 * 60 * 1000;
/** Matches the backend's Redis TTL for a dispatch, so abandoned panels don't linger forever. */
export const PIPELINE_STORAGE_TTL_MS = 24 * 60 * 60 * 1000;
/** Token counters don't need bar-poll freshness; one extra fetch per ~5 s. */
export const LLM_USAGE_REFRESH_MS = 5000;
/** Auto-hide delay for the in-page notice. */
export const NOTICE_MS = 6000;

// ---- Library ----
export const LIBRARY_PAGE_SIZE = 50;
export const LIBRARY_SEARCH_DEBOUNCE_MS = 250;

// ---- Ask ----
/** Ask thread ids are server-known once a turn lands, so they live in the URL (`?thread=`), not in
 * localStorage. */
export const ASK_THREAD_PARAM = "thread";

/** The Ask stage, on one thread (`?thread=`) or fresh (no argument). */
export function askHref(threadId?: string): string {
  return threadId ? `/ycs/ask?${ASK_THREAD_PARAM}=${encodeURIComponent(threadId)}` : "/ycs/ask";
}
/** Trigger-label trim before ellipsis in the thread picker (the thread's first question). */
export const THREAD_LABEL_MAX = 28;
/** The text the server puts in place of an answer when the model call fails (the generate step has no error event). */
export const ASK_FAILED_ANSWER_PREFIX = "Error generating answer:";
/** Stages of the adaptive RAG graph, in pill order. Keys match the LangGraph stage mapping. */
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
/** Canned prompts shown on the empty state; they fill the composer when clicked. */
export const ASK_EXAMPLES: ReadonlyArray<string> = [
  "What are the main themes across all videos?",
  "Compare the recommendations made by each channel.",
  "What contradictions appear between sources?",
];
/** Refresh-recovery: the backend persists thinking_state snapshots every ~2.5 s. */
export const HISTORY_POLL_MS = 2500;
/** After this many consecutive identical snapshots (~15 s of flatline) the recovered turn is marked
 * stalled — in practice that only happens on a backend restart. */
export const STALL_TICKS_THRESHOLD = 6;
/** Defensive second cap on a source-card excerpt (the backend caps at 220 chars first). */
export const SNIPPET_CAP = 220;

// ---- Query ----
export type QueryBackend = "elasticsearch" | "qdrant" | "neo4j";
export type QueryLanguage = "json" | "cypher";

export const QUERY_BACKENDS: ReadonlyArray<{ id: QueryBackend; label: string; language: QueryLanguage }> = [
  { id: "elasticsearch", label: "Elasticsearch", language: "json" },
  { id: "qdrant", label: "Qdrant", language: "json" },
  { id: "neo4j", label: "Neo4j", language: "cypher" },
];

/** What each backend's editor starts with — a runnable query, so Run works the moment a backend is picked. */
export const QUERY_SCAFFOLDS: Record<QueryBackend, string> = {
  // The field list stays on one line: ten lines, not fifteen, so the results start higher on the page.
  elasticsearch: [
    "{",
    '  "query": {',
    '    "multi_match": {',
    '      "query": "transformer attention",',
    '      "fields": ["title^3", "description", "channel", "content"],',
    '      "type": "best_fields"',
    "    }",
    "  },",
    '  "size": 10',
    "}",
  ].join("\n"),
  qdrant: JSON.stringify({ op: "scroll", limit: 10, with_payload: true }, null, 2),
  // Whole nodes and relationships, so the first Run opens on the Graph view (a query that returns plain values has none).
  neo4j: [
    "// Browse the YCS knowledge graph: return whole nodes and relationships to see them as a graph.",
    "MATCH (a)-[r]->(b)",
    "RETURN a, r, b",
    "LIMIT 25",
  ].join("\n"),
};

/** Row cap of a query the Schema sheet writes for a label, an index or a field. */
export const QUERY_INSERT_LIMIT = 25;
/** The Schema sheet opens every group when a backend has this few; more than that it starts folded (Neo4j lists
 * dozens of labels). */
export const QUERY_SCHEMA_OPEN_UNDER = 4;

export const QUERY_AI_PLACEHOLDER = "e.g. List the 10 most recent videos from each channel";
export const QUERY_HISTORY_LIMIT = 50;
/** Table rows painted at first and per "Show more" — a result set can be thousands of rows, a screen holds dozens. */
export const QUERY_ROWS_STEP = 50;
/** Longest cell text before it is cut with an ellipsis (the full value stays in the title and the inspector). */
export const QUERY_CELL_MAX = 160;
/** A column whose longest value is longer than this is a text column: it gets room and its cells clamp to a few lines. */
export const QUERY_WIDE_TEXT = 80;
/** Longest an identifier shows in a table cell before its middle is cut (the record sheet has the whole value). */
export const QUERY_ID_CLIP = 20;
/** JSON tree: how many levels start open — two, so a hit list opens on each hit's `_source` or payload. */
export const QUERY_TREE_OPEN_DEPTH = 2;
/** Graph: the largest result drawn; above it the view asks for a tighter query and the table stays complete. */
export const QUERY_GRAPH_MAX_NODES = 300;
/** Graph: fixed layout settings — the layout runs once per result, so nothing moves afterwards. */
export const QUERY_GRAPH_ITERATIONS = 260;
/** Ideal distance between linked nodes; wider than a node card so neighbours never overlap. */
export const QUERY_GRAPH_SPACING = 240;
/** Pull toward the middle per iteration: strong enough that separate clusters settle side by side instead of drifting far apart. */
export const QUERY_GRAPH_GRAVITY = 0.2;
/** Strength of the push between any two nodes, against the pull of a relationship; below 1 keeps clusters compact. */
export const QUERY_GRAPH_REPULSION = 0.5;
/** Empty space kept between two separate clusters of a graph. */
export const QUERY_GRAPH_GAP = 56;
export const QUERY_GRAPH_NODE_WIDTH = 176;
export const QUERY_GRAPH_NODE_HEIGHT = 52;
/** Categorical node colours: the five chart tokens, assigned to labels in first-seen order. */
export const QUERY_GRAPH_COLORS: ReadonlyArray<string> = [
  "var(--chart-1)",
  "var(--chart-2)",
  "var(--chart-3)",
  "var(--chart-4)",
  "var(--chart-5)",
];
