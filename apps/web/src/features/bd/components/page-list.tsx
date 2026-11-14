"use client";

import { useEffect, useRef, useState } from "react";

import type { BookUnits } from "../domain/books";
import {
  breadcrumb,
  filterPageRows,
  PAGE_FLAG_LABELS,
  type PageFlag,
  type PageRow,
  pageChips,
} from "../domain/pages";
import type { ContentSelection } from "../entities";

const FLAG_STYLE: Record<PageFlag, string> = {
  review: "text-warning-text",
  unverified: "text-warning-text",
  blank: "text-muted-foreground",
  image: "text-muted-foreground",
};

/** The extracted content units, in book order, filling in as the stream delivers them. Following keeps the newest unit in
 * view (the list scrolls itself, never the document). Choosing a page stops the following. */
export function PageList({
  rows,
  total,
  units,
  shown,
  following,
  live,
  flags,
  selection,
  selectionPending,
  selectionError,
  processedNumbers,
  showExcludedPages = true,
  onFollow,
  onPick,
}: {
  rows: readonly PageRow[];
  total: number;
  units: BookUnits;
  shown: number | null;
  following: boolean;
  /** A run is in flight, so there is something to follow. */
  live: boolean;
  /** Whether the page needs review, has an unverified audit, is blank, or contains only an image. */
  flags: ReadonlyMap<number, PageFlag>;
  /** Current reviewed Distill input selection. The source archive remains complete. */
  selection: ContentSelection | null;
  selectionPending: boolean;
  selectionError: boolean;
  processedNumbers?: ReadonlyMap<number, number>;
  showExcludedPages?: boolean;
  onFollow: (follow: boolean) => void;
  onPick: (page: number) => void;
}) {
  const list = useRef<HTMLOListElement | null>(null);
  const [query, setQuery] = useState("");
  const [onlyReview, setOnlyReview] = useState(false);
  const [showExcluded, setShowExcluded] = useState(false);
  const includedPages = new Set(
    selection?.pages.filter((item) => item.disposition === "include").map((item) => item.page_number) ?? [],
  );
  const selectionByPage = new Map(selection?.pages.map((item) => [item.page_number, item]) ?? []);
  const includedRows = selection ? rows.filter((row) => includedPages.has(row.page)) : rows;
  const eligibleRows = selection && !showExcluded ? includedRows : rows;
  const count = rows.length;
  const reviewCount = eligibleRows.filter((row) => flags.get(row.page) === "review").length;
  const searched = filterPageRows(eligibleRows, query);
  const visibleRows =
    onlyReview && reviewCount > 0 ? searched.filter((row) => flags.get(row.page) === "review") : searched;

  useEffect(() => {
    const element = list.current;
    if (following && count > 0 && element) element.scrollTop = element.scrollHeight;
  }, [following, count]);

  return (
    <section aria-labelledby="units-title">
      <div className="flex items-center justify-between gap-3">
        <h3 id="units-title" className="mono-label">
          {processedNumbers && processedNumbers.size > 0
            ? `Processed pages · ${count}`
            : selection
              ? `Pages for Distill · ${includedRows.length} of ${selection.included_page_count}`
              : `${units.plural[0]?.toUpperCase()}${units.plural.slice(1)} · ${count}${total > 0 ? ` of ${total}` : ""}`}
        </h3>
        {live ? (
          <label className="flex min-h-9 items-center gap-2 text-xs text-muted-foreground">
            <input type="checkbox" checked={following} onChange={(event) => onFollow(event.target.checked)} />
            Follow newest
          </label>
        ) : null}
      </div>
      <input
        type="search"
        aria-label={`Search ${units.plural}`}
        placeholder="Search chapter or number"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        className="mt-2 h-9 w-full min-w-0 rounded-sm border border-border bg-background px-2 text-sm"
      />
      {selection ? (
        <>
          <p className="mt-2 text-xs text-muted-foreground">
            {processedNumbers && processedNumbers.size > 0
              ? "Blank and excluded pages are omitted. The original pages remain in the book archive."
              : "The list starts with pages selected for Distill. Original pages are retained in the book archive."}
          </p>
          {showExcludedPages && selection.excluded_page_count > 0 ? (
            <label className="mt-2 flex min-h-9 items-center gap-2 text-xs text-muted-foreground">
              <input
                type="checkbox"
                checked={showExcluded}
                onChange={(event) => setShowExcluded(event.target.checked)}
              />
              Show all source pages, including {selection.excluded_page_count} excluded from Distill
            </label>
          ) : null}
        </>
      ) : selectionPending ? (
        <p role="status" className="mt-2 text-xs text-muted-foreground">
          Loading Distill page selection. All source pages remain available.
        </p>
      ) : selectionError ? (
        <p role="status" className="mt-2 text-xs text-muted-foreground">
          Distill page selection is unavailable. Showing all source pages.
        </p>
      ) : null}
      {reviewCount > 0 ? (
        <label className="mt-2 flex min-h-9 items-center gap-2 text-xs text-muted-foreground">
          <input
            type="checkbox"
            checked={onlyReview}
            onChange={(event) => setOnlyReview(event.target.checked)}
          />
          Only {units.plural} that need review · {reviewCount}
        </label>
      ) : null}
      {query.trim() || (onlyReview && reviewCount > 0) || showExcluded ? (
        <p role="status" className="mt-1 text-xs text-muted-foreground">
          Showing {visibleRows.length} of {eligibleRows.length} source pages
        </p>
      ) : null}
      {count === 0 ? (
        <p className="mt-2 text-sm text-muted-foreground">
          {live ? `Waiting for the first ${units.singular}…` : "Nothing has been extracted yet."}
        </p>
      ) : visibleRows.length === 0 ? (
        <p className="mt-2 text-sm text-muted-foreground">No {units.plural} match that search.</p>
      ) : (
        <ol ref={list} aria-label={`Extracted ${units.plural}`} className="mt-2 max-h-72 overflow-auto">
          {visibleRows.map((row) => {
            const chips = pageChips(row);
            const where = row.sectionPath.at(-1);
            const flag = flags.get(row.page);
            const contentPage = selectionByPage.get(row.page);
            return (
              <li key={row.page}>
                <button
                  type="button"
                  aria-current={row.page === shown ? "true" : undefined}
                  onClick={() => onPick(row.page)}
                  title={breadcrumb(row.sectionPath) || undefined}
                  className={`flex min-h-12 w-full flex-col justify-center border-l-2 px-2 py-1 text-left text-sm ${
                    row.page === shown ? "border-primary bg-muted" : "border-transparent hover:bg-muted"
                  }`}
                >
                  <span className="flex items-baseline gap-2">
                    <span className="tabular shrink-0 font-medium">
                      {units.singular === "page"
                        ? "p. " +
                          (processedNumbers?.get(row.page) ?? row.page) +
                          (processedNumbers?.has(row.page) ? " · source " + row.page : "")
                        : "Section " + row.page}
                    </span>{" "}
                    {where ? (
                      <span className="min-w-0 truncate text-xs text-muted-foreground">{where}</span>
                    ) : null}
                  </span>{" "}
                  {chips.length > 0 ? (
                    <span className="text-xs text-muted-foreground">{chips.join(" · ")}</span>
                  ) : null}
                  {flag ? (
                    <span className={`text-xs font-medium ${FLAG_STYLE[flag]}`}>
                      {PAGE_FLAG_LABELS[flag]}
                    </span>
                  ) : null}
                  {contentPage?.disposition === "exclude" ? (
                    <span className="text-xs font-medium text-warning-text">Excluded from Distill</span>
                  ) : null}
                </button>
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
