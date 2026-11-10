"use client";

import { ChevronDown, RotateCw, Trash2 } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import type { BookRow } from "@/shared/api";
import { apiErrorMessage } from "@/shared/lib/api-error";
import { bookFormat, bookUnits, fmtBytes, isLive, newestFirst, toBookStatus } from "../domain/books";
import { useDeleteBook, useLibrary, useRunBook } from "../hooks/use-bd-data";
import { useBdOverlays } from "../hooks/use-overlays";
import { ActionButton } from "./action-button";
import { StatusChip } from "./status-chip";

const titleOf = (row: BookRow) => row.title.trim() || row.filename;

export function LibraryPicker({
  selected,
  onOpen,
  onRefresh,
  autoChain = false,
  showActions = true,
  showSelectedTitle = true,
}: {
  selected: string | null;
  onOpen: (bookId: string | null) => void;
  onRefresh?: (bookId: string) => void;
  autoChain?: boolean;
  showActions?: boolean;
  showSelectedTitle?: boolean;
}) {
  const library = useLibrary();
  const del = useDeleteBook();
  const runBook = useRunBook();
  const { confirm, notice } = useBdOverlays();
  const rows = newestFirst(library.data ?? []);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const boxRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const searchRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onDocClick = (event: MouseEvent) => {
      if (boxRef.current && event.target instanceof Node && !boxRef.current.contains(event.target)) {
        setOpen(false);
      }
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        setQuery("");
        triggerRef.current?.focus();
      }
    };
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  useEffect(() => {
    if (open) searchRef.current?.focus();
  }, [open]);

  const filtered = useMemo(() => {
    const search = query.trim().toLowerCase();
    if (!search) return rows;
    return rows.filter((row) => `${titleOf(row)} ${row.filename}`.toLowerCase().includes(search));
  }, [rows, query]);
  const currentBook = rows.find((row) => row.book_id === selected);
  const busy = runBook.isPending || del.isPending;

  const close = () => {
    setOpen(false);
    setQuery("");
  };

  const remove = async (row: BookRow) => {
    const yes = await confirm({
      title: "Delete this book?",
      message: `This deletes ${titleOf(row)} — the uploaded book and everything extracted from it — permanently.`,
      confirmLabel: "Delete",
    });
    if (!yes) return;
    del.mutate(row.book_id, {
      onSuccess: (result) => {
        if (!result.deleted) {
          notice(result.reason ?? "The book could not be deleted.", "error");
          return;
        }
        notice(`Deleted ${titleOf(row)}.`);
        if (row.book_id === selected) onOpen(null);
      },
      onError: (error) => notice(apiErrorMessage(error), "error"),
    });
  };

  const refreshBook = (row: BookRow) => {
    if (!onRefresh) return;
    runBook.mutate(
      { bookId: row.book_id, refresh: true, autoChain },
      {
        onSuccess: (result) => {
          if (result.status === "queued") {
            close();
            onRefresh(row.book_id);
            notice("Extraction queued.");
          } else if (result.status === "locked") {
            close();
            onRefresh(row.book_id);
            notice("This book is already being extracted — following the live run.");
          } else {
            notice("Already extracted.");
          }
        },
        onError: (error) => notice(apiErrorMessage(error), "error"),
      },
    );
  };

  return (
    <div className="relative min-w-0" ref={boxRef}>
      <ActionButton
        ref={triggerRef}
        className="max-w-[min(24rem,calc(100vw-2rem))]"
        aria-haspopup="true"
        aria-expanded={open}
        aria-controls={open ? "bd-library-options" : undefined}
        onClick={() => setOpen((value) => !value)}
      >
        <span className="inline-flex max-w-72 items-center gap-2 truncate">
          {currentBook && showSelectedTitle
            ? titleOf(currentBook)
            : `Library${library.data ? ` · ${rows.length}` : ""}`}
          <ChevronDown
            className={`size-4 shrink-0 text-muted-foreground ${open ? "rotate-180" : ""}`}
            aria-hidden="true"
          />
        </span>
      </ActionButton>
      {open ? (
        <div
          id="bd-library-options"
          className="absolute left-0 z-40 mt-1 w-[min(24rem,calc(100vw-2rem))] rounded-md border border-border bg-card p-2"
        >
          <label htmlFor="bd-library-search" className="mb-1 block text-xs font-medium text-muted-foreground">
            Search books
          </label>
          <input
            id="bd-library-search"
            ref={searchRef}
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search books…"
            aria-label="Search books"
            className="mb-1 h-9 w-full rounded-sm border border-border bg-background px-2 text-sm"
          />
          {library.isError ? (
            <div role="alert" className="p-2 text-sm text-destructive-text">
              <p>The library could not be loaded.</p>
              <div className="mt-1">
                <ActionButton onClick={() => void library.refetch()}>Try again</ActionButton>
              </div>
            </div>
          ) : (
            <ul aria-label="Ingested books" className="max-h-64 overflow-auto">
              {filtered.map((row) => {
                const status = toBookStatus(row.status);
                const units = bookUnits(row.filename);
                const current = row.book_id === selected;
                return (
                  <li
                    key={row.book_id}
                    className={`group grid min-w-0 ${showActions && onRefresh ? "grid-cols-[minmax(0,1fr)_auto_auto]" : "grid-cols-1"} items-center gap-1 rounded-sm px-1 ${current ? "bg-muted" : "hover:bg-muted"}`}
                  >
                    <button
                      type="button"
                      aria-pressed={current}
                      onClick={() => {
                        close();
                        onOpen(row.book_id);
                      }}
                      className="flex min-h-12 min-w-0 flex-col justify-center gap-0.5 px-2 py-1 text-left text-sm"
                    >
                      <span title={titleOf(row)} className="block w-full min-w-0 truncate font-medium">
                        {titleOf(row)}
                      </span>
                      <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-muted-foreground">
                        <StatusChip status={status} />
                        <span className="tabular">
                          {bookFormat(row.filename)} · {row.pages_done}/{row.n_pages} {units.plural} ·{" "}
                          {fmtBytes(row.size_bytes)}
                        </span>
                      </span>
                    </button>
                    {showActions && onRefresh ? (
                      <>
                        <ActionButton
                          iconOnly
                          aria-label={`Refresh ${titleOf(row)}`}
                          title="Refresh extraction"
                          disabled={busy || isLive(status)}
                          onClick={() => refreshBook(row)}
                        >
                          <RotateCw aria-hidden="true" />
                        </ActionButton>
                        <ActionButton
                          iconOnly
                          aria-label={`Delete ${titleOf(row)}`}
                          title={isLive(status) ? "Cancel the extraction first" : "Delete"}
                          disabled={busy || isLive(status)}
                          onClick={() => void remove(row)}
                        >
                          <Trash2 aria-hidden="true" />
                        </ActionButton>
                      </>
                    ) : null}
                  </li>
                );
              })}
              {filtered.length === 0 ? (
                <li className="px-2 py-3 text-sm text-muted-foreground">
                  {rows.length > 0
                    ? query
                      ? "No match."
                      : "No books yet. Upload a PDF or EPUB to start."
                    : library.isPending
                      ? "Loading library…"
                      : "No books yet. Upload a PDF or EPUB to start."}
                </li>
              ) : null}
            </ul>
          )}
        </div>
      ) : null}
    </div>
  );
}
