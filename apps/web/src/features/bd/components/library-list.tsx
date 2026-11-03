"use client";

import { Trash2 } from "lucide-react";
import Link from "next/link";

import type { BookRow } from "@/shared/api";
import { apiErrorMessage } from "@/shared/lib/api-error";

import { bookUnits, fmtBytes, isLive, newestFirst, toBookStatus } from "../domain/books";
import { useDeleteBook, useLibrary } from "../hooks/use-bd-data";
import { useBdOverlays } from "../hooks/use-overlays";
import { bookHref } from "../params";
import { StatusChip } from "./status-chip";

const titleOf = (row: BookRow) => row.title.trim() || row.filename;

export function LibraryList({
  selected,
  onOpen,
}: {
  selected: string | null;
  onOpen: (bookId: string | null) => void;
}) {
  const library = useLibrary();
  const del = useDeleteBook();
  const { confirm, notice } = useBdOverlays();
  const rows = newestFirst(library.data ?? []);

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

  return (
    <section aria-labelledby="library-title">
      <h2 id="library-title" className="mono-label">
        Library{library.data ? ` · ${rows.length}` : ""}
      </h2>
      {library.isPending ? (
        <p className="mt-2 text-sm text-muted-foreground">Loading the library…</p>
      ) : library.isError ? (
        <div role="alert" className="mt-2 text-sm text-destructive-text">
          <p>The library could not be loaded.</p>
          <button
            type="button"
            onClick={() => void library.refetch()}
            className="mt-1 min-h-9 rounded-sm border border-border px-3 text-foreground hover:border-neutral-8"
          >
            Try again
          </button>
        </div>
      ) : rows.length === 0 ? (
        <p className="mt-2 text-sm text-muted-foreground">No books yet. Upload a PDF or EPUB to start.</p>
      ) : (
        <ul aria-label="Books" className="mt-2 flex flex-col gap-1">
          {rows.map((row) => {
            const status = toBookStatus(row.status);
            const units = bookUnits(row.filename);
            const current = row.book_id === selected;
            return (
              <li
                key={row.book_id}
                className={`flex items-center gap-1 rounded-sm border-l-2 ${
                  current ? "border-primary bg-muted" : "border-transparent hover:bg-muted"
                }`}
              >
                <Link
                  href={bookHref(row.book_id)}
                  scroll={false}
                  aria-current={current ? "page" : undefined}
                  className="flex min-h-14 min-w-0 flex-1 flex-col justify-center gap-0.5 px-2 py-1 text-sm"
                >
                  <span className="truncate font-medium">{titleOf(row)}</span>
                  <span className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs text-muted-foreground">
                    <StatusChip status={status} />
                    <span className="tabular">
                      {row.pages_done}/{row.n_pages} {units.plural}
                    </span>
                    <span>{fmtBytes(row.size_bytes)}</span>
                  </span>
                </Link>
                <button
                  type="button"
                  aria-label={`Delete ${titleOf(row)}`}
                  title={isLive(status) ? "Cancel the extraction first" : "Delete"}
                  disabled={del.isPending || isLive(status)}
                  onClick={() => void remove(row)}
                  className="inline-flex size-9 shrink-0 items-center justify-center rounded-sm text-muted-foreground hover:text-destructive-text disabled:opacity-50"
                >
                  <Trash2 className="size-4" aria-hidden="true" />
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
