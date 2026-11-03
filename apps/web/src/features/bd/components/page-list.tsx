"use client";

import { useEffect, useRef } from "react";

import { breadcrumb, type PageRow, pageChips } from "../domain/pages";
import type { BookUnits } from "../domain/books";

/** The extracted content units, in book order, filling in as the stream delivers them. Following keeps the newest unit in
 * view (the list scrolls itself, never the document). Choosing a page stops the following. */
export function PageList({
  rows,
  total,
  units,
  shown,
  following,
  live,
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
  onFollow: (follow: boolean) => void;
  onPick: (page: number) => void;
}) {
  const list = useRef<HTMLOListElement | null>(null);
  const count = rows.length;

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
      {count === 0 ? (
        <p className="mt-2 text-sm text-muted-foreground">
          {live ? `Waiting for the first ${units.singular}…` : "Nothing has been extracted yet."}
        </p>
      ) : (
        <ol ref={list} aria-label={`Extracted ${units.plural}`} className="mt-2 max-h-72 overflow-auto">
          {rows.map((row) => {
            const chips = pageChips(row);
            const where = row.sectionPath.at(-1);
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
                </button>
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
