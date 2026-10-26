import { describe, expect, it } from "vitest";

import type { CatalogEntry } from "../entities";
import {
  catalogCategories,
  catalogTiers,
  categoryCounts,
  DEFAULT_FILTERS,
  facetCounts,
  facetSummary,
  filterCatalog,
  tileLogos,
  tileName,
} from "./catalog";

function tile(extra: Partial<CatalogEntry>): CatalogEntry {
  return { slug: "x", name: "X", category: null, tier: null, logo: null, logos: null, ...extra };
}

const ENTRIES: CatalogEntry[] = [
  tile({ slug: "a", name: "Alpha", category: "Dev", tier: 1 }),
  tile({ slug: "b", name: "Beta", category: "Data", tier: 2 }),
  tile({ slug: "c", name: "Absorb", category: "Dev", tier: null }),
];

describe("catalog filters", () => {
  const ingested = new Set(["b"]);

  it("filters by query and category", () => {
    const visible = filterCatalog(ENTRIES, ingested, { ...DEFAULT_FILTERS, query: "ab", category: "Dev" });
    expect(visible.map((e) => e.name)).toEqual(["Absorb"]);
  });

  it("counts each category over the whole catalog, putting the uncategorised under Other", () => {
    expect(categoryCounts([...ENTRIES, tile({ slug: "d", name: "D" })])).toEqual({
      Dev: 2,
      Data: 1,
      Other: 1,
    });
  });

  it("words what a multi-toggle filter has chosen", () => {
    expect(facetSummary(new Set())).toBe("All");
    expect(facetSummary(new Set(["2", "1"]))).toBe("1, 2");
    expect(facetSummary(new Set(["1", "2", "3"]))).toBe("3 selected");
  });

  it("filters by ingestion status", () => {
    const visible = filterCatalog(ENTRIES, ingested, { ...DEFAULT_FILTERS, statuses: new Set(["Ingested"]) });
    expect(visible.map((e) => e.name)).toEqual(["Beta"]);
  });

  it("counts facets against every other facet, so a selected option stays selectable", () => {
    const counts = facetCounts(ENTRIES, ingested, { ...DEFAULT_FILTERS, tiers: new Set(["1"]) });
    expect(counts.status).toEqual({ Ingested: 0, "Not ingested": 1 }); // only tier 1 counts toward status
    expect(counts.tier).toEqual({ "1": 1, "2": 1, "—": 1 }); // tier counts ignore the tier selection itself
  });

  it("lists the categories and tiers present, sorted, with a dash for an unknown tier", () => {
    expect(catalogCategories(ENTRIES)).toEqual(["Data", "Dev"]);
    expect(catalogTiers(ENTRIES)).toEqual(["1", "2", "—"]);
  });

  it("names a tile by its name, then its slug, and keeps the logo strip order", () => {
    expect(tileName(tile({ slug: "s", name: "" }))).toBe("s");
    expect(tileLogos(tile({ logos: ["a.png", "b.png"], logo: "c.png" }))).toEqual(["a.png", "b.png"]);
    expect(tileLogos(tile({ logo: "c.png" }))).toEqual(["c.png"]);
    expect(tileLogos(tile({}))).toEqual([]);
  });
});
