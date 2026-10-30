"use client";

/** The Library: every ingested video as one list. A filter row (debounced search and three exclusive facets — Status, Channel,
 * Language — with their counts), a list header that sticks under the app header with the master checkbox and where the page
 * sits, the rows, server-side paging, and a bar over the bottom of the screen while rows are selected (Re-ingest, Delete).
 * Port of `static/js/ycs/ingest/library.js`. Refreshes on the pipeline panel's phase ticks (`refreshTick`), not on a timer. */

import { useMutation, useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import type { JSX } from "react";
import { useEffect, useState } from "react";

import { getYcsAdminVideosFacetsOptions, getYcsAdminVideosOptions } from "@/shared/api";
import { apiErrorMessage } from "@/shared/lib/api-error";
import { Button } from "@/shared/ui/button";
import { Input } from "@/shared/ui/input";
import { Label } from "@/shared/ui/label";

import { bulkDeleteLibraryVideos, deleteLibraryVideo, queueVideosPipeline } from "../api";
import { fmtCount, fmtInt, libraryMasterState } from "../domain";
import {
  type FacetEntry,
  type LibraryFacets,
  type LibraryItem,
  libraryFacetsSchema,
  libraryResponseSchema,
} from "../entities";
import { useYcsOverlays } from "../hooks/use-overlays";
import { ingestionHref, LIBRARY_PAGE_SIZE, LIBRARY_SEARCH_DEBOUNCE_MS } from "../params";
import { LibraryBar } from "./library-bar";
import { LibraryRow } from "./library-row";
import { SELECT_CLASS } from "./search-filters";

/** The facets response keys are plural while the query params are singular — mapped explicitly so a
 * group can never silently empty again. */
const FACET_GROUPS = [
  { key: "status", label: "Status" },
  { key: "channel", label: "Channel" },
  { key: "lang", label: "Language" },
] as const;

type FacetGroupKey = (typeof FACET_GROUPS)[number]["key"];

function facetValue(entry: FacetEntry): string {
  return entry.key || entry.channel_id || "";
}

function facetLabel(entry: FacetEntry): string {
  return entry.label || entry.channel || facetValue(entry);
}

function facetCount(entry: FacetEntry): string {
  return fmtCount(entry.count ?? entry.video_count ?? 0);
}

function libraryGroupOf(facets: LibraryFacets, group: FacetGroupKey): FacetEntry[] {
  if (group === "status") return facets.statuses;
  if (group === "channel") return facets.channels;
  return facets.languages;
}

const EMPTY_FACETS: LibraryFacets = { channels: [], languages: [], statuses: [] };
const SKELETON_KEYS = ["sk-1", "sk-2", "sk-3", "sk-4"] as const;

export function LibraryPanel({ refreshTick }: { refreshTick: number }): JSX.Element {
  const router = useRouter();
  const { confirm, toast } = useYcsOverlays();

  const [draft, setDraft] = useState("");
  const [q, setQ] = useState("");
  const [filters, setFilters] = useState<Record<FacetGroupKey, string>>({
    status: "",
    channel: "",
    lang: "",
  });
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());
  const [lastToggled, setLastToggled] = useState<string | null>(null);

  useEffect(() => {
    const timer = setTimeout(() => {
      setQ(draft.trim());
      setOffset(0);
    }, LIBRARY_SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [draft]);

  const rowsQuery = useQuery({
    ...getYcsAdminVideosOptions({
      query: {
        q: q || undefined,
        channel: filters.channel || undefined,
        status: filters.status || undefined,
        lang: filters.lang || undefined,
        limit: LIBRARY_PAGE_SIZE,
        offset,
      },
    }),
    retry: 0,
    select: libraryResponseSchema.parse,
  });
  const facetsQuery = useQuery({
    ...getYcsAdminVideosFacetsOptions(),
    retry: 0,
    select: libraryFacetsSchema.parse,
  });

  // Pipeline phase ticks (and wipes) arrive as a refreshTick bump — one refresh per tick.
  const refetchRows = rowsQuery.refetch;
  const refetchFacets = facetsQuery.refetch;
  useEffect(() => {
    if (refreshTick > 0) {
      void refetchRows();
      void refetchFacets();
    }
  }, [refreshTick, refetchRows, refetchFacets]);

  const rows: LibraryItem[] = rowsQuery.data?.items ?? [];
  const total = rowsQuery.data?.total ?? 0;
  const returned = rowsQuery.data?.returned ?? 0;

  const deleteOne = useMutation({
    mutationFn: (videoId: string) => deleteLibraryVideo(videoId),
    onSuccess: (_data, videoId) => {
      setSelected((current) => {
        const next = new Set(current);
        next.delete(videoId);
        return next;
      });
      void refetchRows();
      void refetchFacets();
    },
  });
  const deleteBulk = useMutation({
    mutationFn: (ids: readonly string[]) => bulkDeleteLibraryVideos(ids),
    onSuccess: () => {
      setSelected(new Set());
      void refetchRows();
      void refetchFacets();
    },
  });
  const reingest = useMutation({
    mutationFn: async (ids: readonly string[]) =>
      queueVideosPipeline({
        video_ids: [...ids],
        include_transcription: true,
        transcription_languages: null,
      }),
    onSuccess: (queued) => {
      setSelected(new Set());
      if (queued) router.push(ingestionHref(queued.phases));
      else toast("Re-ingest returned no phase ids.");
    },
  });

  const filtering = q !== "" || FACET_GROUPS.some((group) => filters[group.key] !== "");

  function setFilter(group: FacetGroupKey, value: string): void {
    setFilters((current) => ({ ...current, [group]: value }));
    setOffset(0);
  }

  function clearFilters(): void {
    setDraft("");
    setQ("");
    setFilters({ status: "", channel: "", lang: "" });
    setOffset(0);
  }

  function setMany(ids: ReadonlyArray<string>, on: boolean): void {
    setSelected((current) => {
      const next = new Set(current);
      for (const id of ids) {
        if (on) next.add(id);
        else next.delete(id);
      }
      return next;
    });
  }

  /** Tick or untick a row; with Shift, every row between it and the last one touched, in the order shown. */
  function toggleRow(videoId: string, on: boolean, shift: boolean): void {
    if (shift && lastToggled) {
      const from = rows.findIndex((row) => row.video_id === lastToggled);
      const to = rows.findIndex((row) => row.video_id === videoId);
      if (from !== -1 && to !== -1) {
        const [lo, hi] = from < to ? [from, to] : [to, from];
        setMany(
          rows.slice(lo, hi + 1).map((row) => row.video_id),
          on,
        );
        setLastToggled(videoId);
        return;
      }
    }
    setMany([videoId], on);
    setLastToggled(videoId);
  }

  function clearSelection(): void {
    setSelected(new Set());
    setLastToggled(null);
  }

  async function handleDeleteOne(videoId: string): Promise<void> {
    const ok = await confirm({
      title: "Delete this video?",
      message:
        "Wipes ES metadata + transcripts, Qdrant points, and Neo4j Document + Video nodes for this video. Entity nodes are left intact (may be referenced by other videos). This cannot be undone.",
      confirmLabel: "Delete",
    });
    if (!ok) return;
    deleteOne.mutate(videoId);
  }

  async function handleDeleteBulk(): Promise<void> {
    const n = selected.size;
    if (!n) return;
    const ok = await confirm({
      title: `Delete ${n} video${n === 1 ? "" : "s"}?`,
      message:
        "Wipes ES + Qdrant + Neo4j for the selected videos. Entity nodes are left intact. This cannot be undone.",
      confirmLabel: "Delete",
    });
    if (!ok) return;
    deleteBulk.mutate([...selected]);
  }

  function handleReingest(): void {
    const ids = [...selected];
    if (ids.length) reingest.mutate(ids);
  }

  const master = libraryMasterState(rows, selected);
  const rangeStart = total === 0 ? 0 : offset + 1;
  const rangeEnd = offset + returned;
  const status =
    rowsQuery.isFetching && rows.length > 0
      ? "Updating…"
      : total === 0
        ? ""
        : `${rangeStart}–${rangeEnd} of ${fmtInt(total)}`;

  return (
    <section aria-label="Library" className="flex flex-col gap-4">
      <h2 className="text-base font-medium">
        Library{" "}
        <span className="font-mono text-xs font-normal text-muted-foreground">· {fmtInt(total)} videos</span>
      </h2>

      <div className="flex flex-wrap items-end gap-x-3 gap-y-3">
        <div className="min-w-0 flex-[1_1_14rem]">
          <Label htmlFor="ycs-lib-search" className="mb-1.5 block text-xs font-medium text-muted-foreground">
            Search
          </Label>
          <Input
            id="ycs-lib-search"
            type="search"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder="Title or channel"
            autoComplete="off"
            className="h-9 text-sm"
          />
        </div>
        {FACET_GROUPS.map((group) => (
          <div key={group.key} className="min-w-0 flex-[1_1_10rem]">
            <Label
              htmlFor={`ycs-lib-facet-${group.key}`}
              className="mb-1.5 block text-xs font-medium text-muted-foreground"
            >
              {group.label}
            </Label>
            <select
              id={`ycs-lib-facet-${group.key}`}
              value={filters[group.key]}
              onChange={(event) => setFilter(group.key, event.target.value)}
              className={`${SELECT_CLASS} w-full`}
            >
              <option value="">All</option>
              {libraryGroupOf(facetsQuery.data ?? EMPTY_FACETS, group.key).map((entry) => {
                const value = facetValue(entry);
                if (!value) return null;
                return (
                  <option key={value} value={value}>
                    {facetLabel(entry)} ({facetCount(entry)})
                  </option>
                );
              })}
            </select>
          </div>
        ))}
        {filtering ? (
          <Button type="button" variant="ghost" size="lg" onClick={clearFilters}>
            Clear filters
          </Button>
        ) : null}
      </div>

      {deleteOne.isError ? (
        <p role="alert" className="text-sm text-destructive-text">
          Delete failed — {apiErrorMessage(deleteOne.error)}
        </p>
      ) : null}
      {deleteBulk.isError ? (
        <p role="alert" className="text-sm text-destructive-text">
          Bulk delete failed — {apiErrorMessage(deleteBulk.error)}
        </p>
      ) : null}
      {reingest.isError ? (
        <p role="alert" className="text-sm text-destructive-text">
          Re-ingest failed — {apiErrorMessage(reingest.error)}
        </p>
      ) : null}

      <div className="rounded-md border border-border bg-card">
        <div className="z-10 flex flex-wrap items-center gap-x-4 gap-y-1 rounded-t-md border-b border-border bg-card px-3 py-1 sm:sticky sm:top-14">
          <label className="flex min-h-9 cursor-pointer items-center gap-3 text-sm">
            <span className="flex size-9 items-center justify-center">
              <input
                type="checkbox"
                checked={master === "all"}
                ref={(el) => {
                  if (el) el.indeterminate = master === "some";
                }}
                disabled={rows.length === 0}
                onChange={(event) =>
                  setMany(
                    rows.map((row) => row.video_id),
                    event.target.checked,
                  )
                }
                aria-label="Select all videos on this page"
                className="size-4 cursor-pointer accent-primary"
              />
            </span>
            Select all on this page
          </label>
          <p role="status" className="tabular text-xs text-muted-foreground">
            {status}
          </p>
        </div>

        {rowsQuery.isPending ? (
          <ul aria-label="Loading the library" aria-busy>
            {SKELETON_KEYS.map((key) => (
              <li
                key={key}
                aria-hidden
                className="grid animate-status grid-cols-[auto_auto_minmax(0,1fr)] gap-x-3 border-b border-border px-3 py-2"
              >
                <span className="size-9" />
                <span className="aspect-video w-24 rounded-sm bg-muted" />
                <span className="flex flex-col gap-2 pt-1.5">
                  <span className="h-4 w-3/5 rounded-sm bg-muted" />
                  <span className="h-3 w-2/5 rounded-sm bg-muted" />
                </span>
              </li>
            ))}
          </ul>
        ) : rowsQuery.isError ? (
          <p role="alert" className="px-3 py-8 text-center text-sm text-destructive-text">
            Could not load the library — {apiErrorMessage(rowsQuery.error)}
          </p>
        ) : rows.length === 0 ? (
          <p className="px-3 py-8 text-center text-sm text-muted-foreground">
            No videos match the current filters.
          </p>
        ) : (
          <ul aria-label="Ingested videos" aria-busy={rowsQuery.isFetching}>
            {rows.map((row) => (
              <LibraryRow
                key={row.video_id}
                row={row}
                checked={selected.has(row.video_id)}
                deleting={deleteOne.isPending}
                onToggle={(on, shift) => toggleRow(row.video_id, on, shift)}
                onDelete={() => void handleDeleteOne(row.video_id)}
              />
            ))}
          </ul>
        )}

        {total > LIBRARY_PAGE_SIZE ? (
          <div className="flex items-center justify-between gap-3 rounded-b-md border-t border-border px-3 py-2">
            <Button
              type="button"
              variant="outline"
              size="lg"
              onClick={() => setOffset(Math.max(0, offset - LIBRARY_PAGE_SIZE))}
              disabled={offset === 0}
            >
              Previous
            </Button>
            <span className="tabular font-mono text-xs text-muted-foreground">
              {rangeStart}–{rangeEnd} of {fmtInt(total)}
            </span>
            <Button
              type="button"
              variant="outline"
              size="lg"
              onClick={() => setOffset(offset + LIBRARY_PAGE_SIZE)}
              disabled={rangeEnd >= total}
            >
              Next
            </Button>
          </div>
        ) : null}
      </div>

      {selected.size > 0 ? (
        <LibraryBar
          count={selected.size}
          reingesting={reingest.isPending}
          deleting={deleteBulk.isPending}
          onReingest={handleReingest}
          onDelete={() => void handleDeleteBulk()}
          onClear={clearSelection}
        />
      ) : null}
    </section>
  );
}
