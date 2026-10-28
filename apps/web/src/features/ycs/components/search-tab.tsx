"use client";

/** Source · Search — full-text yt-dlp search with optional sort/filter fields, a growing-window page
 * cache (the backend fetches `needed = target × size` results and the client slices), Gmail-style
 * Shift-range selection, per-kind routing into the other Source tabs, active filter chips,
 * loading skeletons, and per-browser density and page-size preferences. */

import { useMutation } from "@tanstack/react-query";
import {
  AlertCircle,
  ArrowRight,
  ChevronDown,
  ChevronUp,
  Filter,
  LayoutList,
  List,
  ListVideo,
  Loader2,
  RotateCcw,
  Search,
  SlidersHorizontal,
  Sparkles,
  User,
  Video,
  X,
} from "lucide-react";
import type { FormEvent, JSX, ReactNode } from "react";
import { useRef, useState } from "react";

import { apiErrorMessage } from "@/shared/lib/api-error";
import { Button } from "@/shared/ui/button";
import { Input } from "@/shared/ui/input";
import { Label } from "@/shared/ui/label";

import { searchVideos } from "../api";
import {
  dropRouted,
  masterState,
  readSearchRequest,
  routeItems,
  selectRange,
  selectSnippet,
} from "../domain";
import type { SearchRequestPayload, VideoSnippet } from "../entities";
import { useBrowserPref } from "../hooks/use-browser-pref";
import {
  DENSITY_DEFAULT,
  PAGE_SIZE_DEFAULT,
  PAGE_SIZE_STORAGE_KEY,
  PAGE_SIZES,
  RESULTS_DENSITY_STORAGE_KEY,
  type SourceMode,
} from "../params";
import { type Density, kindCountLabel, MasterRow, ResultRow, ResultSkeleton } from "./result-list";

const selectCls =
  "h-9 w-full rounded-md border border-border bg-background px-2.5 text-sm transition-colors focus:border-primary focus:ring-1 focus:ring-primary/30";
const outlineBtnCls =
  "inline-flex h-9 items-center justify-center gap-1.5 rounded-md border border-border bg-card px-3 text-sm font-medium transition-colors hover:bg-muted hover:text-foreground disabled:opacity-50 cursor-pointer select-none";

const SKELETON_KEYS = ["sk-1", "sk-2", "sk-3", "sk-4", "sk-5"] as const;

type RouteMode = Exclude<SourceMode, "search">;
const ROUTE_LABELS: Record<RouteMode, string> = {
  videos: "Videos",
  channel: "Channel",
  playlist: "Playlist",
};

export type Routed = { mode: RouteMode; items: string[] };

export function SearchTab({ onRoute }: { onRoute: (routed: Routed) => void }): JSX.Element {
  const formRef = useRef<HTMLFormElement>(null);
  const [cache, setCache] = useState<VideoSnippet[]>([]);
  const [page, setPage] = useState(1);
  const [exhausted, setExhausted] = useState(false);
  const [baseReq, setBaseReq] = useState<SearchRequestPayload | null>(null);
  const [selected, setSelected] = useState<Map<string, VideoSnippet>>(new Map());
  const [lastCheckedId, setLastCheckedId] = useState<string | null>(null);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [statusMsg, setStatusMsg] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [densityPref, setDensityPref] = useBrowserPref(RESULTS_DENSITY_STORAGE_KEY, DENSITY_DEFAULT);
  const [sizePref, setSizePref] = useBrowserPref(PAGE_SIZE_STORAGE_KEY, String(PAGE_SIZE_DEFAULT));

  const size = PAGE_SIZES.includes(Number.parseInt(sizePref, 10))
    ? Number.parseInt(sizePref, 10)
    : PAGE_SIZE_DEFAULT;
  const density: Density = densityPref === "comfortable" ? "comfortable" : "compact";

  const searchMutation = useMutation({
    mutationFn: ({ req, needed }: { req: SearchRequestPayload; needed: number }) =>
      searchVideos({ ...req, max_results: needed }),
  });
  const pending = searchMutation.isPending;

  const runSearch = async (req: SearchRequestPayload, needed: number): Promise<boolean> => {
    try {
      const res = await searchMutation.mutateAsync({ req, needed });
      setCache(res.results);
      setExhausted(res.results.length < needed);
      return true;
    } catch (e) {
      setStatusMsg(apiErrorMessage(e));
      return false;
    }
  };

  const executeSearch = (req: SearchRequestPayload) => {
    setBaseReq(req);
    setPage(1);
    setExhausted(false);
    setSelected(new Map());
    setLastCheckedId(null);
    setStatusMsg(null);
    setFiltersOpen(false);
    void runSearch(req, size);
  };

  const onSubmit = (ev: FormEvent<HTMLFormElement>) => {
    ev.preventDefault();
    let req: SearchRequestPayload;
    try {
      req = readSearchRequest(new FormData(ev.currentTarget), size);
    } catch {
      setStatusMsg("Enter a search query.");
      return;
    }
    executeSearch(req);
  };

  const total = cache.length;
  const from = total === 0 ? 0 : (page - 1) * size + 1;
  const to = Math.min(page * size, total);
  const slice = cache.slice((page - 1) * size, page * size);

  const goPrev = () => {
    if (!baseReq || pending || page <= 1) return;
    setPage(page - 1);
  };

  const goNext = () => {
    if (!baseReq || pending) return;
    const target = page + 1;
    const needed = target * size;
    if (exhausted || cache.length >= needed) {
      setPage(target);
      return;
    }
    void runSearch(baseReq, needed).then((ok) => {
      if (ok) setPage(target);
    });
  };

  const changeSize = (v: string) => {
    setSizePref(v);
    const n = PAGE_SIZES.includes(Number.parseInt(v, 10)) ? Number.parseInt(v, 10) : PAGE_SIZE_DEFAULT;
    if (!baseReq || pending || n === size) return;
    setCache([]);
    setPage(1);
    setExhausted(false);
    void runSearch(baseReq, n);
  };

  const onCheck = (snippet: VideoSnippet, on: boolean, shift: boolean) => {
    if (shift && lastCheckedId) {
      const start = slice.findIndex((s) => s.id === lastCheckedId);
      const end = slice.findIndex((s) => s.id === snippet.id);
      if (start !== -1 && end !== -1) {
        const [lo, hi] = start < end ? [start, end] : [end, start];
        setSelected((prev) => selectRange(prev, slice.slice(lo, hi + 1), on));
        setLastCheckedId(snippet.id);
        return;
      }
    }
    setSelected((prev) => selectSnippet(prev, snippet, on));
    setLastCheckedId(snippet.id);
  };

  const route = (mode: RouteMode) => {
    const items = routeItems(mode, selected);
    if (!items.length) return;
    onRoute({ mode, items });
    setSelected((prev) => dropRouted(prev, mode, items));
  };

  const copyId = (id: string) => {
    const clip = navigator.clipboard;
    if (clip) void clip.writeText(id).catch(() => undefined);
  };

  // Active filters detection from current baseReq
  const activeFilters = baseReq
    ? Object.entries(baseReq).filter(([key, val]) => {
        if (key === "query" || key === "max_results") return false;
        return val != null && val !== "" && val !== false;
      })
    : [];

  const removeFilter = (filterKey: string) => {
    if (!baseReq || !formRef.current) return;
    const form = formRef.current;
    const input = form.elements.namedItem(filterKey) as HTMLInputElement | HTMLSelectElement | null;
    if (input) {
      if (input.type === "checkbox") {
        (input as HTMLInputElement).checked = false;
      } else {
        input.value = "";
      }
    }
    try {
      const nextReq = readSearchRequest(new FormData(form), size);
      executeSearch(nextReq);
    } catch {
      // Keep query if available
    }
  };

  const resetAllFilters = () => {
    if (!formRef.current) return;
    const form = formRef.current;
    const elements = Array.from(form.elements) as (HTMLInputElement | HTMLSelectElement)[];
    for (const el of elements) {
      if (el.name && el.name !== "query") {
        if (el.type === "checkbox") {
          (el as HTMLInputElement).checked = false;
        } else {
          el.value = "";
        }
      }
    }
    if (baseReq) {
      const nextReq: SearchRequestPayload = { query: baseReq.query, max_results: size };
      executeSearch(nextReq);
    }
  };

  const master = masterState(slice, selected);
  const prevOff = !baseReq || pending || page <= 1;
  const nextOff = !baseReq || pending || (exhausted && to >= total);
  const rangeText = pending
    ? "Searching…"
    : total === 0
      ? baseReq
        ? "No results."
        : "Run a search to see videos."
      : `${from}–${to} of ${total}${exhausted ? "" : "+"}`;

  return (
    <div className="space-y-4">
      <form ref={formRef} onSubmit={onSubmit} className="space-y-3">
        <div className="flex flex-wrap items-end gap-x-3 gap-y-2">
          {/* Enhanced Search Input */}
          <div className="min-w-0 flex-1">
            <Label
              htmlFor="ycs-search-query"
              className="mb-1.5 flex items-center gap-1.5 text-xs font-medium text-muted-foreground"
            >
              <Search className="size-3.5" />
              <span>Search</span>
            </Label>
            <div className="relative flex items-center">
              <Input
                id="ycs-search-query"
                name="query"
                required
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Search YouTube videos, channels, or playlists…"
                autoComplete="off"
                className="h-10 pr-9 text-sm shadow-xs"
              />
              {searchQuery ? (
                <button
                  type="button"
                  onClick={() => setSearchQuery("")}
                  className="absolute right-2.5 text-muted-foreground hover:text-foreground p-0.5 rounded cursor-pointer"
                  title="Clear input"
                >
                  <X className="size-4" />
                </button>
              ) : null}
            </div>
          </div>

          {/* Search Button with Loader */}
          <Button
            type="submit"
            disabled={pending}
            className="h-10 px-5 font-medium shadow-xs inline-flex items-center gap-2 cursor-pointer"
          >
            {pending ? (
              <>
                <Loader2 className="size-4 animate-spin" />
                <span>Searching…</span>
              </>
            ) : (
              <>
                <Search className="size-4" />
                <span>Search</span>
              </>
            )}
          </Button>

          {/* Filters Toggle Button */}
          <button
            type="button"
            aria-expanded={filtersOpen}
            onClick={() => setFiltersOpen(!filtersOpen)}
            className={`${outlineBtnCls} h-10 px-3.5`}
          >
            <SlidersHorizontal className="size-4 text-muted-foreground" />
            <span>Filters</span>
            {activeFilters.length > 0 ? (
              <span className="flex size-5 items-center justify-center rounded-full bg-primary text-[10px] font-bold text-primary-foreground leading-none">
                {activeFilters.length}
              </span>
            ) : null}
            {filtersOpen ? <ChevronUp className="size-4" /> : <ChevronDown className="size-4" />}
          </button>
        </div>

        {/* Collapsible Filter Panel */}
        <details
          open={filtersOpen}
          onToggle={(e) => setFiltersOpen(e.currentTarget.open)}
          className="rounded-lg border border-border bg-card shadow-xs overflow-hidden transition-all"
        >
          <summary className="flex items-center justify-between cursor-pointer px-4 py-2.5 text-sm font-medium hover:bg-muted/40 transition-colors">
            <span className="flex items-center gap-2">
              <Filter className="size-4 text-muted-foreground" />
              <span>Search Filters & Sort Options</span>
            </span>
            <span className="text-xs text-muted-foreground">Click to toggle</span>
          </summary>
          <div className="border-t border-border bg-background/50 p-4">
            <FilterGrid onReset={resetAllFilters} />
          </div>
        </details>

        {/* Active Filter Chips Bar */}
        {activeFilters.length > 0 ? (
          <div className="flex flex-wrap items-center gap-2 pt-1 text-xs">
            <span className="font-medium text-muted-foreground flex items-center gap-1">
              <Filter className="size-3" /> Active filters:
            </span>
            {activeFilters.map(([key, val]) => (
              <span
                key={key}
                className="inline-flex items-center gap-1.5 rounded-full border border-border bg-muted/60 px-2.5 py-1 font-mono text-xs text-foreground shadow-2xs"
              >
                <span>
                  {key === "sort_by_date"
                    ? "Newest first"
                    : key === "exclude_shorts"
                      ? "No shorts"
                      : `${key.replace(/_/g, " ")}: ${String(val)}`}
                </span>
                <button
                  type="button"
                  onClick={() => removeFilter(key)}
                  className="rounded-full hover:bg-muted p-0.5 text-muted-foreground hover:text-foreground"
                  title={`Remove ${key}`}
                >
                  <X className="size-3" />
                </button>
              </span>
            ))}
            <button
              type="button"
              onClick={resetAllFilters}
              className="text-xs text-muted-foreground hover:text-foreground underline underline-offset-2 ml-1 cursor-pointer"
            >
              Clear all
            </button>
          </div>
        ) : null}
      </form>

      {/* Main Results Card */}
      <div className="rounded-lg border border-border bg-card shadow-xs overflow-hidden">
        {/* Results Header Toolbar */}
        <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b border-border bg-card px-4 py-3">
          <div className="flex items-center gap-2">
            {pending ? <Loader2 className="size-4 animate-spin text-primary" /> : null}
            {statusMsg ? <AlertCircle className="size-4 text-destructive-text" /> : null}
            <p
              aria-live="polite"
              className={
                statusMsg
                  ? "text-sm font-medium text-destructive-text"
                  : "text-sm font-medium text-muted-foreground"
              }
            >
              {statusMsg ?? rangeText}
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
            {/* Quick mini-pagination in header for power users */}
            {total > 0 ? (
              <div className="hidden sm:flex items-center gap-1 mr-2">
                <button
                  type="button"
                  onClick={goPrev}
                  disabled={prevOff}
                  aria-label="Previous page"
                  className="size-8 rounded-md border border-border bg-muted/30 flex items-center justify-center text-xs hover:bg-muted disabled:opacity-40 cursor-pointer"
                >
                  ←
                </button>
                <span className="text-xs font-mono text-muted-foreground px-1">p.{page}</span>
                <button
                  type="button"
                  onClick={goNext}
                  disabled={nextOff}
                  aria-label="Next page"
                  className="size-8 rounded-md border border-border bg-muted/30 flex items-center justify-center text-xs hover:bg-muted disabled:opacity-40 cursor-pointer"
                >
                  →
                </button>
              </div>
            ) : null}

            {/* Page Size Dropdown */}
            <select
              id="ycs-page-size"
              aria-label="Results per page"
              value={String(size)}
              onChange={(e) => changeSize(e.currentTarget.value)}
              className={`${selectCls} w-auto text-xs font-medium cursor-pointer`}
            >
              {PAGE_SIZES.map((n) => (
                <option key={n} value={String(n)}>
                  {n} per page
                </option>
              ))}
            </select>

            {/* Density Segmented Control */}
            <fieldset
              aria-label="Result density"
              className="flex items-center rounded-lg border border-border bg-muted/50 p-0.5 m-0"
            >
              {(["compact", "comfortable"] as const).map((d) => (
                <button
                  key={d}
                  type="button"
                  aria-pressed={density === d}
                  onClick={() => setDensityPref(d)}
                  className={`inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs font-medium transition-all cursor-pointer ${
                    density === d
                      ? "bg-background text-foreground shadow-2xs font-semibold"
                      : "text-muted-foreground hover:text-foreground"
                  }`}
                >
                  {d === "compact" ? (
                    <>
                      <List className="size-3" />
                      <span>Compact</span>
                    </>
                  ) : (
                    <>
                      <LayoutList className="size-3" />
                      <span>Comfortable</span>
                    </>
                  )}
                </button>
              ))}
            </fieldset>
          </div>
        </div>

        {/* Results List / Skeletons / Empty State */}
        {pending && slice.length === 0 ? (
          <ul aria-label="Loading search results" className="divide-y divide-border/60">
            {SKELETON_KEYS.map((k) => (
              <ResultSkeleton key={k} density={density} />
            ))}
          </ul>
        ) : slice.length > 0 ? (
          <ul aria-label="Search results" className="divide-y divide-border/60">
            <MasterRow
              state={master}
              totalVisible={slice.length}
              selectedCount={selected.size}
              onToggle={() => setSelected(selectRange(selected, slice, master !== "all"))}
            />
            {slice.map((snippet) => (
              <ResultRow
                key={`${snippet.kind || "video"}-${snippet.id}`}
                snippet={snippet}
                checked={selected.has(snippet.id)}
                density={density}
                onCheck={(on, shift) => onCheck(snippet, on, shift)}
                onCopyId={() => copyId(snippet.id)}
              />
            ))}
          </ul>
        ) : (
          <div className="flex flex-col items-center justify-center px-4 py-16 text-center">
            {baseReq ? (
              <div className="space-y-2">
                <Search className="mx-auto size-10 text-muted-foreground/60" />
                <h3 className="text-base font-semibold text-foreground">No matching results</h3>
                <p className="max-w-md text-sm text-muted-foreground">
                  No videos or channels matched "{baseReq.query}". Try adjusting or clearing your filters.
                </p>
                {activeFilters.length > 0 ? (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={resetAllFilters}
                    className="mt-2 inline-flex items-center gap-1.5"
                  >
                    <RotateCcw className="size-3.5" />
                    Reset all filters
                  </Button>
                ) : null}
              </div>
            ) : (
              <div className="space-y-2">
                <div className="mx-auto flex size-12 items-center justify-center rounded-full bg-primary/10 text-primary-text">
                  <Sparkles className="size-6" />
                </div>
                <h3 className="text-base font-semibold text-foreground">Search YouTube Catalog</h3>
                <p className="max-w-md text-sm text-muted-foreground">
                  Search videos, channels, or playlists to inspect details and route into the Ingestion
                  pipeline.
                </p>
              </div>
            )}
          </div>
        )}

        {/* Bottom Pagination Bar */}
        <div className="flex items-center justify-between border-t border-border bg-card/60 px-4 py-3">
          <button
            type="button"
            onClick={goPrev}
            disabled={prevOff}
            className={outlineBtnCls}
            aria-label="Previous page"
          >
            ← Previous
          </button>
          <span className="text-xs font-mono text-muted-foreground">
            {total > 0 ? `Page ${page} · ${from}–${to} of ${total}` : ""}
          </span>
          <button
            type="button"
            onClick={goNext}
            disabled={nextOff}
            className={outlineBtnCls}
            aria-label="Next page"
          >
            Next →
          </button>
        </div>
      </div>

      {/* Floating Action Dock for Batch Operations */}
      {selected.size > 0 ? (
        <div className="sticky bottom-4 z-20 mx-auto flex max-w-xl flex-wrap items-center justify-between gap-x-3 gap-y-2 rounded-xl border border-border/80 bg-background/95 px-4 py-2.5 shadow-2xl backdrop-blur-md transition-all animate-in fade-in slide-in-from-bottom-2">
          <div className="flex items-center gap-2">
            <span className="flex size-6 items-center justify-center rounded-full bg-primary font-mono text-xs font-bold text-primary-foreground">
              {selected.size}
            </span>
            <span className="text-sm font-medium text-foreground">selected</span>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            {(Object.keys(ROUTE_LABELS) as RouteMode[]).map((mode) => {
              const count = routeItems(mode, selected).length;
              return (
                <button
                  key={mode}
                  type="button"
                  disabled={count === 0}
                  onClick={() => route(mode)}
                  className={`${outlineBtnCls} h-8 px-2.5 text-xs font-semibold ${
                    count > 0 ? "border-primary/40 bg-primary/10 text-primary-text hover:bg-primary/20" : ""
                  }`}
                >
                  {mode === "videos" ? (
                    <Video className="size-3.5" />
                  ) : mode === "channel" ? (
                    <User className="size-3.5" />
                  ) : (
                    <ListVideo className="size-3.5" />
                  )}
                  <span>{kindCountLabel(count, ROUTE_LABELS[mode])}</span>
                </button>
              );
            })}

            <button
              type="button"
              onClick={() => setSelected(new Map())}
              aria-label="Clear selection"
              title="Clear selection"
              className="inline-flex size-8 items-center justify-center rounded-md border border-border text-muted-foreground hover:bg-muted hover:text-foreground cursor-pointer"
            >
              <X className="size-4" aria-hidden />
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function FilterGrid({ onReset }: { onReset: () => void }): JSX.Element {
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-x-4 gap-y-3 sm:grid-cols-2 lg:grid-cols-4">
        <FilterField label="Duration" htmlFor="ycs-f-duration">
          <select id="ycs-f-duration" name="duration" className={selectCls} defaultValue="">
            <option value="">Any duration</option>
            <option value="Under 4 minutes">Under 4 minutes</option>
            <option value="4 - 20 minutes">4 - 20 minutes</option>
            <option value="Over 20 minutes">Over 20 minutes</option>
          </select>
        </FilterField>

        <FilterField label="Kind" htmlFor="ycs-f-kind">
          <select id="ycs-f-kind" name="kind_filter" className={selectCls} defaultValue="">
            <option value="">Any kind</option>
            <option value="video">Videos</option>
            <option value="channel">Channels</option>
            <option value="playlist">Playlists</option>
          </select>
        </FilterField>

        <FilterField label="Uploaded after" htmlFor="ycs-f-date_after">
          <Input
            type="date"
            name="date_after"
            id="ycs-f-date_after"
            autoComplete="off"
            className="h-9 text-sm"
          />
        </FilterField>

        <FilterField label="Uploaded before" htmlFor="ycs-f-date_before">
          <Input
            type="date"
            name="date_before"
            id="ycs-f-date_before"
            autoComplete="off"
            className="h-9 text-sm"
          />
        </FilterField>

        <FilterField label="Min views" htmlFor="ycs-f-min_views">
          <Input
            type="number"
            name="min_views"
            id="ycs-f-min_views"
            min={0}
            placeholder="e.g. 10000"
            className="h-9 text-sm"
          />
        </FilterField>

        <FilterField label="Max views" htmlFor="ycs-f-max_views">
          <Input type="number" name="max_views" id="ycs-f-max_views" min={0} className="h-9 text-sm" />
        </FilterField>

        <FilterField label="Min likes" htmlFor="ycs-f-min_likes">
          <Input type="number" name="min_likes" id="ycs-f-min_likes" min={0} className="h-9 text-sm" />
        </FilterField>

        <FilterField label="Title contains" htmlFor="ycs-f-title">
          <Input
            name="title_contains"
            id="ycs-f-title"
            placeholder="Plain text or *=op"
            className="h-9 text-sm"
          />
        </FilterField>

        <FilterField label="Channel name" htmlFor="ycs-f-channel">
          <Input
            name="channel_name"
            id="ycs-f-channel"
            placeholder="Creator or channel"
            className="h-9 text-sm"
          />
        </FilterField>

        <div className="flex flex-wrap items-center gap-x-6 sm:col-span-2 lg:col-span-3">
          <label className="flex min-h-9 items-center gap-2 text-sm font-medium cursor-pointer">
            <input
              type="checkbox"
              name="sort_by_date"
              className="size-4 rounded border-border text-primary"
            />
            <span>Newest first</span>
          </label>
          <label className="flex min-h-9 items-center gap-2 text-sm font-medium cursor-pointer">
            <input
              type="checkbox"
              name="exclude_shorts"
              className="size-4 rounded border-border text-primary"
            />
            <span>Exclude shorts</span>
          </label>
        </div>
      </div>

      <div className="flex items-center justify-between border-t border-border pt-3">
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={onReset}
          className="inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground"
        >
          <RotateCcw className="size-3.5" />
          <span>Reset fields</span>
        </Button>
        <Button type="submit" size="sm" className="inline-flex items-center gap-1.5">
          <span>Apply filters</span>
          <ArrowRight className="size-3.5" />
        </Button>
      </div>
    </div>
  );
}

function FilterField({
  label,
  htmlFor,
  children,
}: {
  label: string;
  htmlFor: string;
  children: ReactNode;
}): JSX.Element {
  return (
    <div>
      <Label htmlFor={htmlFor} className="mb-1.5 block text-xs font-medium text-muted-foreground">
        {label}
      </Label>
      {children}
    </div>
  );
}
