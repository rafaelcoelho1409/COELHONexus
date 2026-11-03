"use client";

import { useId, useMemo, useState } from "react";

import { type BookSearchHit, searchBookPages } from "../domain/book-search";
import type { PageRow } from "../domain/pages";
import { useBookPageIndex } from "../hooks/use-book-page-index";
import { useModalDialog } from "../hooks/use-modal-dialog";
import { BOOK_SEARCH_MAX_HITS } from "../params";

export function BookSearch({
  bookId,
  unitLabel,
  rows,
  onChoose,
  onClose,
}: {
  bookId: string;
  unitLabel: string;
  rows: readonly PageRow[];
  onChoose: (hit: BookSearchHit) => void;
  onClose: () => void;
}) {
  const ref = useModalDialog();
  const listId = useId();
  const { entries, loading, failed } = useBookPageIndex(bookId, rows, true);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState(0);
  const hits = useMemo(() => searchBookPages(entries, query, BOOK_SEARCH_MAX_HITS), [entries, query]);
  const active = hits.length === 0 ? -1 : Math.min(selected, hits.length - 1);
  const optionId = (index: number) => `${listId}-${index}`;
  const status = loading
    ? "Indexing extracted pages…"
    : query.trim() === ""
      ? "Search titles, headings, and extracted text."
      : hits.length === 0
        ? "No matches."
        : `${hits.length} result${hits.length === 1 ? "" : "s"}`;

  return (
    <dialog
      ref={ref}
      aria-labelledby={`${listId}-title`}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      className="mx-auto mt-[12vh] mb-auto w-[calc(100%-2rem)] max-w-xl overflow-hidden rounded-md border border-border bg-card p-0 text-foreground backdrop:bg-background/80"
    >
      <h2 id={`${listId}-title`} className="px-4 pt-3 text-base font-medium">
        Search this book
      </h2>
      <label htmlFor={`${listId}-query`} className="block px-4 pt-2 text-xs text-muted-foreground">
        Search titles, headings, and extracted text
      </label>
      <input
        id={`${listId}-query`}
        type="search"
        autoFocus
        role="combobox"
        aria-expanded={hits.length > 0}
        aria-controls={listId}
        aria-activedescendant={active >= 0 ? optionId(active) : undefined}
        aria-autocomplete="list"
        autoComplete="off"
        spellCheck={false}
        placeholder="Search extracted contents…"
        value={query}
        onChange={(event) => {
          setQuery(event.target.value);
          setSelected(0);
        }}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown") {
            event.preventDefault();
            if (hits.length > 0) setSelected(Math.min(active + 1, hits.length - 1));
          } else if (event.key === "ArrowUp") {
            event.preventDefault();
            if (hits.length > 0) setSelected(Math.max(active - 1, 0));
          } else if (event.key === "Enter") {
            event.preventDefault();
            const hit = hits[active];
            if (hit) onChoose(hit);
          }
        }}
        className="h-12 w-full border-b border-border bg-transparent px-4 text-sm outline-none"
      />
      <div id={listId} role="listbox" aria-label="Book search results" className="max-h-80 overflow-y-auto">
        {hits.map((hit, index) => (
          <button
            key={`${hit.page}:${hit.sub}`}
            type="button"
            id={optionId(index)}
            role="option"
            tabIndex={-1}
            aria-selected={index === active}
            onClick={() => onChoose(hit)}
            className={`flex min-h-9 w-full items-baseline gap-3 px-4 py-2 text-left text-sm ${
              index === active ? "bg-muted" : "hover:bg-muted"
            }`}
          >
            <span className="shrink-0 font-mono text-xs text-muted-foreground">
              {unitLabel} {hit.page}
            </span>
            <span className="min-w-0 truncate font-medium">{hit.title}</span>
            {hit.sub !== hit.title ? (
              <span className="min-w-0 truncate text-muted-foreground">{hit.sub}</span>
            ) : null}
          </button>
        ))}
      </div>
      <p role="status" className="border-t border-border px-4 py-2 text-xs text-muted-foreground">
        {failed > 0 && !loading ? `${status} · ${failed} page${failed === 1 ? "" : "s"} unavailable` : status}
      </p>
    </dialog>
  );
}
