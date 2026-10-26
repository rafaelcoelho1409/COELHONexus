/** Docs Distiller ingestion — run progress view, manifest summary and the file-explorer grouping. */

import type { CatalogEntry, LibraryItem, Manifest, ManifestEntry, RunStatus } from "../entities";
import { tileLogos, tileName } from "./catalog";
import { fmtAge, fmtBytes } from "./format";
import { itemDisplayName, itemLogos } from "./library";

/**
 * Which framework a run belongs to, for its progress box: the catalog knows it (name and logos) even on a first
 * ingestion, when the library has nothing yet; the library covers a framework the catalog no longer lists; the
 * slug is the last resort.
 */
export function runFramework(
  slug: string,
  catalog: readonly CatalogEntry[] | undefined,
  library: readonly LibraryItem[] | undefined,
): { name: string; logos: string[] } {
  const entry = catalog?.find((candidate) => candidate.slug === slug);
  if (entry) return { name: tileName(entry), logos: tileLogos(entry) };
  const item = library?.find((candidate) => candidate.slug === slug);
  if (item) return { name: itemDisplayName(item), logos: itemLogos(item) };
  return { name: slug, logos: [] };
}

/** Raw tier values are internal dispatch keys; each maps to a human phase label. */
const TIER_LABELS: Record<string, string> = {
  llms_full: "Fetching documentation bundle",
  llms_txt: "Fetching page index",
  sitemap: "Crawling sitemap",
  docs: "Crawling documentation site",
  github: "Fetching GitHub repository",
  post: "Splitting & deduplicating pages",
  vault_backfill: "Building citation index",
  finalize: "Finalizing",
};

export function tierLabel(tier: string | null | undefined): string {
  if (tier && TIER_LABELS[tier]) return TIER_LABELS[tier];
  return tier && tier.length > 0 ? tier : "—";
}

/** Terminal states stop the poll loop. */
export function isRunTerminal(status: string | null | undefined): boolean {
  return status === "done" || status === "failed" || status === "cancelled";
}

/**
 * The run id a page opened without `?run=` should switch to, or null to leave it alone. This is the page-reload
 * recovery ("an ingestion of this framework is in flight, show it"), and it must never put back a run the page
 * has already watched finish: the list of active runs is cached for a few seconds, so right after a run ends it
 * can still name it, and adopting it again would make the address flip between with and without `?run=`.
 */
export function runToAdopt(args: {
  slug: string | null;
  urlRunId: string | null;
  active: { slug: string | null; run_id: string; progress?: { status?: string | null } | null } | undefined;
  finished: ReadonlySet<string>;
}): string | null {
  const { slug, urlRunId, active, finished } = args;
  if (urlRunId || !slug || !active || active.slug !== slug) return null;
  if (finished.has(active.run_id) || isRunTerminal(active.progress?.status)) return null;
  return active.run_id;
}

export type RunProgressView = {
  tier: string;
  status: string;
  lastUrl: string;
  percent: number | null;
  counter: string;
  postSummary: string;
};

function postSummary(p: RunStatus["post"]): string {
  if (!p) return "";
  const parts: string[] = [];
  const input = p.input_files ?? 0;
  const output = p.output_files ?? 0;
  if (p.was_split) parts.push(`split into ${output} page${output === 1 ? "" : "s"}`);
  else if (output !== input) parts.push(`${input} → ${output} pages`);
  if (p.duplicates_dropped) parts.push(`-${p.duplicates_dropped} dupes`);
  if (p.stubs_dropped) parts.push(`-${p.stubs_dropped} stubs`);
  return parts.length > 0 ? `(${parts.join(", ")})` : "";
}

/** Progress and the post summary render independently: the summary survives when progress is absent. */
export function runProgressView(p: RunStatus["progress"], post: RunStatus["post"]): RunProgressView {
  if (!p) {
    return {
      tier: "—",
      status: "—",
      lastUrl: "",
      percent: null,
      counter: "…",
      postSummary: postSummary(post),
    };
  }
  const total = p.total ?? 0;
  const current = p.current ?? 0;
  const base = {
    tier: tierLabel(p.tier),
    status: p.status || "—",
    lastUrl: p.last_url || "",
    postSummary: postSummary(post),
  };
  if (total > 0) {
    const percent = Math.min(100, Math.round((current / total) * 100));
    return { ...base, percent, counter: `${current} / ${total} (${percent}%)` };
  }
  return { ...base, percent: null, counter: `${current} so far…` };
}

// ============================================================
// Manifest view — summary line + explorer tree
// ============================================================

export type ManifestView = {
  framework: string;
  pageCount: number;
  totalBytes: number;
  tierKind: string;
  ingestedAt: number | null;
  entries: ManifestEntry[];
};

export function manifestView(m: Manifest | null): ManifestView | null {
  if (!m) return null;
  return {
    framework: m.framework_name || m.framework_slug || "",
    pageCount: m.entries.length,
    totalBytes: m.total_bytes ?? 0,
    tierKind: m.tier_kind || "",
    ingestedAt: m.ingested_at,
    entries: m.entries,
  };
}

export function ingressSummary(m: Manifest | null): string {
  const view = manifestView(m);
  if (!view) return "";
  const at = view.ingestedAt ? fmtAge(view.ingestedAt) : "";
  const kind = view.tierKind ? `${view.tierKind} · ` : "";
  return `${view.framework} · ${view.pageCount} pages · ${fmtBytes(view.totalBytes)} ${`${kind}${at}`.trim()}`;
}

/** The file name a chapter's citations use for this page: the last segment of its storage key, or else the
 * `<4-digit idx>-<slug>.md` the key would have had. */
export function entryBasename(entry: ManifestEntry): string {
  if (entry.key) return entry.key.replace(/\/+$/, "").split("/").pop() ?? "";
  return `${String(entry.idx).padStart(4, "0")}-${entry.slug || "page"}.md`;
}

/** Where in `entries` the page a citation names is (-1 if it is not there); a missing or extra `.md` is forgiven. */
export function findEntryByBasename(entries: readonly ManifestEntry[], basename: string): number {
  const wanted = basename.trim().toLowerCase();
  if (!wanted) return -1;
  const variants = new Set([wanted, wanted.endsWith(".md") ? wanted.slice(0, -3) : `${wanted}.md`]);
  return entries.findIndex((entry) => variants.has(entryBasename(entry).toLowerCase()));
}

/** Default group name for entries whose URL yields no useful path. */
export const ROOT_GROUP = "(root)";

function groupPrefix(entry: ManifestEntry): string {
  const section = entry.section?.trim().slice(0, 60);
  if (section) return section;
  try {
    const url = new URL(entry.source_path || entry.url || "");
    let rest = url.pathname.split("/").filter(Boolean);
    // Version pins (/v5.9.0/…) carry no topic signal.
    while (rest.length > 1 && /^v?\d+(\.\d+)*$/.test(rest[0] ?? "")) rest = rest.slice(1);
    // Scope roots (/oss/python/…) go too, but a genuine /python/<page> pair stays.
    while (rest.length > 2 && (rest[0] === "oss" || rest[0] === "python")) rest = rest.slice(1);
    const [first, second] = rest;
    if (!first || rest.length <= 1) return ROOT_GROUP;
    if (first === "docs") return second ?? ROOT_GROUP;
    if (first === "usage" && rest.length >= 3) return `${first}/${second}`;
    return first;
  } catch {
    return ROOT_GROUP;
  }
}

export type ExplorerGroup = { prefix: string; items: Array<{ entry: ManifestEntry; index: number }> };

/**
 * The pages under their section headings, **in the order of the original documentation**: the manifest lists pages
 * in source order, so a group appears where its first page does and its pages keep their relative order. Nothing is
 * sorted by name — that is what let the sidebar drift from the docs it mirrors.
 */
export function groupEntries(entries: ManifestEntry[]): ExplorerGroup[] {
  const groups = new Map<string, ExplorerGroup["items"]>();
  entries.forEach((entry, index) => {
    const key = groupPrefix(entry);
    const items = groups.get(key);
    if (items) items.push({ entry, index });
    else groups.set(key, [{ entry, index }]);
  });
  return [...groups].map(([prefix, items]) => ({ prefix, items }));
}

/** Indexes of the entries matching the tier filter and the search text. */
export function filterEntries(entries: ManifestEntry[], tiers: ReadonlySet<string>, q: string): number[] {
  const query = q.trim().toLowerCase();
  const visible: number[] = [];
  entries.forEach((entry, index) => {
    if (tiers.size > 0 && !tiers.has(entry.tier || "")) return;
    if (query) {
      const haystack =
        `${entry.title || ""} ${entry.slug || ""} ${entry.section || ""} ${entry.notes || ""} ${entry.source_path || entry.url || ""}`.toLowerCase();
      if (!haystack.includes(query)) return;
    }
    visible.push(index);
  });
  return visible;
}

export function explorerTierValues(entries: ManifestEntry[]): string[] {
  return [...new Set(entries.map((e) => e.tier || "").filter(Boolean))].sort();
}
