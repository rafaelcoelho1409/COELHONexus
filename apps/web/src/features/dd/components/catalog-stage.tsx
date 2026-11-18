"use client";

/** Catalog stage: browse and select source documentation, then explicitly enter Pages or start extraction. */

import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";

import { ContextBar } from "@/shared/layout/context-bar";
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
import { isRunTerminal } from "../domain/pages";
import { useActiveRun, useCatalog, useLibrary, useStartRun } from "../hooks/use-dd-data";
import { useDdOverlays } from "../hooks/use-overlays";
import { pagesHref, stageHref } from "../params";
import { CatalogTile } from "./catalog-tile";
import { FilterDropdown } from "./filter-dropdown";

const STATUSES: readonly CatalogStatus[] = ["Ingested", "Not ingested"];

export function CatalogStage() {
  const catalog = useCatalog();
  const libraryQuery = useLibrary();
  const { runs } = useActiveRun(true);
  const router = useRouter();
  const start = useStartRun();
  const { notice, toast } = useDdOverlays();

  const ingestedSlugs = useMemo(() => {
    const set = new Set<string>();
    for (const item of libraryQuery.data ?? []) {
      set.add(item.slug);
    }
    return set;
  }, [libraryQuery.data]);

  const [filters, setFilters] = useState<CatalogFilters>({ ...DEFAULT_FILTERS });
  const [selectedSlug, setSelectedSlug] = useState<string | null>(null);

  const entries = useMemo(() => catalog.data ?? [], [catalog.data]);
  const visible = useMemo(
    () => filterCatalog(entries, ingestedSlugs, filters),
    [entries, ingestedSlugs, filters],
  );
  const counts = useMemo(
    () => facetCounts(entries, ingestedSlugs, filters),
    [entries, ingestedSlugs, filters],
  );

  const selectedEntry = entries.find((entry) => entry.slug === selectedSlug) ?? null;
  const selectedIngested = selectedEntry ? ingestedSlugs.has(selectedEntry.slug) : false;
  const selectedRun = selectedSlug
    ? runs.find((run) => run.slug === selectedSlug && !isRunTerminal(run.progress?.status))
    : undefined;

  const continueToPages = () => {
    if (!selectedEntry) return;
    router.push(stageHref("pages", selectedEntry.slug));
  };

  const startIngestion = () => {
    if (!selectedEntry) return;
    const name = tileName(selectedEntry);
    start.mutate(
      { slug: selectedEntry.slug },
      {
        onSuccess: (outcome) => {
          if (outcome.kind === "queued") {
            notice(`Ingesting ${name}…`);
            router.push(pagesHref(selectedEntry.slug, outcome.runId ?? null));
          } else if (outcome.kind === "locked") {
            toast(outcome.message ?? "A run is already active for this framework.");
          } else {
            router.push(stageHref("pages", selectedEntry.slug));
          }
        },
        onError: (error) => toast(apiErrorMessage(error)),
      },
    );
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

  return (
    <div className={selectedEntry ? "pb-24" : undefined}>
      {/* The third navbar: search and filters; the match count sits beside Status. */}
      <ContextBar
        start={
          <>
            <Input
              value={filters.query}
              onChange={(e) => setFilters((f) => ({ ...f, query: e.target.value }))}
              placeholder={`Search ${entries.length} frameworks…`}
              aria-label="Search the catalog"
              className="h-9 w-full sm:w-64"
            />
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
            <div className="flex items-center gap-2">
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
              <span aria-live="polite" className="shrink-0 text-xs tabular-nums text-muted-foreground">
                <span aria-hidden="true">
                  {visible.length}/{entries.length}
                </span>
                <span className="sr-only">
                  {visible.length} of {entries.length} frameworks shown
                </span>
              </span>
            </div>
          </>
        }
      />

      <ul className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-4">
        {visible.map((entry) => (
          <li key={entry.slug} className="min-w-0">
            <CatalogTile
              entry={entry}
              ingested={tileStatus(entry.slug, ingestedSlugs) === "Ingested"}
              selected={selectedSlug === entry.slug}
              onPick={() => setSelectedSlug(entry.slug)}
            />
          </li>
        ))}
        {visible.length === 0 ? (
          <li className="col-span-full text-sm text-muted-foreground">Nothing matches those filters.</li>
        ) : null}
      </ul>
      {selectedEntry ? (
        <div className="fixed inset-x-0 bottom-0 z-40 border-t border-border bg-background">
          <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-4 py-3 sm:px-6">
            <div className="min-w-0">
              <p className="text-sm font-medium">{tileName(selectedEntry)}</p>
              <p className="text-xs text-muted-foreground">
                {selectedIngested ? "Pages are ready" : "Ready to fetch documentation pages"}
              </p>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <Button
                type="button"
                variant="outline"
                onClick={() => setSelectedSlug(null)}
                disabled={start.isPending}
              >
                Cancel
              </Button>
              {selectedRun ? (
                <Button
                  type="button"
                  onClick={() => router.push(pagesHref(selectedEntry.slug, selectedRun.run_id))}
                >
                  Open Pages
                </Button>
              ) : selectedIngested ? (
                <Button type="button" onClick={continueToPages}>
                  Open Pages
                </Button>
              ) : (
                <Button type="button" onClick={startIngestion} disabled={start.isPending}>
                  {start.isPending ? "Starting ingestion…" : "Start Ingestion"}
                </Button>
              )}
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
