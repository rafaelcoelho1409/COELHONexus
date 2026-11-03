"use client";

/** The open book: header with its controls, the progress, the chapters, the page list and the reader. The snapshot
 * (`GET /bd/ingestion/{id}`) restores the picture after a reload; the event stream, which replays its history, fills
 * in the rest and keeps it live. */

import { useState } from "react";
import type { Snapshot } from "@/shared/api";
import { apiErrorMessage } from "@/shared/lib/api-error";
import { bookUnits, fmtBytes, isLive } from "../domain/books";
import { chapterTree } from "../domain/outline";
import { mergePages, shownPage } from "../domain/pages";
import { useCancelBook, useRunBook, useSnapshot } from "../hooks/use-bd-data";
import { useBookRun } from "../hooks/use-book-run";
import { useBdOverlays } from "../hooks/use-overlays";
import { ChapterTree } from "./chapter-tree";
import { PageList } from "./page-list";
import { PageReader } from "./page-reader";
import { RunProgress } from "./run-progress";
import { StatusChip } from "./status-chip";

const BUTTON =
  "min-h-9 rounded-sm border border-border px-3 text-sm text-foreground hover:border-neutral-8 disabled:opacity-50";

export function BookView({ bookId }: { bookId: string }) {
  const snapshot = useSnapshot(bookId);
  if (snapshot.isPending) {
    return (
      <p role="status" className="text-sm text-muted-foreground">
        Opening the book…
      </p>
    );
  }
  if (snapshot.isError) {
    return (
      <section role="alert" className="rounded-md border border-border bg-card p-4 text-sm">
        <p className="text-destructive-text">
          This book could not be opened — {apiErrorMessage(snapshot.error)}
        </p>
        <button type="button" onClick={() => void snapshot.refetch()} className={`mt-3 ${BUTTON}`}>
          Try again
        </button>
      </section>
    );
  }
  return <BookDetail bookId={bookId} snapshot={snapshot.data} />;
}

function BookDetail({ bookId, snapshot }: { bookId: string; snapshot: Snapshot }) {
  const run = useBookRun(bookId, snapshot);
  const runBook = useRunBook();
  const cancelBook = useCancelBook();
  const { notice } = useBdOverlays();
  const { manifest } = snapshot;
  const units = bookUnits(manifest.filename);
  const { state, status } = run;
  const live = isLive(status);

  const rows = mergePages(manifest.pages, state.pages);
  const extracted = new Set(rows.map((row) => row.page));
  const title = (state.title ?? manifest.title).trim() || manifest.filename;
  const nPages = state.nPages ?? manifest.n_pages;
  const total = Math.max(state.total, nPages);
  const completed = Math.max(state.completed, snapshot.progress?.completed ?? 0, rows.length);
  // The stream's `started` event is newer than the manifest; before it arrives (or when nothing is live) the manifest has them.
  const tree = chapterTree(
    state.chapters.length > 0 ? state.chapters : manifest.chapters,
    state.outline.length > 0 ? state.outline : manifest.outline,
  );

  // Follow the newest page while the run is live when the book is opened; a reader's own choice turns it off.
  const [view, setView] = useState<{ follow: boolean; picked: number | null }>({
    follow: live,
    picked: null,
  });
  const page = shownPage(view, state.newest, rows);
  const row = rows.find((candidate) => candidate.page === page);

  const start = (again: boolean) =>
    runBook.mutate(
      { bookId, refresh: again },
      {
        onSuccess: (result) => {
          run.restart();
          setView({ follow: true, picked: null });
          if (result.status === "locked")
            notice("This book is already being extracted — following the live run.");
          else if (result.status === "cached") notice("Already extracted.");
          else notice(result.resumed ? "Resuming the extraction where it stopped." : "Extraction queued.");
        },
        onError: (error) => notice(apiErrorMessage(error), "error"),
      },
    );

  const cancel = () =>
    cancelBook.mutate(bookId, {
      onSuccess: () => run.markCanceling(),
      onError: (error) => notice(apiErrorMessage(error), "error"),
    });

  const cancelling = state.canceling || cancelBook.isPending;

  return (
    <section aria-label={title} className="min-w-0">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="break-words text-xl font-semibold tracking-tight">{title}</h2>
          <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted-foreground">
            <StatusChip status={status} />
            <span className="break-all">{manifest.filename}</span>
            <span>{fmtBytes(manifest.size_bytes)}</span>
            <span className="tabular">
              {nPages > 0 ? `${nPages} ${units.plural}` : `${units.plural} not counted yet`}
            </span>
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {live ? (
            <button type="button" onClick={cancel} disabled={cancelling} className={BUTTON}>
              {cancelling ? "Cancelling…" : "Cancel"}
            </button>
          ) : status === "cancelled" || status === "failed" ? (
            <button
              type="button"
              onClick={() => start(false)}
              disabled={runBook.isPending}
              className={BUTTON}
            >
              {runBook.isPending ? "Starting…" : "Resume"}
            </button>
          ) : status === "done" ? (
            <button type="button" onClick={() => start(true)} disabled={runBook.isPending} className={BUTTON}>
              {runBook.isPending ? "Starting…" : "Extract again"}
            </button>
          ) : null}
        </div>
      </header>

      <div aria-live="polite" className="mt-3 text-sm">
        {cancelling && live ? (
          <p className="text-warning-text">
            {units.singular === "page"
              ? "Cancelling — the run stops between slices of 5 pages, which can take a moment."
              : "Cancelling — the current EPUB conversion finishes before stopping."}
          </p>
        ) : status === "queued" ? (
          <p className="text-muted-foreground">Queued — waiting for a worker to pick it up.</p>
        ) : null}
      </div>
      {run.error ? (
        <p role="alert" className="mt-2 break-words text-sm text-destructive-text">
          The extraction failed: {run.error}
        </p>
      ) : status === "failed" ? (
        <p role="alert" className="mt-2 text-sm text-destructive-text">
          The extraction failed.
        </p>
      ) : null}

      <RunProgress
        completed={completed}
        total={total}
        units={units}
        ratePerMinute={units.singular === "page" ? run.pagesPerMinute : null}
        streaming={run.streaming}
        streamStatus={run.streamStatus}
      />

      <div className="mt-5 grid grid-cols-1 gap-4 md:grid-cols-[15rem_minmax(0,1fr)]">
        <div className="flex min-w-0 flex-col gap-4">
          <ChapterTree
            tree={tree}
            activePage={page}
            unitLabel={units.singular}
            isExtracted={(candidate) => extracted.has(candidate)}
            onOpenPage={(target) => setView({ follow: false, picked: target })}
          />
          <PageList
            rows={rows}
            total={total}
            units={units}
            shown={page}
            following={view.follow}
            live={live}
            onFollow={(follow) => setView({ follow, picked: null })}
            onPick={(target) => setView({ follow: false, picked: target })}
          />
        </div>
        <PageReader
          bookId={bookId}
          page={page}
          row={row}
          livePage={page === null ? undefined : state.pages[page]}
          nPages={nPages}
          units={units}
        />
      </div>
    </section>
  );
}
