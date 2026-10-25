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
  catalogCategories,
  catalogTiers,
  DEFAULT_FILTERS,
  facetCounts,
  filterCatalog,
  tileName,
  tileStatus,
} from "../domain/catalog";
import { useActiveRun, useCatalog, useLibrary, useStartRun } from "../hooks/use-dd-data";
import { useDdOverlays } from "../hooks/use-overlays";
import { ingestionHref, stageHref } from "../params";
import { CatalogTile } from "./catalog-tile";

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
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <Input
          value={filters.query}
          onChange={(e) => setFilters((f) => ({ ...f, query: e.target.value }))}
          placeholder="Search the catalog…"
          aria-label="Search the catalog"
          className="h-9 w-64"
        />
        <div className="flex flex-wrap gap-1">
          {categories.map((category) => (
            <button
              key={category}
              type="button"
              aria-pressed={filters.category === category}
              onClick={() => setFilters((f) => ({ ...f, category }))}
              className={`min-h-9 rounded-sm border px-2 text-xs ${
                filters.category === category
                  ? "border-primary bg-primary/10 text-primary-text"
                  : "border-border text-muted-foreground hover:text-foreground"
              }`}
            >
              {category}
            </button>
          ))}
        </div>
      </div>
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <span className="text-xs text-muted-foreground">Tiers:</span>
        <div className="flex flex-wrap gap-1">
          {tierKeys.map((tier) => {
            const active = filters.tiers.has(tier);
            return (
              <button
                key={tier}
                type="button"
                aria-pressed={active}
                onClick={() =>
                  setFilters((f) => {
                    const tiers = new Set(f.tiers);
                    if (active) {
                      tiers.delete(tier);
                    } else {
                      tiers.add(tier);
                    }
                    return { ...f, tiers };
                  })
                }
                className={`min-h-9 rounded-sm border px-2 text-xs ${
                  active
                    ? "border-primary bg-primary/10 text-primary-text"
                    : "border-border text-muted-foreground hover:text-foreground"
                }`}
              >
                {tier} <span className="text-muted-foreground">{counts.tier[tier] ?? 0}</span>
              </button>
            );
          })}
        </div>
        <div className="flex flex-wrap gap-1">
          {(["Ingested", "Not ingested"] as const).map((status) => {
            const active = filters.statuses.has(status);
            return (
              <button
                key={status}
                type="button"
                aria-pressed={active}
                onClick={() =>
                  setFilters((f) => {
                    const statuses = new Set(f.statuses);
                    if (active) {
                      statuses.delete(status);
                    } else {
                      statuses.add(status);
                    }
                    return { ...f, statuses };
                  })
                }
                className={`min-h-9 rounded-sm border px-2 text-xs ${
                  active
                    ? "border-primary bg-primary/10 text-primary-text"
                    : "border-border text-muted-foreground hover:text-foreground"
                }`}
              >
                {status} <span className="text-muted-foreground">{counts.status[status] ?? 0}</span>
              </button>
            );
          })}
        </div>
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
      <p aria-live="polite" className="mt-3 text-xs text-muted-foreground">
        {visible.length} of {entries.length} frameworks shown
      </p>

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
