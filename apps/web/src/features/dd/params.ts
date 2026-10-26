/** Docs Distiller stages. `tab` marks the ones shown in the tab strip, as in the legacy `_STAGES`
 * (shared/urls.py): Planner and Synth run side by side on the Pipeline tab, so they have no tab of their own;
 * their URLs stay reachable as deep links to a single stage. */
export const DD_STAGES = [
  { key: "catalog", label: "Catalog", href: "/dd", tab: true },
  { key: "ingestion", label: "Ingestion", href: "/dd/ingestion", tab: true },
  { key: "pipeline", label: "Pipeline", href: "/dd/pipeline", tab: true },
  { key: "planner", label: "Planner", href: "/dd/planner", tab: false },
  { key: "synth", label: "Synth", href: "/dd/synth", tab: false },
  { key: "study", label: "Study", href: "/dd/study", tab: true },
] as const;

export type DdStage = (typeof DD_STAGES)[number]["key"];

/** The two graph-driven stages that stream progress and share a run lock. */
export type RunStage = "planner" | "synth";

/** Catalog never carries a slug; every other stage carries it when present. */
export function stageHref(stage: DdStage, slug?: string | null): string {
  const entry = DD_STAGES.find((s) => s.key === stage);
  const base = entry ? entry.href : "/dd";
  return stage !== "catalog" && slug ? `${base}?slug=${encodeURIComponent(slug)}` : base;
}

export function ingestionHref(slug: string | null, runId?: string | null): string {
  const qs = new URLSearchParams();
  if (slug) qs.set("slug", slug);
  if (runId) qs.set("run", runId);
  const qsStr = qs.toString();
  return qsStr ? `/dd/ingestion?${qsStr}` : "/dd/ingestion";
}

/** The study reader, opened on one chapter. */
export function chapterStudyHref(slug: string, chapterId: string): string {
  return `/dd/study?slug=${encodeURIComponent(slug)}&chapter=${encodeURIComponent(chapterId)}`;
}

/** Every run thread id starts with this; the server finds a framework's runs by it. */
export const THREAD_PREFIX = "docs-distiller";

/** Backend run modes. */
export const PLANNER_MODE = "llm";
export const SYNTH_MODE = "quality";

/** The study reader shows this artifact of a rendered chapter. */
export const CHAPTER_ARTIFACT = "README.md";

/** An ingestion run's status is re-read this often until it reaches a terminal state. */
export const RUN_POLL_MS = 1_500;

/** The list of in-flight ingestion runs (page-reload recovery) is re-read this often. */
export const ACTIVE_RUN_REFRESH_MS = 8_000;

/** Which graph nodes the server implements changes only on deploy. */
export const STAGE_INFO_STALE_MS = 60_000;

/** The cross-stage run registry is re-read this often (and on window focus). */
export const ACTIVE_REFRESH_MS = 10_000;

/** An in-page notice hides itself after this long. */
export const NOTICE_MS = 8_000;

/** A live run's LLM counters are re-read this often. */
export const USAGE_REFRESH_MS = 4_000;

/** The node drawer's activity log keeps this many of the newest stream events. */
export const EVENT_LOG_LIMIT = 200;

/** A cancel releases the UI after this long even if the server's terminal event never lands. */
export const CANCEL_SAFETY_MS = 5_000;

/** Node checkpoints commit a tick after the stream's `done` event: re-read the state at these offsets. */
export const GRAPH_STATE_RETRY_DELAYS_MS = [0, 250, 400, 550, 700, 850, 1_000] as const;

/** Opt-in "start synth when the planner finishes", remembered per browser. Same key as the FastHTML page. */
export const AUTO_CHAIN_STORAGE_KEY = "dd:pipeline:autochain";

/** After the planner finishes its lock needs a moment to clear before synth can start. */
export const AUTO_CHAIN_POLL_MS = 300;
export const AUTO_CHAIN_MAX_WAIT_MS = 10_000;
