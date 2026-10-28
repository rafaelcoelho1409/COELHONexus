"use client";

/** The Library: debounced search, three exclusive facet filters (Status / Channel / Language), the flat
 * row list of every ingested video, per-row delete and the bulk bar (Delete / Re-ingest). Port of
 * `static/js/ycs/ingest/library.js`. Refreshes on the pipeline panel's phase ticks (`refreshTick`),
 * not on a timer. */

import { useMutation, useQuery } from "@tanstack/react-query";
import { Trash2 } from "lucide-react";
import { useRouter } from "next/navigation";
import type { JSX } from "react";
import { useEffect, useState } from "react";

import { getYcsAdminVideosFacetsOptions, getYcsAdminVideosOptions } from "@/shared/api";
import { apiErrorMessage } from "@/shared/lib/api-error";

import { bulkDeleteLibraryVideos, deleteLibraryVideo, queueVideosPipeline } from "../api";
import {
  fmtCount,
  fmtInt,
  libraryMasterState,
  libraryMetaLine,
  libraryStatsLine,
  libraryStatusTone,
  libraryUrl,
} from "../domain";
import {
  type FacetEntry,
  type LibraryFacets,
  type LibraryItem,
  libraryFacetsSchema,
  libraryResponseSchema,
} from "../entities";
import { useYcsOverlays } from "../hooks/use-overlays";
import { ingestionHref, LIBRARY_PAGE_SIZE, LIBRARY_SEARCH_DEBOUNCE_MS } from "../params";

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

  function toggleRow(videoId: string, on: boolean): void {
    setSelected((current) => {
      const next = new Set(current);
      if (on) next.add(videoId);
      else next.delete(videoId);
      return next;
    });
  }

  function toggleAll(on: boolean): void {
    setSelected((current) => {
      const next = new Set(current);
      for (const row of rows) {
        if (on) next.add(row.video_id);
        else next.delete(row.video_id);
      }
      return next;
    });
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

  return (
    <section aria-label="Library" className="rounded-md border border-border bg-card p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-base font-medium">
          Library <span className="font-mono text-xs text-muted-foreground">· {fmtInt(total)} videos</span>
        </p>
        <button
          type="button"
          onClick={clearFilters}
          className="min-h-9 rounded-sm border border-border px-3 text-sm text-muted-foreground hover:text-foreground"
        >
          Clear filters
        </button>
      </div>

      <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-4">
        <div>
          <label htmlFor="ycs-lib-search" className="text-xs text-muted-foreground">
            Search
          </label>
          <input
            id="ycs-lib-search"
            type="search"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder="Title or channel"
            className="mt-1 w-full rounded-sm border border-border bg-background px-2 py-1.5 text-sm"
          />
        </div>
        {FACET_GROUPS.map((group) => (
          <div key={group.key}>
            <label htmlFor={`ycs-lib-facet-${group.key}`} className="text-xs text-muted-foreground">
              {group.label}
            </label>
            <select
              id={`ycs-lib-facet-${group.key}`}
              value={filters[group.key]}
              onChange={(event) => setFilter(group.key, event.target.value)}
              className="mt-1 w-full rounded-sm border border-border bg-background px-2 py-1.5 text-sm"
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
      </div>

      {selected.size > 0 ? (
        <div className="mt-3 flex flex-wrap items-center gap-2 rounded-sm border border-border px-2 py-1.5">
          <span className="text-sm">{selected.size} selected</span>
          <button
            type="button"
            onClick={() => void handleDeleteBulk()}
            disabled={deleteBulk.isPending}
            className="min-h-9 rounded-sm border border-border px-3 text-sm text-muted-foreground hover:text-foreground disabled:opacity-50"
          >
            {deleteBulk.isPending ? "Deleting…" : "Delete"}
          </button>
          <button
            type="button"
            onClick={handleReingest}
            disabled={reingest.isPending}
            className="min-h-9 rounded-sm border border-border px-3 text-sm text-muted-foreground hover:text-foreground disabled:opacity-50"
          >
            {reingest.isPending ? "Queuing…" : "Re-ingest"}
          </button>
          <button
            type="button"
            onClick={() => setSelected(new Set())}
            aria-label="Clear selection"
            className="min-h-9 rounded-sm px-2 text-sm text-muted-foreground hover:text-foreground"
          >
            ×
          </button>
        </div>
      ) : null}

      {deleteOne.isError ? (
        <p role="alert" className="mt-2 text-sm text-destructive-text">
          Delete failed — {apiErrorMessage(deleteOne.error)}
        </p>
      ) : null}
      {deleteBulk.isError ? (
        <p role="alert" className="mt-2 text-sm text-destructive-text">
          Bulk delete failed — {apiErrorMessage(deleteBulk.error)}
        </p>
      ) : null}
      {reingest.isError ? (
        <p role="alert" className="mt-2 text-sm text-destructive-text">
          Re-ingest failed — {apiErrorMessage(reingest.error)}
        </p>
      ) : null}

      <div className="mt-2 flex items-center gap-2 px-1">
        <input
          type="checkbox"
          checked={master === "all"}
          ref={(el) => {
            if (el) el.indeterminate = master === "some";
          }}
          onChange={(event) => toggleAll(event.target.checked)}
          aria-label="Select all videos on this page"
          className="h-4 w-4"
        />
        <span className="text-xs text-muted-foreground">Select all on this page</span>
      </div>

      {rowsQuery.isPending ? (
        <p className="py-6 text-center text-sm text-muted-foreground">Loading…</p>
      ) : rowsQuery.isError ? (
        <p role="alert" className="py-6 text-center text-sm text-destructive-text">
          Could not load the library — {apiErrorMessage(rowsQuery.error)}
        </p>
      ) : rows.length === 0 ? (
        <p className="py-6 text-center text-sm text-muted-foreground">No videos match the current filters.</p>
      ) : (
        <ul className="mt-1">
          {rows.map((row) => (
            <li key={row.video_id} className="flex items-start gap-2 border-t border-border py-2">
              <input
                type="checkbox"
                checked={selected.has(row.video_id)}
                onChange={(event) => toggleRow(row.video_id, event.target.checked)}
                aria-label={`Select ${row.title || row.video_id}`}
                className="mt-1 h-4 w-4"
              />
              {row.thumbnail ? (
                // biome-ignore lint/performance/noImgElement: YouTube thumbnails of arbitrary hosts — next/image would need every host allow-listed
                <img // eslint-disable-line @next/next/no-img-element
                  src={row.thumbnail}
                  alt=""
                  loading="lazy"
                  className="h-9 w-16 shrink-0 rounded-sm object-cover"
                />
              ) : (
                <div className="h-9 w-16 shrink-0 rounded-sm bg-muted" />
              )}
              <div className="min-w-0 flex-1">
                <div className="flex items-baseline gap-2">
                  <span className={`shrink-0 text-xs ${libraryStatusTone(row.status)}`}>{row.status}</span>
                  <a
                    href={libraryUrl(row)}
                    target="_blank"
                    rel="noopener"
                    className="min-w-0 truncate text-sm hover:underline"
                    title={row.title || row.video_id}
                  >
                    {row.title || "(no title)"}
                  </a>
                </div>
                <p className="truncate text-xs text-muted-foreground">{libraryMetaLine(row)}</p>
                {libraryStatsLine(row) ? (
                  <p className="truncate font-mono text-xs text-muted-foreground">{libraryStatsLine(row)}</p>
                ) : null}
              </div>
              <button
                type="button"
                onClick={() => void handleDeleteOne(row.video_id)}
                disabled={deleteOne.isPending}
                aria-label={`Delete ${row.title || row.video_id}`}
                className="min-h-9 min-w-9 rounded-sm px-2 text-muted-foreground hover:text-foreground disabled:opacity-50"
              >
                <Trash2 size={16} aria-hidden="true" />
              </button>
            </li>
          ))}
        </ul>
      )}

      {total > LIBRARY_PAGE_SIZE ? (
        <div className="mt-2 flex items-center justify-center gap-3 text-sm">
          <button
            type="button"
            onClick={() => setOffset(Math.max(0, offset - LIBRARY_PAGE_SIZE))}
            disabled={offset === 0}
            className="min-h-9 rounded-sm border border-border px-3 text-muted-foreground hover:text-foreground disabled:opacity-50"
          >
            Previous
          </button>
          <span className="font-mono text-xs text-muted-foreground">
            {rangeStart}–{rangeEnd} of {fmtInt(total)}
          </span>
          <button
            type="button"
            onClick={() => setOffset(offset + LIBRARY_PAGE_SIZE)}
            disabled={rangeEnd >= total}
            className="min-h-9 rounded-sm border border-border px-3 text-muted-foreground hover:text-foreground disabled:opacity-50"
          >
            Next
          </button>
        </div>
      ) : null}
    </section>
  );
}
