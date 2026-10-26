"use client";

/** Catalog stage: resolver tiles + search + facets + sticky Start-Ingestion
 * bar. Tiles already ingested open the Pipeline stage; starting a run
 * follows the legacy POST /runs contract (cached / queued / locked). */

import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";

import { apiErrorMessage } from "@/shared/lib/api-error";
import { Button } from "@/shared/ui/button";
import { Input } from "@/shared/ui/input";

import {
  type CatalogFilters,
  type CatalogStatus,
  catalogCategories,
  catalogTiers,
  categoryCounts,
  DEFAULT_FILTERS,
  facetCounts,
  facetSummary,
  filterCatalog,
  tileName,
  tileStatus,
} from "../domain/catalog";
import { useActiveRun, useCatalog, useLibrary, useStartRun } from "../hooks/use-dd-data";
import { useDdOverlays } from "../hooks/use-overlays";
import { ingestionHref, stageHref } from "../params";
import { CatalogTile } from "./catalog-tile";
import { FilterDropdown } from "./filter-dropdown";

const STATUSES: readonly CatalogStatus[] = ["Ingested", "Not ingested"];

export function CatalogStage() {
  const catalog = useCatalog();
  const libraryQuery = useLibrary();
  const start = useStartRun();
  const { run: activeRun } = useActiveRun(true);
  const { notice, toast } = useDdOverlays();
  const router = useRouter();

  const ingestedSlugs = useMemo(() => {
    const set = new Set<string>();
    for (const item of libraryQuery.data ?? []) {
      set.add(item.slug);
    }
    return set;
  }, [libraryQuery.data]);

  const [filters, setFilters] = useState<CatalogFilters>({ ...DEFAULT_FILTERS });
  const [selected, setSelected] = useState<string | null>(null);

  const entries = useMemo(() => catalog.data ?? [], [catalog.data]);
  const visible = useMemo(
    () => filterCatalog(entries, ingestedSlugs, filters),
    [entries, ingestedSlugs, filters],
  );
  const counts = useMemo(
    () => facetCounts(entries, ingestedSlugs, filters),
    [entries, ingestedSlugs, filters],
  );

  const selectedEntry = selected ? (entries.find((entry) => entry.slug === selected) ?? null) : null;
  const selectedIngested = selected != null && ingestedSlugs.has(selected);

  const pick = (slug: string) => {
    if (ingestedSlugs.has(slug)) {
      router.push(stageHref("pipeline", slug));
      return;
    }
    setSelected((current) => (current === slug ? null : slug));
  };

  const startIngestion = () => {
    if (!selected) {
      return;
    }
    const body = selectedIngested ? { slug: selected, refresh: true } : { slug: selected };
    start.mutate(body, {
      onSuccess: (outcome) => {
        if (outcome.kind === "cached") {
          notice("Already up to date — opening the pipeline.");
          router.push(stageHref("pipeline", selected));
        } else if (outcome.kind === "queued") {
          notice(`Ingesting ${selectedEntry ? tileName(selectedEntry) : selected}…`);
          router.push(ingestionHref(selected, outcome.runId ?? null));
        } else {
          toast(outcome.message ?? "A run is already active for this framework.");
        }
      },
      onError: (error) => toast(apiErrorMessage(error)),
    });
  };

  const categories = useMemo(() => ["All", ...catalogCategories(entries)], [entries]);
  const categoryTotals = useMemo(() => categoryCounts(entries), [entries]);
  const tierKeys = useMemo(() => catalogTiers(entries), [entries]);

  if (catalog.isPending) {
    return <p className="text-sm text-muted-foreground">Loading the catalog…</p>;
  }
  if (catalog.isError) {
    return (
      <p role="alert" className="text-sm text-destructive-text">
        Catalog unavailable. Try reloading.
      </p>
    );
  }

  const activeFramework = activeRun?.slug
    ? (entries.find((entry) => entry.slug === activeRun.slug)?.name ?? activeRun.slug)
    : null;

  return (
    <div className="pb-28">
      {/* One row: search, how many match, then the three filters. `relative` anchors the lists on a phone. */}
      <div className="relative mb-4 flex flex-wrap items-center gap-x-3 gap-y-2">
        <Input
          value={filters.query}
          onChange={(e) => setFilters((f) => ({ ...f, query: e.target.value }))}
          placeholder={`Search ${entries.length} frameworks…`}
          aria-label="Search the catalog"
          className="h-9 w-full sm:w-64"
        />
        <p aria-live="polite" className="order-last text-xs text-muted-foreground sm:order-none">
          {visible.length} of {entries.length} frameworks shown
        </p>
        <FilterDropdown
          label="Category"
          ariaLabel="Filter by category"
          summary={filters.category}
          multiple={false}
          options={categories.map((category) => ({
            value: category,
            label: category,
            count: category === "All" ? entries.length : (categoryTotals[category] ?? 0),
            active: filters.category === category,
          }))}
          onToggle={(category) => setFilters((f) => ({ ...f, category }))}
          onReset={() => setFilters((f) => ({ ...f, category: "All" }))}
        />
        <FilterDropdown
          label="Tier"
          ariaLabel="Filter by tier"
          summary={facetSummary(filters.tiers)}
          multiple
          options={tierKeys.map((tier) => ({
            value: tier,
            label: tier,
            count: counts.tier[tier] ?? 0,
            active: filters.tiers.has(tier),
          }))}
          onToggle={(tier) =>
            setFilters((f) => {
              const tiers = new Set(f.tiers);
              if (!tiers.delete(tier)) tiers.add(tier);
              return { ...f, tiers };
            })
          }
          onReset={() => setFilters((f) => ({ ...f, tiers: new Set() }))}
        />
        <FilterDropdown
          label="Status"
          ariaLabel="Filter by status"
          summary={facetSummary(filters.statuses)}
          multiple
          options={STATUSES.map((status) => ({
            value: status,
            label: status,
            count: counts.status[status] ?? 0,
            active: filters.statuses.has(status),
          }))}
          onToggle={(value) => {
            const status = STATUSES.find((candidate) => candidate === value);
            if (!status) return;
            setFilters((f) => {
              const statuses = new Set(f.statuses);
              if (!statuses.delete(status)) statuses.add(status);
              return { ...f, statuses };
            });
          }}
          onReset={() => setFilters((f) => ({ ...f, statuses: new Set() }))}
        />
      </div>

      <ul className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-4">
        {visible.map((entry) => (
          <li key={entry.slug} className="min-w-0">
            <CatalogTile
              entry={entry}
              ingested={tileStatus(entry.slug, ingestedSlugs) === "Ingested"}
              selected={selected === entry.slug}
              onPick={() => pick(entry.slug)}
            />
          </li>
        ))}
        {visible.length === 0 ? (
          <li className="col-span-full text-sm text-muted-foreground">Nothing matches those filters.</li>
        ) : null}
      </ul>

      {selected && selectedEntry ? (
        <div className="fixed bottom-0 left-0 z-40 w-full border-t border-border bg-card p-3">
          <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-3">
            <span className="text-sm font-medium">{tileName(selectedEntry)}</span>
            <span className="text-xs text-muted-foreground">
              {selectedIngested ? "Already ingested — reingests with fresh crawls." : "Not ingested yet."}
              {activeFramework ? ` A run is active (${activeFramework}).` : ""}
            </span>
            <div className="ml-auto flex gap-2">
              <Button size="lg" variant="outline" onClick={() => setSelected(null)}>
                Cancel
              </Button>
              <Button
                size="lg"
                onClick={startIngestion}
                disabled={start.isPending || activeRun != null}
                aria-busy={start.isPending}
              >
                {selectedIngested ? "Reingest" : start.isPending ? "Starting…" : "Start Ingestion"}
              </Button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
