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
/** Trigger-label trim before ellipsis in the thread picker. */
export const THREAD_LABEL_MAX = 16;
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
