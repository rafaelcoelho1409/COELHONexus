import type { Section } from "@/shared/layout/section";

/** The title in the second navbar; Settings has no stages. */
export const SETTINGS_SECTION: Section = {
  href: "/settings",
  title: "Settings",
  description: "Connections used by your tools",
  tabs: [],
};

export const SETTINGS_TABS = [
  { key: "llm", label: "LLM" },
  { key: "embedding", label: "Embedding" },
  { key: "search", label: "Search" },
] as const;
export type SettingsTabKey = (typeof SETTINGS_TABS)[number]["key"];

/** The two saved connections: the chat endpoint and the embedding endpoint. */
export type ConnectionKind = "llm" | "embedding";

export const SEARCH_PROVIDER_IDS = ["coelho_search", "tavily", "exa", "jina"] as const;
export type SearchProviderId = (typeof SEARCH_PROVIDER_IDS)[number];
export const CUSTOM_SEARCH_PROVIDER_ID: SearchProviderId = "coelho_search";
export const SEARCH_PROVIDER_ACCOUNT_URLS: Partial<Record<SearchProviderId, string>> = {
  tavily: "https://app.tavily.com/home",
  exa: "https://dashboard.exa.ai/api-keys",
  jina: "https://jina.ai/api-dashboard/key-manager/",
};

/** PUT body. `api_key`: omit = keep the stored key, `""` = delete it, otherwise replace it. */
export type SaveBody = { url: string; model: string; api_key?: string };

/** The migration task poll re-reads the worker's task status this often. */
export const MIGRATION_POLL_MS = 3_000;

/** After a terminal task state, wait this long before re-reading the migration status. */
export const MIGRATION_RECHECK_MS = 2_000;

/** Celery states after which no further task polling is useful. */
export const TERMINAL_TASK_STATES = ["SUCCESS", "FAILURE", "REVOKED"] as const;
