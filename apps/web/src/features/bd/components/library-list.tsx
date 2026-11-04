"use client";

import { ChevronDown, RotateCw, Trash2 } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import type { BookRow } from "@/shared/api";
import { apiErrorMessage } from "@/shared/lib/api-error";

import { bookUnits, fmtBytes, isLive, newestFirst, toBookStatus } from "../domain/books";
import { useDeleteBook, useLibrary, useRunBook } from "../hooks/use-bd-data";
import { useBdOverlays } from "../hooks/use-overlays";
import { StatusChip } from "./status-chip";

const titleOf = (row: BookRow) => row.title.trim() || row.filename;

export function LibraryPicker({
  selected,
  onOpen,
  onRefresh,
  autoChain,
}: {
  selected: string | null;
  onOpen: (bookId: string | null) => void;
  onRefresh: (bookId: string) => void;
  autoChain: boolean;
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
      <button
        ref={triggerRef}
        type="button"
        aria-expanded={open}
        aria-controls="bd-library-options"
        onClick={() => setOpen((value) => !value)}
        className="flex min-h-10 max-w-[min(24rem,calc(100vw-2rem))] min-w-0 items-center gap-2 rounded-sm border border-border bg-background px-3 text-sm text-foreground hover:border-neutral-8"
      >
        <span className="max-w-72 truncate font-medium">
          {currentBook ? titleOf(currentBook) : `Library${library.data ? ` · ${rows.length}` : ""}`}
        </span>
        <ChevronDown className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
      </button>
      {open ? (
        <div
          id="bd-library-options"
          className="absolute right-0 z-40 mt-1 w-[min(28rem,calc(100vw-2rem))] rounded-md border border-border bg-card p-2"
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
            className="mb-1 h-9 w-full rounded-sm border border-border bg-background px-2 text-sm"
          />
          {library.isError ? (
            <div role="alert" className="p-2 text-sm text-destructive-text">
              <p>The library could not be loaded.</p>
              <button
                type="button"
                onClick={() => void library.refetch()}
                className="mt-1 min-h-9 rounded-sm border border-border px-3 text-foreground hover:border-neutral-8"
              >
                Try again
              </button>
            </div>
          ) : (
            <ul aria-label="Books" className="max-h-80 overflow-auto">
              {filtered.map((row) => {
                const status = toBookStatus(row.status);
                const units = bookUnits(row.filename);
                const current = row.book_id === selected;
                return (
                  <li
                    key={row.book_id}
                    className={`group flex items-center gap-1 rounded-sm border-l-2 px-1 ${
                      current ? "border-primary bg-muted" : "border-transparent hover:bg-muted"
                    }`}
                  >
                    <button
                      type="button"
                      aria-pressed={current}
                      onClick={() => {
                        close();
                        onOpen(row.book_id);
                      }}
                      className="flex min-h-14 min-w-0 flex-1 flex-col justify-center gap-0.5 px-2 py-1 text-left text-sm"
                    >
                      <span className="truncate font-medium">{titleOf(row)}</span>
                      <span className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs text-muted-foreground">
                        <StatusChip status={status} />
                        <span className="tabular">
                          {row.pages_done}/{row.n_pages} {units.plural}
                        </span>
                        <span>{fmtBytes(row.size_bytes)}</span>
                      </span>
                    </button>
                    <button
                      type="button"
                      aria-label={`Refresh ${titleOf(row)}`}
                      title="Refresh extraction"
                      disabled={busy || isLive(status)}
                      onClick={() => refreshBook(row)}
                      className="inline-flex size-9 shrink-0 items-center justify-center rounded-sm text-muted-foreground hover:text-foreground disabled:opacity-50"
                    >
                      <RotateCw className="size-4" aria-hidden="true" />
                    </button>
                    <button
                      type="button"
                      aria-label={`Delete ${titleOf(row)}`}
                      title={isLive(status) ? "Cancel the extraction first" : "Delete"}
                      disabled={busy || isLive(status)}
                      onClick={() => void remove(row)}
                      className="inline-flex size-9 shrink-0 items-center justify-center rounded-sm text-muted-foreground hover:text-destructive-text disabled:opacity-50"
                    >
                      <Trash2 className="size-4" aria-hidden="true" />
                    </button>
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
