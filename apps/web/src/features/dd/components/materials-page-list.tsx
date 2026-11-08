"use client";

/** The ingested pages as a list under their section headings, in the order of the original documentation, the open one
 * marked: the left column of the page and of the reading window. A filter box and the tier chips narrow it; the arrow keys
 * move through the pages on show. */

import { type RefObject, useEffect, useRef } from "react";

import { fmtBytes } from "../domain/format";
import { type ExplorerGroup, ROOT_GROUP } from "../domain/ingestion";

/** Scrolls `list` — and only it, never the document — just far enough for `item` to be in view. */
function revealIn(list: HTMLElement, item: HTMLElement) {
  const top = item.offsetTop;
  const bottom = top + item.offsetHeight;
  if (top < list.scrollTop) list.scrollTop = top;
  else if (bottom > list.scrollTop + list.clientHeight) list.scrollTop = bottom - list.clientHeight;
}

export function MaterialsPageList({
  groups,
  selected,
  shown,
  total,
  query,
  onQuery,
  tierValues,
  tiers,
  onToggleTier,
  onPick,
  onMove,
  filterRef,
  emptyHint,
}: {
  groups: readonly ExplorerGroup[];
  /** The `idx` of the page that is open. */
  selected: number | null;
  /** How many pages the filters leave, out of `total`. */
  shown: number;
  total: number;
  query: string;
  onQuery: (query: string) => void;
  tierValues: readonly string[];
  tiers: ReadonlySet<string>;
  onToggleTier: (tier: string) => void;
  onPick: (idx: number) => void;
  /** The arrow keys: one page on (1) or back (-1). */
  onMove: (delta: 1 | -1) => void;
  filterRef: RefObject<HTMLInputElement | null>;
  emptyHint: string;
}) {
  const list = useRef<HTMLDivElement | null>(null);

  // The page opened from elsewhere (previous/next, search) is brought into view in the list.
  // biome-ignore lint/correctness/useExhaustiveDependencies: `selected` is the trigger, not a value read here
  useEffect(() => {
    const item = list.current?.querySelector<HTMLElement>('[aria-current="page"]');
    if (list.current && item) revealIn(list.current, item);
  }, [selected]);

  return (
    <nav
      aria-label="Pages"
      className="flex min-h-0 flex-1 flex-col gap-2"
      onKeyDown={(event) => {
        if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
        event.preventDefault();
        onMove(event.key === "ArrowDown" ? 1 : -1);
      }}
    >
      <div className="flex flex-wrap items-center gap-2">
        <input
          ref={filterRef}
          value={query}
          onChange={(event) => onQuery(event.target.value)}
          onKeyDown={(event) => event.key === "Escape" && onQuery("")}
          placeholder="Filter pages  ( / )"
          aria-label="Filter pages"
          className="h-9 min-w-40 flex-1 rounded-sm border border-border bg-background px-2 text-sm"
        />
        {tierValues.map((tier) => {
          const active = tiers.has(tier);
          return (
            <button
              key={tier}
              type="button"
              aria-pressed={active}
              onClick={() => onToggleTier(tier)}
              className={`min-h-9 rounded-sm border px-2 text-xs ${
                active
                  ? "border-primary bg-primary/10 text-primary-text"
                  : "border-border text-muted-foreground hover:text-foreground"
              }`}
            >
              {tier}
            </button>
          );
        })}
      </div>
      <div
        ref={list}
        // biome-ignore lint/a11y/noNoninteractiveTabindex: a long list scrolls, so keyboard users must be able to focus it to scroll it (WCAG 2.1.1)
        tabIndex={0}
        className="relative max-h-72 min-h-0 overflow-auto lg:max-h-none lg:flex-1"
      >
        {groups.map((group) => (
          <div key={group.prefix}>
            {group.prefix ? (
              <p className="border-b border-border bg-muted px-2 py-1 text-xs font-medium text-muted-foreground">
                {group.prefix === ROOT_GROUP ? "(root)" : group.prefix}
              </p>
            ) : null}
            <ul>
              {group.items.map(({ entry }) => (
                <li key={entry.idx}>
                  <button
                    type="button"
                    onClick={() => onPick(entry.idx)}
                    aria-current={selected === entry.idx ? "page" : undefined}
                    className={`flex min-h-12 w-full items-baseline gap-2 border-l-2 px-2 py-1 text-left text-sm hover:bg-muted ${
                      selected === entry.idx ? "border-primary bg-muted" : "border-transparent"
                    }`}
                  >
                    <span
                      className={`min-w-0 flex-1 truncate ${
                        selected === entry.idx ? "font-medium text-foreground" : "text-foreground"
                      }`}
                    >
                      {entry.title || entry.slug}
                    </span>
                    <span aria-hidden className="ml-auto shrink-0 text-right text-xs text-muted-foreground">
                      {[entry.tier, fmtBytes(entry.bytes ?? 0)].filter(Boolean).join(" · ")}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ))}
        {groups.length === 0 ? (
          <p className="p-4 text-center text-xs text-muted-foreground">{emptyHint}</p>
        ) : null}
      </div>
      <p aria-live="polite" className="text-xs text-muted-foreground">
        {shown} of {total} pages
      </p>
    </nav>
  );
}
