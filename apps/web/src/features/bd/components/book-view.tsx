"use client";

/** The open book: header with its controls, the progress, the chapters, the page list and the reader. The snapshot
 * (`GET /bd/ingestion/{id}`) restores the picture after a reload; the event stream, which replays its history, fills
 * in the rest and keeps it live. */

import {
  ChevronLeft,
  ChevronRight,
  Maximize2,
  PanelLeftClose,
  PanelLeftOpen,
  PanelRightClose,
  PanelRightOpen,
  Search,
  TriangleAlert,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { flushSync } from "react-dom";
import { apiErrorMessage } from "@/shared/lib/api-error";
import { useScrollSpy } from "@/shared/lib/use-scroll-spy";
import { bookUnits, fmtBytes, isLive } from "../domain/books";
import { chapterTree } from "../domain/outline";
import { mergePages, neighbourReviewPage, pageFlags, shownPage } from "../domain/pages";
import type { BookSnapshot } from "../entities";
import { readRememberedPage, rememberPage } from "../hooks/remembered-page";
import { useAgenticRun } from "../hooks/use-agentic-run";
import {
  useCancelAgenticRun,
  useCancelBook,
  useResumeAgenticRun,
  useRunBook,
  useSnapshot,
  useStartAgenticRun,
  useWipeAgenticRun,
  useWipeExtraction,
} from "../hooks/use-bd-data";
import { useBookRun } from "../hooks/use-book-run";
import { useBdOverlays } from "../hooks/use-overlays";
import { usePageContent } from "../hooks/use-page-content";
import { useReaderLayout } from "../hooks/use-reader-layout";
import { ActionButton } from "./action-button";
import { AgenticPipelineOverview, AgenticProgressBar, AgenticUsageSheet } from "./agentic-pipeline";
import { BookReadingMode } from "./book-reading-mode";
import { BookReadingProgress } from "./book-reading-progress";
import { BookSearch } from "./book-search";
import { ChapterTree } from "./chapter-tree";
import { PageList } from "./page-list";
import { PageOutline } from "./page-outline";
import { PageReader } from "./page-reader";
import { RunProgress } from "./run-progress";
import { StatusChip } from "./status-chip";

/** The page's grid from 1024px up for each way of folding the two side columns (whole class names, so Tailwind sees them). */
const COLUMNS = {
  both: "lg:grid-cols-[16rem_minmax(0,1fr)_14rem]",
  contentsOnly: "lg:grid-cols-[16rem_minmax(0,1fr)]",
  outlineOnly: "lg:grid-cols-[minmax(0,1fr)_14rem]",
  neither: "lg:grid-cols-[minmax(0,1fr)]",
} as const;

/** A column that stays in view while the page scrolls, and scrolls on its own when it is taller than the screen. */
const STICKY_COLUMN = "lg:sticky lg:top-20 lg:max-h-[calc(100dvh-6rem)] lg:self-start lg:overflow-y-auto";

export function BookView({ bookId, autoChain }: { bookId: string; autoChain: boolean }) {
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
        <div className="mt-3">
          <ActionButton onClick={() => void snapshot.refetch()}>Try again</ActionButton>
        </div>
      </section>
    );
  }
  return <BookDetail bookId={bookId} snapshot={snapshot.data} autoChain={autoChain} />;
}

function BookDetail({
  bookId,
  snapshot,
  autoChain,
}: {
  bookId: string;
  snapshot: BookSnapshot;
  autoChain: boolean;
}) {
  const run = useBookRun(bookId, snapshot);
  const agentic = useAgenticRun(bookId, snapshot);
  const runBook = useRunBook();
  const cancelBook = useCancelBook();
  const cancelAgentic = useCancelAgenticRun();
  const startAgentic = useStartAgenticRun();
  const resumeAgentic = useResumeAgenticRun();
  const wipeAgentic = useWipeAgenticRun();
  const wipeExtraction = useWipeExtraction();
  const { confirm, notice } = useBdOverlays();
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
  const [view, setView] = useState<{ follow: boolean; picked: number | null }>(() => {
    const remembered = live ? null : readRememberedPage(bookId, manifest.n_pages);
    return {
      follow: live,
      picked:
        remembered !== null && rows.some((candidate) => candidate.page === remembered) ? remembered : null,
    };
  });
  const page = shownPage(view, state.newest, rows);
  const row = rows.find((candidate) => candidate.page === page);
  const { layout, update: updateLayout } = useReaderLayout();
  // The reading window: null while closed, else the heading the reader was at on the page.
  const [reading, setReading] = useState<{ startId: string | null } | null>(null);
  const [searchOpen, setSearchOpen] = useState(false);
  const [usageOpen, setUsageOpen] = useState(false);
  const [activeView, setActiveView] = useState<"pipeline" | "materials">("pipeline");
  const currentIndex = rows.findIndex((candidate) => candidate.page === page);
  const previousPage = currentIndex > 0 ? (rows[currentIndex - 1]?.page ?? null) : null;
  const nextPage = currentIndex >= 0 ? (rows[currentIndex + 1]?.page ?? null) : null;

  // The page's text and everything drawn from it is read once here: the article, the outline column and the reading
  // window all show the same copy. The outline watches the headings of whichever of them is on screen.
  const content = usePageContent({
    bookId,
    page,
    row,
    livePage: page === null ? undefined : state.pages[page],
    agenticRunAvailable: agentic.run !== null,
  });
  const activeHeading = useScrollSpy(content.outlineIds, `${reading !== null}:${page}:${content.version}`);
  const hasOutline = content.outline.length >= 2;
  const flags = pageFlags(agentic.run);
  const reviewPages = agentic.run?.review_pages ?? [];
  const nextReview = neighbourReviewPage(reviewPages, page, 1);

  // After a search jumps to a heading on another page, scroll there once that page has rendered.
  const pendingHeading = useRef<string | null>(null);
  useEffect(() => {
    const id = pendingHeading.current;
    if (id === null || page === null || content.source === "") return;
    const element = document.getElementById(id);
    if (!element) return;
    pendingHeading.current = null;
    element.scrollIntoView({ block: "start" });
  }, [page, content.source]);

  useEffect(() => {
    if (rows.length === 0) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setSearchOpen(true);
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [rows.length]);

  const start = () =>
    runBook.mutate(
      { bookId, autoChain },
      {
        onSuccess: (result) => {
          setActiveView("pipeline");
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
  const agenticActionPending = startAgentic.isPending || resumeAgentic.isPending;
  const agenticCancelPending = cancelAgentic.isPending;
  const agenticWipePending = wipeAgentic.isPending;
  const startAgenticProcessing = () =>
    startAgentic.mutate(bookId, {
      onSuccess: () => {
        setActiveView("pipeline");
        notice("Agentic ingestion queued.");
      },
      onError: (error) => notice(apiErrorMessage(error), "error"),
    });
  const retryAgenticProcessing = () => {
    if (!agentic.run) return;
    resumeAgentic.mutate(
      { bookId, agenticRunId: agentic.run.agentic_run_id },
      {
        onSuccess: () => {
          setActiveView("pipeline");
          agentic.restart();
          notice("Agentic ingestion retry queued.");
        },
        onError: (error) => notice(apiErrorMessage(error), "error"),
      },
    );
  };
  const stopAgenticProcessing = () => {
    if (!agentic.run) return;
    cancelAgentic.mutate(
      { bookId, agenticRunId: agentic.run.agentic_run_id },
      {
        onSuccess: (result) => {
          if (result.status === "cancelling") {
            agentic.markCanceling();
            notice("Agentic AI stop requested; active page work is draining.");
          } else {
            notice(`Agentic AI run is already ${result.status}.`);
          }
        },
        onError: (error) => notice(apiErrorMessage(error), "error"),
      },
    );
  };
  const wipeAgenticProcessing = async () => {
    if (!agentic.run) return;
    const confirmed = await confirm({
      title: "Wipe Agentic AI run?",
      message:
        "Deletes this run’s processed pages, assessments, proposals, usage counters, and checkpoints. The uploaded book and Docling extraction are preserved. This cannot be undone.",
      confirmLabel: "Wipe",
    });
    if (!confirmed) return;
    wipeAgentic.mutate(
      { bookId, agenticRunId: agentic.run.agentic_run_id },
      {
        onSuccess: () => notice("Agentic AI artifacts wiped. Docling extraction is unchanged."),
        onError: (error) => notice(apiErrorMessage(error), "error"),
      },
    );
  };
  const wipeDoclingExtraction = async () => {
    const confirmed = await confirm({
      title: "Wipe extracted materials?",
      message:
        "Deletes Docling pages, figures, and all Agentic AI results derived from them. The uploaded book stays in your library so you can extract it again. This cannot be undone.",
      confirmLabel: "Wipe",
    });
    if (!confirmed) return;
    wipeExtraction.mutate(bookId, {
      onSuccess: () => {
        run.clear();
        setView({ follow: false, picked: null });
        setActiveView("materials");
        notice("Extracted materials wiped. The uploaded book is preserved.");
      },
      onError: (error) => notice(apiErrorMessage(error), "error"),
    });
  };
  const choosePage = (target: number) => {
    rememberPage(bookId, target);
    setView({ follow: false, picked: target });
  };
  const openReadingMode = () => {
    if (page === null) return;
    choosePage(page);
    setReading({ startId: activeHeading });
  };
  // Leaving the reading window lands the page on the heading the reader reached in it (the page's text is drawn again first).
  const exitReading = () => {
    flushSync(() => setReading(null));
    if (activeHeading) document.getElementById(activeHeading)?.scrollIntoView?.({ block: "start" });
  };
  const navigation = (
    <>
      <div className="rounded-md border border-border bg-card p-3">
        <ChapterTree
          tree={tree}
          activePage={page}
          unitLabel={units.singular}
          isExtracted={(candidate) => extracted.has(candidate)}
          onOpenPage={choosePage}
        />
      </div>
      <div className="rounded-md border border-border bg-card p-3">
        <PageList
          rows={rows}
          total={total}
          units={units}
          shown={page}
          following={view.follow}
          live={live}
          flags={flags}
          onFollow={(follow) => setView({ follow, picked: null })}
          onPick={choosePage}
        />
      </div>
    </>
  );
  const position = `${page ?? 0} / ${nPages > 0 ? nPages : total || rows.length}`;
  const unitLabel = `${units.singular[0]?.toUpperCase()}${units.singular.slice(1)}`;
  const outlineView = hasOutline ? <PageOutline entries={content.outline} activeId={activeHeading} /> : null;
  const showOutlineColumn = layout.outline && hasOutline;
  const columns = layout.contents
    ? showOutlineColumn
      ? COLUMNS.both
      : COLUMNS.contentsOnly
    : showOutlineColumn
      ? COLUMNS.outlineOnly
      : COLUMNS.neither;

  // On the right of the page's header: step through the book, fold either side column, or read on the whole screen.
  const toolbar = (
    <div className="flex shrink-0 flex-wrap items-center justify-end gap-1">
      <ActionButton
        iconOnly
        aria-label={`Previous ${units.singular}`}
        title={`Previous ${units.singular}`}
        disabled={previousPage === null}
        onClick={() => previousPage !== null && choosePage(previousPage)}
      >
        <ChevronLeft aria-hidden />
      </ActionButton>
      <ActionButton
        iconOnly
        aria-label={`Next ${units.singular}`}
        title={`Next ${units.singular}`}
        disabled={nextPage === null}
        onClick={() => nextPage !== null && choosePage(nextPage)}
      >
        <ChevronRight aria-hidden />
      </ActionButton>
      {reviewPages.length > 0 ? (
        <ActionButton
          aria-label={`Next to review: the next ${units.singular} that needs review`}
          title={`Jump to the next ${units.singular} that needs review`}
          disabled={nextReview === null}
          onClick={() => nextReview !== null && choosePage(nextReview)}
        >
          <TriangleAlert aria-hidden />
          <span className="hidden sm:inline">Next to review</span>
        </ActionButton>
      ) : null}
      {layout.contents ? null : (
        <ActionButton
          iconOnly
          aria-label="Search this book"
          title="Search this book"
          onClick={() => setSearchOpen(true)}
        >
          <Search aria-hidden />
        </ActionButton>
      )}
      <ActionButton
        iconOnly
        aria-expanded={layout.contents}
        aria-label={layout.contents ? "Hide contents" : "Show contents"}
        title={layout.contents ? "Hide contents" : "Show contents"}
        onClick={() => updateLayout({ contents: !layout.contents })}
      >
        {layout.contents ? <PanelLeftClose aria-hidden /> : <PanelLeftOpen aria-hidden />}
      </ActionButton>
      {hasOutline ? (
        <ActionButton
          iconOnly
          className="hidden lg:inline-flex"
          aria-expanded={layout.outline}
          aria-label={layout.outline ? "Hide outline" : "Show outline"}
          title={layout.outline ? "Hide outline" : "Show outline"}
          onClick={() => updateLayout({ outline: !layout.outline })}
        >
          {layout.outline ? <PanelRightClose aria-hidden /> : <PanelRightOpen aria-hidden />}
        </ActionButton>
      ) : null}
      <ActionButton
        aria-haspopup="dialog"
        title={`Read this ${units.singular} on the whole screen`}
        onClick={openReadingMode}
        disabled={page === null}
      >
        <Maximize2 aria-hidden />
        Reading mode
      </ActionButton>
    </div>
  );

  return (
    <section aria-label={title} className="min-w-0">
      <BookReadingProgress />
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
          {status === "cancelled" || status === "failed" ? (
            <ActionButton onClick={start} disabled={runBook.isPending}>
              {runBook.isPending ? "Starting…" : "Resume"}
            </ActionButton>
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

      {agentic.run ? (
        <AgenticUsageSheet
          open={usageOpen}
          bookId={bookId}
          run={agentic.run}
          live={agentic.streaming}
          onClose={() => setUsageOpen(false)}
        />
      ) : null}
      <div role="tablist" aria-label="Book views" className="mt-5 border-b border-border">
        {(
          [
            ["pipeline", "Pipeline"],
            ["materials", "Materials"],
          ] as const
        ).map(([viewId, label]) => (
          <button
            key={viewId}
            id={`bd-${viewId}-tab`}
            type="button"
            role="tab"
            aria-selected={activeView === viewId}
            aria-controls={`bd-${viewId}-panel`}
            tabIndex={activeView === viewId ? 0 : -1}
            onClick={() => setActiveView(viewId)}
            onKeyDown={(event) => {
              if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
                event.preventDefault();
                const nextView = viewId === "pipeline" ? "materials" : "pipeline";
                setActiveView(nextView);
                document.getElementById(`bd-${nextView}-tab`)?.focus();
              }
            }}
            className={`-mb-px border-b-2 px-3 py-2 text-sm ${
              activeView === viewId
                ? "border-primary font-medium text-primary-text"
                : "border-transparent text-muted-foreground hover:text-foreground"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      <div
        role="tabpanel"
        id="bd-pipeline-panel"
        aria-labelledby="bd-pipeline-tab"
        hidden={activeView !== "pipeline"}
        className="mt-4"
      >
        <div className="mb-4 grid grid-cols-1 items-stretch gap-3 lg:grid-cols-2">
          <section
            aria-label="Docling extraction progress"
            className="min-w-0 rounded-md border border-border bg-card p-3"
          >
            <RunProgress
              completed={completed}
              total={total}
              units={units}
              ratePerMinute={units.singular === "page" ? run.pagesPerMinute : null}
              streaming={run.streaming}
              streamStatus={run.streamStatus}
              statusText={
                status === "extracting"
                  ? "Processing"
                  : status === "queued"
                    ? "Queued"
                    : status === "done"
                      ? "Complete"
                      : status === "failed"
                        ? "Failed"
                        : status === "cancelled"
                          ? "Cancelled"
                          : "Waiting"
              }
              successful={status === "done" && run.error === null}
              action={
                <div className="flex flex-wrap items-center justify-end gap-1.5">
                  <ActionButton
                    onClick={start}
                    disabled={live || runBook.isPending || status === "done"}
                    title={
                      status === "done"
                        ? "Use Refresh in the library menu to extract this book again."
                        : live
                          ? "Docling extraction is already running."
                          : undefined
                    }
                    aria-busy={runBook.isPending}
                    aria-label={
                      status === "failed" || status === "cancelled"
                        ? "Resume Docling extraction"
                        : "Start Docling extraction"
                    }
                  >
                    {runBook.isPending
                      ? "Starting…"
                      : status === "failed" || status === "cancelled"
                        ? "Resume"
                        : "Start"}
                  </ActionButton>
                  <ActionButton
                    onClick={cancel}
                    disabled={!live || cancelling}
                    aria-busy={cancelling}
                    aria-label="Stop Docling extraction"
                    title={live ? undefined : "There is no active Docling extraction to stop."}
                  >
                    {cancelling ? "Stopping…" : "Stop"}
                  </ActionButton>
                  <ActionButton
                    onClick={() => void wipeDoclingExtraction()}
                    disabled={
                      live ||
                      agentic.status === "queued" ||
                      agentic.status === "running" ||
                      wipeExtraction.isPending
                    }
                    aria-busy={wipeExtraction.isPending}
                    aria-label="Wipe Docling extraction"
                    title={
                      live || agentic.status === "queued" || agentic.status === "running"
                        ? "Stop both pipelines and wait for them to finish before wiping."
                        : undefined
                    }
                  >
                    {wipeExtraction.isPending ? "Wiping…" : "Wipe"}
                  </ActionButton>
                </div>
              }
              className="mt-0"
            />
            <p className="mt-2 text-xs text-muted-foreground">
              Wipe clears extracted materials and derived AI results but keeps the uploaded book.
            </p>
          </section>
          <section
            aria-label="Agentic AI progress"
            className="min-w-0 rounded-md border border-border bg-card p-3"
          >
            <AgenticProgressBar
              status={agentic.status}
              progress={agentic.progress}
              total={nPages}
              error={agentic.error}
              units={units}
              extractionDone={status === "done"}
              successful={agentic.status === "done"}
              qualityStatus={agentic.run?.quality_status ?? null}
              deliveredPageCount={agentic.run?.delivered_page_count ?? 0}
              discardedBlankPageCount={agentic.run?.discarded_blank_page_count ?? 0}
              reviewPageCount={agentic.run?.review_page_count ?? 0}
              unverifiedPageCount={agentic.run?.unverified_page_count ?? 0}
              streamStatus={agentic.streamStatus}
              actionPending={agenticActionPending}
              cancelPending={agenticCancelPending}
              wipePending={agenticWipePending}
              hasRun={agentic.run !== null}
              cancelRequested={agentic.cancelRequested}
              onStart={startAgenticProcessing}
              onResume={retryAgenticProcessing}
              onStop={stopAgenticProcessing}
              onWipe={() => void wipeAgenticProcessing()}
              chapters={state.chapters.length > 0 ? state.chapters : manifest.chapters}
            />
          </section>
        </div>
        <AgenticPipelineOverview
          bookId={bookId}
          run={agentic.run}
          status={agentic.status}
          progress={agentic.progress}
          error={agentic.error}
          units={units}
          chapters={state.chapters.length > 0 ? state.chapters : manifest.chapters}
          onOpenUsage={() => setUsageOpen(true)}
        />
      </div>
      <div
        role="tabpanel"
        id="bd-materials-panel"
        aria-labelledby="bd-materials-tab"
        hidden={activeView !== "materials"}
        className="mt-4"
      >
        {!reading ? (
          <div className={`flex flex-col gap-4 lg:grid ${columns}`}>
            {layout.contents ? (
              <aside className={`flex min-w-0 flex-col gap-3 ${STICKY_COLUMN}`}>
                <ActionButton
                  className="w-full justify-start"
                  onClick={() => setSearchOpen(true)}
                  aria-haspopup="dialog"
                  disabled={rows.length === 0}
                >
                  <Search aria-hidden />
                  Search book
                  <kbd className="ml-auto font-mono text-xs text-muted-foreground">Ctrl K</kbd>
                </ActionButton>
                {navigation}
              </aside>
            ) : null}

            {/* In the reading window the text is drawn there, not here: one copy at a time, so a heading's id is never twice on the page. */}
            <PageReader
              bookId={bookId}
              page={page}
              row={row}
              nPages={nPages}
              units={units}
              content={content}
              toolbar={toolbar}
            />

            {showOutlineColumn ? (
              <div className="hidden lg:block">
                <div className={STICKY_COLUMN}>{outlineView}</div>
              </div>
            ) : null}
          </div>
        ) : page !== null ? (
          <BookReadingMode
            title={title}
            unitLabel={unitLabel}
            position={position}
            page={page}
            previousPage={previousPage}
            nextPage={nextPage}
            navigation={navigation}
            outline={outlineView}
            layout={layout}
            onLayout={updateLayout}
            startId={reading.startId}
            onPage={choosePage}
            onSearch={() => setSearchOpen(true)}
            onExit={exitReading}
          >
            <PageReader
              bookId={bookId}
              page={page}
              row={row}
              nPages={nPages}
              units={units}
              content={content}
              framed={false}
            />
          </BookReadingMode>
        ) : null}

        {searchOpen ? (
          <BookSearch
            bookId={bookId}
            unitLabel={unitLabel}
            rows={rows}
            onChoose={(hit) => {
              pendingHeading.current = hit.headingId ?? null;
              choosePage(hit.page);
              setSearchOpen(false);
            }}
            onClose={() => setSearchOpen(false)}
          />
        ) : null}
      </div>
    </section>
  );
}
