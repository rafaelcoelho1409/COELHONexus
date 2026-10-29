"use client";

/** Source · Search — a yt-dlp search over videos, channels and playlists.
 *
 * Layout, top to bottom: the query row (with one "Filters" disclosure), the filter chips of the last search, then one
 * results card — a sticky header (master checkbox, status, density, page size, paging), the rows, and a pager. A bar sticks
 * to the bottom of the screen while rows are selected. yt-dlp has no offset, so a page is a slice of a growing cache: asking
 * for page N fetches `N × size` results again (3–10 s), which is why paging is explicit and the status line says what is loading. */

import { useMutation } from "@tanstack/react-query";
import { Loader2, RotateCcw, Search, SlidersHorizontal, X } from "lucide-react";
import type { FormEvent, JSX } from "react";
import { useEffect, useRef, useState } from "react";

import { apiErrorMessage } from "@/shared/lib/api-error";
import { Button } from "@/shared/ui/button";
import { Input } from "@/shared/ui/input";
import { Label } from "@/shared/ui/label";

import { searchVideos } from "../api";
import {
  dropRouted,
  filterChips,
  masterState,
  panelFilterCount,
  rangeLabel,
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
} from "../params";
import { FilterChips } from "./filter-chips";
import { type Density, ResultRow, ResultSkeleton } from "./result-list";
import { ResultsHeader } from "./results-header";
import { MoreFilters, QuickFilters } from "./search-filters";
import { type RouteMode, SelectionBar } from "./selection-bar";

export type Routed = { mode: RouteMode; items: string[] };

const SKELETON_KEYS = ["sk-1", "sk-2", "sk-3", "sk-4", "sk-5"] as const;
const FILTERS_ID = "ycs-filters";

/** A search that failed, with what it takes to ask again. */
type Failure = { message: string; req: SearchRequestPayload; needed: number; target: number };

const parseSize = (value: string): number => {
  const n = Number.parseInt(value, 10);
  return PAGE_SIZES.includes(n) ? n : PAGE_SIZE_DEFAULT;
};

/** Empty a named form field — the filter a chip stands for. */
function clearField(form: HTMLFormElement, name: string) {
  const field = form.elements.namedItem(name);
  if (field instanceof RadioNodeList)
    field.value = ""; // the group's "All"
  else if (field instanceof HTMLInputElement && field.type === "checkbox") field.checked = false;
  else if (field instanceof HTMLInputElement || field instanceof HTMLSelectElement) field.value = "";
}

export function SearchTab({ onRoute }: { onRoute: (routed: Routed) => void }): JSX.Element {
  const formRef = useRef<HTMLFormElement>(null);
  const queryRef = useRef<HTMLInputElement>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const pageMoved = useRef(false);

  const [cache, setCache] = useState<VideoSnippet[]>([]);
  const [page, setPage] = useState(1);
  const [exhausted, setExhausted] = useState(false);
  const [baseReq, setBaseReq] = useState<SearchRequestPayload | null>(null);
  const [elapsed, setElapsed] = useState<number | null>(null);
  const [loadingLabel, setLoadingLabel] = useState("Searching…");
  const [failure, setFailure] = useState<Failure | null>(null);
  const [selected, setSelected] = useState<Map<string, VideoSnippet>>(new Map());
  const [lastCheckedId, setLastCheckedId] = useState<string | null>(null);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [densityPref, setDensityPref] = useBrowserPref(RESULTS_DENSITY_STORAGE_KEY, DENSITY_DEFAULT);
  const [sizePref, setSizePref] = useBrowserPref(PAGE_SIZE_STORAGE_KEY, String(PAGE_SIZE_DEFAULT));

  const size = parseSize(sizePref);
  const density: Density = densityPref === "comfortable" ? "comfortable" : "compact";

  const searchMutation = useMutation({
    mutationFn: ({ req, needed }: { req: SearchRequestPayload; needed: number }) =>
      searchVideos({ ...req, max_results: needed }),
  });
  const pending = searchMutation.isPending;

  // "/" jumps to the query, as on GitHub and YouTube — unless the reader is typing somewhere already.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "/" || event.metaKey || event.ctrlKey || event.altKey) return;
      const target = event.target;
      if (target instanceof HTMLElement && /^(input|textarea|select)$/i.test(target.tagName)) return;
      if (target instanceof HTMLElement && target.isContentEditable) return;
      event.preventDefault();
      queryRef.current?.focus();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  // After a page change, focus moves to the results so a keyboard or screen-reader user starts at the new rows.
  // biome-ignore lint/correctness/useExhaustiveDependencies: runs once per page change; `pageMoved` says it was the reader's doing
  useEffect(() => {
    if (!pageMoved.current) return;
    pageMoved.current = false;
    headingRef.current?.focus();
  }, [page]);

  /** Fetch the first `needed` results and show `target` as the page. */
  const fetchPage = async (req: SearchRequestPayload, needed: number, target: number) => {
    setFailure(null);
    try {
      const res = await searchMutation.mutateAsync({ req, needed });
      setCache(res.results);
      setExhausted(res.results.length < needed);
      setElapsed(res.elapsed_s > 0 ? res.elapsed_s : null);
      if (target !== page) pageMoved.current = true;
      setPage(target);
    } catch (error) {
      setFailure({ message: apiErrorMessage(error), req, needed, target });
    }
  };

  const executeSearch = (req: SearchRequestPayload) => {
    setBaseReq(req);
    setCache([]);
    setPage(1);
    setExhausted(false);
    setElapsed(null);
    setSelected(new Map());
    setLastCheckedId(null);
    setFiltersOpen(false);
    setLoadingLabel(`Searching “${req.query}”…`);
    void fetchPage(req, size, 1);
  };

  const readForm = (): SearchRequestPayload | null => {
    if (!formRef.current) return null;
    try {
      return readSearchRequest(new FormData(formRef.current), size);
    } catch {
      return null; // no query
    }
  };

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const req = readForm();
    if (req) executeSearch(req);
    else setFailure({ message: "Enter a search query.", req: { query: "" }, needed: size, target: 1 });
  };

  const total = cache.length;
  const slice = cache.slice((page - 1) * size, page * size);
  const master = masterState(slice, selected);
  const chips = baseReq ? filterChips(baseReq) : [];
  const panelCount = panelFilterCount(chips);

  const goPrev = () => {
    if (!baseReq || pending || page <= 1) return;
    pageMoved.current = true;
    setPage(page - 1);
  };

  const goNext = () => {
    if (!baseReq || pending) return;
    const target = page + 1;
    const needed = target * size;
    if (exhausted || cache.length >= needed) {
      pageMoved.current = true;
      setPage(target);
      return;
    }
    setLoadingLabel(`Loading page ${target}… each new page asks YouTube again and can take a few seconds.`);
    void fetchPage(baseReq, needed, target);
  };

  const changeSize = (value: string) => {
    setSizePref(value);
    const next = parseSize(value);
    if (!baseReq || pending || next === size) return;
    setCache([]);
    setExhausted(false);
    setLoadingLabel(`Loading ${next} results…`);
    void fetchPage(baseReq, next, 1);
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
    if (items.length === 0) return;
    onRoute({ mode, items });
    setSelected((prev) => dropRouted(prev, mode, items));
  };

  const copyId = (id: string) => {
    const clipboard = navigator.clipboard;
    if (clipboard) void clipboard.writeText(id).catch(() => undefined);
  };

  const removeFilter = (key: string) => {
    if (formRef.current) clearField(formRef.current, key);
    const req = readForm();
    if (req) executeSearch(req);
  };

  const resetFilters = () => {
    const form = formRef.current;
    if (!form) return;
    for (const element of Array.from(form.elements)) {
      if (
        (element instanceof HTMLInputElement || element instanceof HTMLSelectElement) &&
        element.name &&
        element.name !== "query"
      ) {
        clearField(form, element.name);
      }
    }
    const req = readForm();
    if (req && baseReq) executeSearch(req);
  };

  // The alert under the header carries the error text; the live status only says that it happened.
  const status = failure
    ? "Search failed"
    : pending
      ? loadingLabel
      : `${rangeLabel(page, size, total, exhausted)}${elapsed ? ` · ${elapsed.toFixed(1)} s` : ""}`;
  const showResults = baseReq !== null;
  const canPrev = !pending && page > 1;
  const canNext = !pending && !(exhausted && page * size >= total);

  return (
    <div className="flex flex-col gap-4">
      <form ref={formRef} onSubmit={onSubmit} className="flex flex-col gap-3">
        <div className="flex flex-wrap items-end gap-x-3 gap-y-2">
          <div className="min-w-0 flex-[1_1_20rem]">
            <Label
              htmlFor="ycs-search-query"
              className="mb-1.5 block text-xs font-medium text-muted-foreground"
            >
              Search
            </Label>
            <div className="relative">
              <Input
                ref={queryRef}
                id="ycs-search-query"
                name="query"
                required
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Videos, channels or playlists on YouTube"
                autoComplete="off"
                className="h-10 pr-10 text-sm"
              />
              {query ? (
                <button
                  type="button"
                  onClick={() => {
                    setQuery("");
                    queryRef.current?.focus();
                  }}
                  aria-label="Clear the query"
                  className="absolute top-0.5 right-0.5 inline-flex size-9 items-center justify-center rounded-sm text-muted-foreground hover:text-foreground"
                >
                  <X aria-hidden className="size-4" />
                </button>
              ) : (
                <kbd
                  aria-hidden
                  className="pointer-events-none absolute top-1/2 right-3 hidden -translate-y-1/2 rounded-sm border border-border px-1.5 font-mono text-xs text-muted-foreground sm:block"
                >
                  /
                </kbd>
              )}
            </div>
          </div>
          <Button type="submit" size="lg" className="h-10 px-4" disabled={pending}>
            {pending ? <Loader2 aria-hidden className="animate-spin" /> : <Search aria-hidden />}
            Search
          </Button>
        </div>

        {/* The three filters most searches start with stay in view; the rest open from "More filters". */}
        <div className="flex flex-wrap items-end gap-x-4 gap-y-3">
          <QuickFilters />
          <Button
            type="button"
            variant="outline"
            size="lg"
            className="sm:ml-auto"
            aria-expanded={filtersOpen}
            aria-controls={FILTERS_ID}
            onClick={() => setFiltersOpen(!filtersOpen)}
          >
            <SlidersHorizontal aria-hidden />
            More filters
            {panelCount > 0 ? (
              <span className="tabular rounded-sm bg-primary px-1.5 text-xs text-primary-foreground">
                <span aria-hidden>{panelCount}</span>
                <span className="sr-only">, {panelCount} active</span>
              </span>
            ) : null}
          </Button>
        </div>

        <MoreFilters
          id={FILTERS_ID}
          open={filtersOpen}
          onClose={() => setFiltersOpen(false)}
          onReset={resetFilters}
        />
        <FilterChips chips={chips} onRemove={removeFilter} onClear={resetFilters} />
      </form>

      <section aria-labelledby="ycs-results-heading" className="rounded-md border border-border bg-card">
        <h3 id="ycs-results-heading" ref={headingRef} tabIndex={-1} className="sr-only scroll-mt-32">
          Search results
        </h3>

        {showResults ? (
          <ResultsHeader
            master={master}
            visibleCount={slice.length}
            status={status}
            failed={failure !== null}
            density={density}
            onDensity={setDensityPref}
            size={size}
            onSize={changeSize}
            canPrev={canPrev}
            canNext={canNext}
            onPrev={goPrev}
            onNext={goNext}
            onToggleMaster={() => setSelected(selectRange(selected, slice, master !== "all"))}
          />
        ) : null}

        {failure ? (
          <div
            role="alert"
            className="flex flex-wrap items-center gap-3 border-b border-border px-3 py-3 text-sm"
          >
            <p className="min-w-0 flex-1 text-destructive-text">{failure.message}</p>
            {failure.req.query ? (
              <Button
                type="button"
                variant="outline"
                size="lg"
                disabled={pending}
                onClick={() => void fetchPage(failure.req, failure.needed, failure.target)}
              >
                <RotateCcw aria-hidden />
                Try again
              </Button>
            ) : null}
          </div>
        ) : null}

        {pending && slice.length === 0 ? (
          <ul aria-label="Loading search results" aria-busy>
            {SKELETON_KEYS.map((key) => (
              <ResultSkeleton key={key} density={density} />
            ))}
          </ul>
        ) : slice.length > 0 ? (
          <ul aria-label="Search results" aria-busy={pending}>
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
          <EmptyState
            query={baseReq?.query ?? null}
            failed={failure !== null}
            filtered={chips.length > 0}
            onReset={resetFilters}
          />
        )}

        {slice.length > 0 ? (
          <div className="flex items-center justify-between gap-3 rounded-b-md border-t border-border px-3 py-2">
            <Button type="button" variant="outline" size="lg" disabled={!canPrev} onClick={goPrev}>
              ← Previous
            </Button>
            <p className="tabular text-xs text-muted-foreground">Page {page}</p>
            <Button type="button" variant="outline" size="lg" disabled={!canNext} onClick={goNext}>
              {`Next ${size} →`}
            </Button>
          </div>
        ) : null}
      </section>

      {selected.size > 0 ? (
        <SelectionBar
          selected={selected.size}
          counts={{
            videos: routeItems("videos", selected).length,
            channel: routeItems("channel", selected).length,
            playlist: routeItems("playlist", selected).length,
          }}
          onRoute={route}
          onClear={() => setSelected(new Map())}
        />
      ) : null}
    </div>
  );
}

/** What the results area says when it has no rows: before any search, after a search that found nothing, or after a failure. */
function EmptyState({
  query,
  failed,
  filtered,
  onReset,
}: {
  query: string | null;
  failed: boolean;
  filtered: boolean;
  onReset: () => void;
}): JSX.Element | null {
  if (failed && query) return null; // the alert above says what happened
  return (
    <div className="flex flex-col items-start gap-2 px-4 py-10 sm:items-center sm:text-center">
      {query === null ? (
        <>
          <h4 className="text-sm font-medium">Search YouTube</h4>
          <p className="max-w-md text-sm text-muted-foreground">
            Find videos, channels and playlists, tick the ones you want, and send them to the Videos, Channel
            or Playlist tab to ingest. Press{" "}
            <kbd className="rounded-sm border border-border px-1 font-mono text-xs">/</kbd> to jump to the
            search box.
          </p>
        </>
      ) : (
        <>
          <h4 className="text-sm font-medium">No matches for “{query}”</h4>
          <p className="max-w-md text-sm text-muted-foreground">
            {filtered ? "The filters may be too narrow." : "Try other words."}
          </p>
          {filtered ? (
            <Button type="button" variant="outline" size="lg" onClick={onReset}>
              <RotateCcw aria-hidden />
              Reset filters
            </Button>
          ) : null}
        </>
      )}
    </div>
  );
}
