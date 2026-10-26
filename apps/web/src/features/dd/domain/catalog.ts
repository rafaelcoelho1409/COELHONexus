/** Docs Distiller catalog — entry status, filtering and disjunctive facet counts. */

import type { CatalogEntry } from "../entities";

export type CatalogStatus = "Ingested" | "Not ingested";

const UNKNOWN_TIER = "—";

export function tileStatus(slug: string, ingestedSlugs: ReadonlySet<string>): CatalogStatus {
  return ingestedSlugs.has(slug) ? "Ingested" : "Not ingested";
}

export function tileTier(entry: CatalogEntry): string {
  return entry.tier == null ? UNKNOWN_TIER : String(entry.tier);
}

export function tileName(entry: CatalogEntry): string {
  return entry.name || entry.slug;
}

/** Multi-logo entries keep the strip order; single-logo entries fall back to one image. */
export function tileLogos(entry: CatalogEntry): string[] {
  if (entry.logos && entry.logos.length > 0) return entry.logos;
  return entry.logo ? [entry.logo] : [];
}

export type CatalogFilters = Readonly<{
  query: string;
  category: string;
  tiers: ReadonlySet<string>;
  statuses: ReadonlySet<CatalogStatus>;
}>;

export const DEFAULT_FILTERS: CatalogFilters = {
  query: "",
  category: "All",
  tiers: new Set(),
  statuses: new Set(),
};

function tileMatches(entry: CatalogEntry, ingestedSlugs: ReadonlySet<string>, f: CatalogFilters): boolean {
  const q = f.query.trim().toLowerCase();
  const category = entry.category ?? "Other";
  if (q && !tileName(entry).toLowerCase().includes(q)) return false;
  if (f.category !== "All" && category !== f.category) return false;
  if (f.tiers.size > 0 && !f.tiers.has(tileTier(entry))) return false;
  if (f.statuses.size > 0 && !f.statuses.has(tileStatus(entry.slug, ingestedSlugs))) return false;
  return true;
}

export function catalogCategories(entries: CatalogEntry[]): string[] {
  return [...new Set(entries.map((e) => e.category ?? "Other"))].sort();
}

/** How many frameworks each category holds, over the whole catalog. */
export function categoryCounts(entries: CatalogEntry[]): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const entry of entries) {
    const category = entry.category ?? "Other";
    counts[category] = (counts[category] ?? 0) + 1;
  }
  return counts;
}

/** What a multi-toggle filter's button reads: "All" with nothing chosen, the values for one or two, else a count. */
export function facetSummary(selected: ReadonlySet<string>): string {
  if (selected.size === 0) return "All";
  const values = [...selected].sort();
  return values.length <= 2 ? values.join(", ") : `${values.length} selected`;
}

export function catalogTiers(entries: CatalogEntry[]): string[] {
  return [...new Set(entries.map(tileTier))].sort();
}

export function filterCatalog(
  entries: CatalogEntry[],
  ingestedSlugs: ReadonlySet<string>,
  f: CatalogFilters,
): CatalogEntry[] {
  return entries.filter((e) => tileMatches(e, ingestedSlugs, f));
}

/** Multi-toggle facets count matches computed against every OTHER facet, so options stay
 * selectable after selection (disjunctive counts). */
export function facetCounts(
  entries: CatalogEntry[],
  ingestedSlugs: ReadonlySet<string>,
  f: CatalogFilters,
): { tier: Record<string, number>; status: Record<string, number> } {
  const tier: Record<string, number> = {};
  const status: Record<string, number> = { Ingested: 0, "Not ingested": 0 };
  const q = f.query.trim().toLowerCase();
  for (const entry of entries) {
    const category = entry.category ?? "Other";
    const queryOk = !q || tileName(entry).toLowerCase().includes(q);
    const categoryOk = f.category === "All" || category === f.category;
    if (!queryOk || !categoryOk) continue;
    const entryTier = tileTier(entry);
    const entryStatus = tileStatus(entry.slug, ingestedSlugs);
    if (f.statuses.size === 0 || f.statuses.has(entryStatus)) tier[entryTier] = (tier[entryTier] ?? 0) + 1;
    if (f.tiers.size === 0 || f.tiers.has(entryTier)) status[entryStatus] = (status[entryStatus] ?? 0) + 1;
  }
  return { tier, status };
}
