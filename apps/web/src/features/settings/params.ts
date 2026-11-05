import type { Section } from "@/shared/layout/section";

/** The title in the second navbar; Settings has no stages. */
export const SETTINGS_SECTION: Section = {
  href: "/settings",
  title: "Settings",
  description: "The LLM and embedding connections the tools call",
  tabs: [],
};

/** The two saved connections: the chat endpoint and the embedding endpoint. */
export type ConnectionKind = "llm" | "embedding";

/** PUT body. `api_key`: omit = keep the stored key, `""` = delete it, otherwise replace it. */
export type SaveBody = { url: string; model: string; api_key?: string };

/** Fallback model selectors for chat and embedding endpoints. */
export const DEFAULT_MODELS: Record<ConnectionKind, string> = {
  llm: "fgts-va",
  embedding: "auto",
};

/** The migration task poll re-reads the worker's task status this often. */
export const MIGRATION_POLL_MS = 3_000;

/** After a terminal task state, wait this long before re-reading the migration status. */
export const MIGRATION_RECHECK_MS = 2_000;

/** Celery states after which no further task polling is useful. */
export const TERMINAL_TASK_STATES = ["SUCCESS", "FAILURE", "REVOKED"] as const;
