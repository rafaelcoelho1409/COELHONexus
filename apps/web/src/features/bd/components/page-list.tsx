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
  onFollow: (follow: boolean) => void;
  onPick: (page: number) => void;
}) {
  const list = useRef<HTMLOListElement | null>(null);
  const [query, setQuery] = useState("");
  const [onlyReview, setOnlyReview] = useState(false);
  const count = rows.length;
  const reviewCount = rows.filter((row) => flags.get(row.page) === "review").length;
  const searched = filterPageRows(rows, query);
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
          {units.plural[0]?.toUpperCase()}
          {units.plural.slice(1)} · {count}
          {total > 0 ? ` of ${total}` : ""}
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
      {query.trim() || (onlyReview && reviewCount > 0) ? (
        <p role="status" className="mt-1 text-xs text-muted-foreground">
          Showing {visibleRows.length} of {count} {units.plural}
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
                      {units.singular === "page" ? `p. ${row.page}` : `Section ${row.page}`}
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
                </button>
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
