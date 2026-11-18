"use client";

/** The open book: header with its controls, the progress, the chapters, the page list and the reader. The snapshot
 * (`GET /bd/ingestion/{id}`) restores the picture after a reload; the event stream, which replays its history, fills
 * in the rest and keeps it live. */

import {
  BookOpen,
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
import { createPortal, flushSync } from "react-dom";
import { apiErrorMessage } from "@/shared/lib/api-error";
import { useScrollSpy } from "@/shared/lib/use-scroll-spy";
import { bookUnits, fmtBytes, isLive } from "../domain/books";
import { chapterTree } from "../domain/outline";
import { mergePages, neighbourReviewPage, pageFlags } from "../domain/pages";
import type { BookSnapshot } from "../entities";
import { readRememberedPage, rememberPage } from "../hooks/remembered-page";
import { useAgenticRun } from "../hooks/use-agentic-run";
import {
  useCancelAgenticRun,
  useCancelBook,
  useContentSelection,
  useProcessedBook,
  useReplayAgenticRunNode,
  useResumeAgenticRun,
  useRunBook,
  useSnapshot,
  useStartAgenticRun,
  useWipeAgenticRun,
  useWipeExtraction,
} from "../hooks/use-bd-data";
import { useBookRun } from "../hooks/use-book-run";
import { useBdOverlays } from "../hooks/use-overlays";
import { type PageVersion, usePageContent } from "../hooks/use-page-content";
import { useReaderLayout } from "../hooks/use-reader-layout";
import { type BookViewId, CONTENTS_ENTRIES_PER_PAGE } from "../params";
import { ActionButton } from "./action-button";
import {
  AgenticPipelineOverview,
  AgenticPipelineUsage,
  AgenticProgressBar,
  AgenticUsageSheet,
} from "./agentic-pipeline";
import { BookReadingMode } from "./book-reading-mode";
import { BookReadingProgress } from "./book-reading-progress";
import { BookSearch } from "./book-search";
import { ChapterTree } from "./chapter-tree";
import { GeneratedContentsPage } from "./generated-contents-page";
import { PageList } from "./page-list";
import { PageOutline } from "./page-outline";
import { PageReader } from "./page-reader";
import { RetrievalIndexPanel } from "./retrieval-index-panel";
import { RetrievalIndexProgress } from "./retrieval-index-progress";
import { RunProgress } from "./run-progress";
import { StatusChip } from "./status-chip";
import { ToggleGroup } from "./toggle-group";

/** The page's grid from 1024px up for each way of folding the two side columns (whole class names, so Tailwind sees them). */
const COLUMNS = {
  both: "lg:grid-cols-[16rem_minmax(0,1fr)_14rem]",
  contentsOnly: "lg:grid-cols-[16rem_minmax(0,1fr)]",
  outlineOnly: "lg:grid-cols-[minmax(0,1fr)_14rem]",
  neither: "lg:grid-cols-[minmax(0,1fr)]",
} as const;

/** A column that stays in view while the page scrolls, and scrolls on its own when it is taller than the screen. */
const STICKY_COLUMN =
  "lg:sticky lg:top-[calc(var(--chrome-top)+1.5rem)] lg:max-h-[calc(100dvh-var(--chrome-top)-2.5rem)] lg:self-start lg:overflow-y-auto";
const PAGES_NAV_OPTIONS = [
  { id: "contents", label: "Contents" },
  { id: "pages", label: "Processed pages" },
] as const;
const CONTENT_VIEW_OPTIONS = [
  { id: "rendered", label: "Rendered" },
  { id: "source", label: "Markdown source" },
] as const;
const CONTENT_VERSION_OPTIONS: readonly { id: PageVersion; label: string }[] = [
  { id: "ai", label: "AI-processed" },
  { id: "docling", label: "Docling raw" },
  { id: "changes", label: "Changes" },
];

type ReaderPosition = { kind: "contents"; index: number } | { kind: "page"; page: number };

function sameReaderPosition(left: ReaderPosition, right: ReaderPosition): boolean {
  if (left.kind !== right.kind) return false;
  if (left.kind === "contents") return right.kind === "contents" && left.index === right.index;
  return right.kind === "page" && left.page === right.page;
}

export function BookView({
  bookId,
  initialPage,
  autoChain,
  activeView,
  pageControlsHost,
}: {
  bookId: string;
  initialPage: number | null;
  autoChain: boolean;
  activeView: BookViewId;
  pageControlsHost: HTMLDivElement | null;
}) {
  const snapshot = useSnapshot(bookId);
  if (snapshot.isPending && !snapshot.data) {
    return (
      <p
        role="status"
        className={
          activeView === "materials"
            ? "flex min-h-0 flex-1 items-center justify-center text-sm text-muted-foreground"
            : "text-sm text-muted-foreground"
        }
      >
        Opening the book…
      </p>
    );
  }
  if (!snapshot.data) {
    return (
      <section
        role="status"
        aria-live="polite"
        className={
          activeView === "materials"
            ? "flex min-h-0 flex-1 flex-col justify-center rounded-md border border-border bg-card p-4 text-sm"
            : "rounded-md border border-border bg-card p-4 text-sm"
        }
      >
        <p className="font-medium text-foreground">The book could not be loaded right now.</p>
        <p className="mt-1 text-muted-foreground">
          Its saved data may be temporarily unavailable while an Agentic AI run rebuilds it. The run can
          continue; retry to load the latest book state.
        </p>
        <p className="mt-1 text-muted-foreground">{apiErrorMessage(snapshot.error)}</p>
        <div className="mt-3">
          <ActionButton
            onClick={() => void snapshot.refetch()}
            disabled={snapshot.isFetching}
            aria-busy={snapshot.isFetching}
          >
            {snapshot.isFetching ? "Retrying…" : "Try again"}
          </ActionButton>
        </div>
      </section>
    );
  }
  return (
    <>
      {snapshot.isError ? (
        <div
          role="status"
          className="mb-3 flex flex-wrap items-center justify-between gap-2 rounded-md border border-border bg-card px-3 py-2 text-sm"
        >
          <span className="text-muted-foreground">
            Could not refresh this book. Showing the last loaded state. {apiErrorMessage(snapshot.error)}
          </span>
          <ActionButton
            onClick={() => void snapshot.refetch()}
            disabled={snapshot.isFetching}
            aria-busy={snapshot.isFetching}
          >
            {snapshot.isFetching ? "Retrying…" : "Try again"}
          </ActionButton>
        </div>
      ) : null}
      <BookDetail
        bookId={bookId}
        initialPage={initialPage}
        snapshot={snapshot.data}
        autoChain={autoChain}
        activeView={activeView}
        pageControlsHost={pageControlsHost}
      />
    </>
  );
}

function BookDetail({
  bookId,
  initialPage,
  snapshot,
  autoChain,
  activeView,
  pageControlsHost,
}: {
  bookId: string;
  initialPage: number | null;
  snapshot: BookSnapshot;
  autoChain: boolean;
  activeView: BookViewId;
  pageControlsHost: HTMLDivElement | null;
}) {
  const run = useBookRun(bookId, snapshot);
  const agentic = useAgenticRun(bookId, snapshot);
  const contentSelection = useContentSelection(
    bookId,
    agentic.status === "done" ? (agentic.run?.agentic_run_id ?? null) : null,
  );
  const processedBook = useProcessedBook(
    bookId,
    agentic.status === "done" ? (agentic.run?.agentic_run_id ?? null) : null,
    activeView === "materials",
  );
  const runBook = useRunBook();
  const cancelBook = useCancelBook();
  const cancelAgentic = useCancelAgenticRun();
  const startAgentic = useStartAgenticRun();
  const resumeAgentic = useResumeAgenticRun();
  const replayAgenticNode = useReplayAgenticRunNode();
  const wipeAgentic = useWipeAgenticRun();
  const wipeExtraction = useWipeExtraction();
  const { confirm, notice } = useBdOverlays();
  const { manifest } = snapshot;
  const units = bookUnits(manifest.filename);
  const { state, status } = run;
  const live = isLive(status);

  const allRows = mergePages(manifest.pages, state.pages);
  const flags = pageFlags(agentic.run);
  const includedSourcePages = new Set(
    contentSelection.data?.pages
      .filter((item) => item.disposition === "include")
      .map((item) => item.page_number) ?? [],
  );
  const sourceRowsByPage = new Map(allRows.map((row) => [row.page, row]));
  const rows =
    activeView === "materials" && agentic.status === "done"
      ? processedBook.data
        ? processedBook.data.pages
            .map((item) => sourceRowsByPage.get(item.source_page_number))
            .filter((row): row is NonNullable<typeof row> => row !== undefined)
        : contentSelection.data
          ? allRows.filter((row) => includedSourcePages.has(row.page) && flags.get(row.page) !== "blank")
          : allRows
      : allRows;
  const processedNumbers = new Map(
    processedBook.data
      ? processedBook.data.pages.map((item) => [item.source_page_number, item.processed_page_number] as const)
      : activeView === "materials" && agentic.status === "done"
        ? rows.map((row, index) => [row.page, index + 1] as const)
        : [],
  );
  const contentsEntries =
    activeView === "materials" && agentic.status === "done" ? (processedBook.data?.contents ?? []) : [];
  const contentsPageCount = Math.ceil(contentsEntries.length / CONTENTS_ENTRIES_PER_PAGE);
  const readerPositions: ReaderPosition[] = [
    ...Array.from({ length: contentsPageCount }, (_, index) => ({ kind: "contents" as const, index })),
    ...rows.map((item) => ({ kind: "page" as const, page: item.page })),
  ];
  const extracted = new Set(rows.map((row) => row.page));
  const title = (state.title ?? manifest.title).trim() || manifest.filename;
  const nPages = state.nPages ?? manifest.n_pages;
  const total = Math.max(state.total, nPages);
  const completed = Math.max(state.completed, snapshot.progress?.completed ?? 0, rows.length);
  // The stream's `started` event is newer than the manifest; before it arrives (or when nothing is live) the manifest has them.
  const originalTree = chapterTree(
    state.chapters.length > 0 ? state.chapters : manifest.chapters,
    state.outline.length > 0 ? state.outline : manifest.outline,
  );
  const processedOutline =
    processedBook.data?.contents.map((entry) => ({
      level: entry.level,
      title: entry.title,
      page: entry.source_page_number,
    })) ?? [];
  const processedChapters = processedOutline
    .filter((entry) => entry.level === 1)
    .map((entry, index, chapters) => {
      const nextStartPage = chapters[index + 1]?.page;
      const lastSourcePage = processedBook.data?.pages.at(-1)?.source_page_number ?? entry.page;
      return {
        title: entry.title,
        first_page: entry.page,
        last_page: Math.max(entry.page, (nextStartPage ?? lastSourcePage + 1) - 1),
      };
    });
  const tree =
    activeView === "materials" && agentic.status === "done"
      ? processedBook.data
        ? chapterTree(processedChapters, processedOutline)
        : []
      : originalTree;

  // Follow the newest page while the run is live when the book is opened; a reader's own choice turns it off.
  const [view, setView] = useState<{ follow: boolean; picked: ReaderPosition | null }>(() => {
    const remembered = live ? null : readRememberedPage(bookId, manifest.n_pages);
    const cited =
      initialPage !== null && rows.some((candidate) => candidate.page === initialPage) ? initialPage : null;
    const rememberedPage =
      remembered !== null && rows.some((candidate) => candidate.page === remembered) ? remembered : null;
    const sourcePage = cited ?? rememberedPage;
    return {
      follow: live && sourcePage === null,
      picked:
        sourcePage !== null
          ? { kind: "page", page: sourcePage }
          : activeView === "materials" && !live
            ? { kind: "contents", index: 0 }
            : null,
    };
  });
  const requestedPosition = view.follow ? null : view.picked;
  const fallbackSourcePage = rows.at(-1)?.page ?? state.newest;
  const fallbackPosition: ReaderPosition | null = view.follow
    ? fallbackSourcePage === null
      ? null
      : { kind: "page", page: fallbackSourcePage }
    : (readerPositions[0] ?? null);
  const requestedPositionAvailable =
    requestedPosition !== null &&
    readerPositions.some((candidate) => sameReaderPosition(candidate, requestedPosition));
  const pendingContentsPosition =
    activeView === "materials" &&
    agentic.status === "done" &&
    processedBook.isPending &&
    requestedPosition?.kind === "contents"
      ? requestedPosition
      : null;
  const currentPosition = requestedPositionAvailable
    ? requestedPosition
    : (pendingContentsPosition ?? fallbackPosition);
  const page = currentPosition?.kind === "page" ? currentPosition.page : null;
  const contentsPageIndex = currentPosition?.kind === "contents" ? currentPosition.index : null;
  const showingContents = contentsPageIndex !== null;
  const row = rows.find((candidate) => candidate.page === page);
  const { layout, update: updateLayout } = useReaderLayout();
  // The reading window: null while closed, else the heading the reader was at on the page.
  const [reading, setReading] = useState<{ startId: string | null } | null>(() =>
    activeView === "materials" ? { startId: null } : null,
  );
  const [pagesNavigation, setPagesNavigation] = useState<"contents" | "pages">("contents");
  const [previewMode, setPreviewMode] = useState<(typeof CONTENT_VIEW_OPTIONS)[number]["id"]>("rendered");
  const [searchOpen, setSearchOpen] = useState(false);
  const [usageOpen, setUsageOpen] = useState(false);
  const [replayPendingNodeId, setReplayPendingNodeId] = useState<string | null>(null);
  const [replayError, setReplayError] = useState<string | null>(null);
  const currentIndex = readerPositions.findIndex(
    (candidate) => currentPosition !== null && sameReaderPosition(candidate, currentPosition),
  );
  const previousPosition = currentIndex > 0 ? (readerPositions[currentIndex - 1] ?? null) : null;
  const nextPosition =
    currentIndex >= 0 ? (readerPositions[currentIndex + 1] ?? null) : (readerPositions[0] ?? null);

  // The page's text and everything drawn from it is read once here: the article, the outline column and the reading
  // window all show the same copy. The outline watches the headings of whichever of them is on screen.
  const content = usePageContent({
    bookId,
    page,
    row,
    livePage: page === null ? undefined : state.pages[page],
    agenticRunAvailable: agentic.run !== null,
  });
  const pageViewControls =
    activeView === "materials" && pageControlsHost
      ? createPortal(
          <div className="flex min-w-0 flex-wrap items-center gap-1.5">
            <ToggleGroup
              legend="Content view"
              options={CONTENT_VIEW_OPTIONS}
              value={previewMode}
              onChange={setPreviewMode}
            />
            {agentic.status === "done" || content.hasAi ? (
              <ToggleGroup
                legend="Version of this page"
                options={CONTENT_VERSION_OPTIONS}
                value={content.version}
                onChange={content.setVersion}
              />
            ) : null}
          </div>,
          pageControlsHost,
        )
      : null;
  const activeHeading = useScrollSpy(content.outlineIds, `${reading !== null}:${page}:${content.version}`);
  const hasOutline = content.outline.length >= 2;
  const reviewPages = agentic.run?.review_pages ?? [];
  const nextReview = neighbourReviewPage(
    reviewPages.filter((candidate) => rows.some((item) => item.page === candidate)),
    page,
    1,
  );

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
          agentic.restart();
          notice("Agentic ingestion retry queued.");
        },
        onError: (error) => notice(apiErrorMessage(error), "error"),
      },
    );
  };
  const replayAgenticFromNode = (nodeId: string) => {
    const agenticRunId = agentic.run?.agentic_run_id;
    if (
      !agenticRunId ||
      replayAgenticNode.isPending ||
      agentic.status === "queued" ||
      agentic.status === "running"
    ) {
      return;
    }
    setReplayError(null);
    setReplayPendingNodeId(nodeId);
    replayAgenticNode.mutate(
      { bookId, agenticRunId, nodeId },
      {
        onSuccess: () => {
          setReplayPendingNodeId(null);
          agentic.restart();
          notice(`Replay queued from ${nodeId}.`);
        },
        onError: (error) => {
          setReplayPendingNodeId(null);
          const message = apiErrorMessage(error);
          setReplayError(`Replay from ${nodeId} failed — ${message}`);
          notice(message, "error");
        },
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
        notice("Extracted materials wiped. The uploaded book is preserved.");
      },
      onError: (error) => notice(apiErrorMessage(error), "error"),
    });
  };
  const choosePosition = (target: ReaderPosition) => {
    if (target.kind === "page") rememberPage(bookId, target.page);
    setView({ follow: false, picked: target });
  };
  const choosePage = (target: number) => choosePosition({ kind: "page", page: target });
  const goToPreviousPosition = () => {
    if (previousPosition !== null) choosePosition(previousPosition);
  };
  const goToNextPosition = () => {
    if (nextPosition !== null) choosePosition(nextPosition);
  };
  const pageNavigationToolbar = (
    <div className="flex shrink-0 items-center gap-1">
      <ActionButton
        iconOnly
        aria-label={`Previous ${units.singular}`}
        title={`Previous ${units.singular}`}
        disabled={previousPosition === null}
        onClick={goToPreviousPosition}
      >
        <ChevronLeft aria-hidden />
      </ActionButton>
      <ActionButton
        iconOnly
        aria-label={`Next ${units.singular}`}
        title={`Next ${units.singular}`}
        disabled={nextPosition === null}
        onClick={goToNextPosition}
      >
        <ChevronRight aria-hidden />
      </ActionButton>
      <ActionButton
        iconOnly
        aria-label="Search this book"
        title="Search this book"
        onClick={() => setSearchOpen(true)}
      >
        <Search aria-hidden />
      </ActionButton>
    </div>
  );
  const contentsPage =
    contentsEntries.length > 0 && contentsPageIndex !== null ? (
      <GeneratedContentsPage
        bookTitle={title}
        entries={contentsEntries}
        pageIndex={contentsPageIndex}
        pageCount={contentsPageCount}
        onOpenPage={choosePage}
        toolbar={pageNavigationToolbar}
      />
    ) : processedBook.isPending ? (
      <p role="status" className="rounded-md border border-border bg-card p-6 text-sm text-muted-foreground">
        Loading the generated table of contents…
      </p>
    ) : null;
  const openReadingMode = () => {
    if (currentPosition === null) return;
    choosePosition(currentPosition);
    setReading({ startId: activeHeading });
  };
  // Leaving the reading window lands the page on the heading the reader reached in it (the page's text is drawn again first).
  const exitReading = () => {
    flushSync(() => setReading(null));
    if (activeHeading) document.getElementById(activeHeading)?.scrollIntoView?.({ block: "start" });
  };
  const contentsNavigation = (
    <div className="rounded-md border border-border bg-card p-3">
      {contentsPageCount > 0 ? (
        <ActionButton
          className="mb-3 w-full justify-start"
          onClick={() => choosePosition({ kind: "contents", index: 0 })}
        >
          <BookOpen aria-hidden />
          Read table of contents
        </ActionButton>
      ) : null}
      <ChapterTree
        tree={tree}
        activePage={page}
        unitLabel={units.singular}
        heading={activeView === "materials" && agentic.status === "done" ? "Contents" : "Chapters"}
        emptyMessage={
          activeView === "materials" && agentic.status === "done"
            ? "No headings were found in the processed pages."
            : "This book has no outline."
        }
        pageLabels={processedNumbers}
        isExtracted={(candidate) => extracted.has(candidate)}
        onOpenPage={choosePage}
      />
    </div>
  );
  const processedPagesNavigation = (
    <div className="rounded-md border border-border bg-card p-3">
      <PageList
        rows={rows}
        total={total}
        units={units}
        shown={page}
        following={view.follow}
        live={live}
        flags={flags}
        selection={contentSelection.data ?? null}
        selectionPending={agentic.status === "done" && contentSelection.isPending}
        selectionError={agentic.status === "done" && contentSelection.isError}
        processedNumbers={processedNumbers}
        showExcludedPages={false}
        onFollow={(follow) =>
          setView({
            follow,
            picked: follow || fallbackSourcePage === null ? null : { kind: "page", page: fallbackSourcePage },
          })
        }
        onPick={choosePage}
      />
    </div>
  );
  const navigation =
    activeView === "materials" ? (
      <div className="flex min-h-0 min-w-0 flex-col gap-3">
        <ToggleGroup
          fullWidth
          legend="Pages sidebar"
          options={PAGES_NAV_OPTIONS}
          value={pagesNavigation}
          onChange={setPagesNavigation}
        />
        {pagesNavigation === "contents" ? contentsNavigation : processedPagesNavigation}
      </div>
    ) : (
      <>
        {contentsNavigation}
        {processedPagesNavigation}
      </>
    );
  const currentProcessedPage =
    page === null
      ? 0
      : (processedNumbers.get(page) ??
        (activeView === "materials" ? rows.findIndex((item) => item.page === page) + 1 : page));
  const displayedPageCount =
    activeView === "materials" && agentic.status === "done"
      ? (processedBook.data?.source_page_count ?? rows.length)
      : nPages > 0
        ? nPages
        : total || rows.length;
  const unitLabel = `${units.singular[0]?.toUpperCase()}${units.singular.slice(1)}`;
  const position =
    contentsPageIndex !== null
      ? contentsPageCount > 0
        ? `Contents ${contentsPageIndex + 1} / ${contentsPageCount}`
        : "Contents"
      : `${unitLabel} ${currentProcessedPage} / ${displayedPageCount}`;
  const positionKey = showingContents ? `contents:${contentsPageIndex}` : `page:${page ?? "empty"}`;
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
      {status === "cancelled" || status === "failed" ? (
        <ActionButton onClick={start} disabled={runBook.isPending}>
          {runBook.isPending ? "Starting…" : "Resume"}
        </ActionButton>
      ) : null}
      <ActionButton
        iconOnly
        aria-label={`Previous ${units.singular}`}
        title={`Previous ${units.singular}`}
        disabled={previousPosition === null}
        onClick={goToPreviousPosition}
      >
        <ChevronLeft aria-hidden />
      </ActionButton>
      <ActionButton
        iconOnly
        aria-label={`Next ${units.singular}`}
        title={`Next ${units.singular}`}
        disabled={nextPosition === null}
        onClick={goToNextPosition}
      >
        <ChevronRight aria-hidden />
      </ActionButton>
      {reviewPages.length > 0 ? (
        <ActionButton
          aria-label={`Next with findings: the next ${units.singular} with retained findings`}
          title={`Jump to the next ${units.singular} with retained findings`}
          disabled={nextReview === null}
          onClick={() => nextReview !== null && choosePage(nextReview)}
        >
          <TriangleAlert aria-hidden />
          <span className="hidden sm:inline">Next with findings</span>
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
        aria-haspopup={activeView === "materials" ? undefined : "dialog"}
        title={
          activeView === "materials"
            ? `Read this ${units.singular} in reading mode`
            : `Read this ${units.singular} on the whole screen`
        }
        onClick={openReadingMode}
        disabled={currentPosition === null}
      >
        <Maximize2 aria-hidden />
        Reading mode
      </ActionButton>
    </div>
  );
  return (
    <section
      aria-label={title}
      className={activeView === "materials" ? "flex min-h-0 min-w-0 flex-1 flex-col" : "min-w-0"}
    >
      {activeView === "materials" ? null : <BookReadingProgress />}
      {pageViewControls}
      {activeView !== "materials" ? (
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
        </header>
      ) : null}

      <div aria-live="polite" className={activeView === "materials" && reading ? "text-sm" : "mt-3 text-sm"}>
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

      <AgenticUsageSheet
        open={usageOpen}
        bookId={bookId}
        run={agentic.run}
        live={agentic.streaming}
        onClose={() => setUsageOpen(false)}
      />
      <section aria-label="Agents" hidden={activeView !== "pipeline"} className="mt-4">
        <div className="mb-4 grid grid-cols-1 items-stretch gap-3 md:grid-cols-2 xl:grid-cols-3">
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
                <div className="grid w-full grid-cols-3 gap-2">
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
                    className="w-full"
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
                    className="w-full"
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
                    className="w-full"
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
          </section>
          <section
            aria-label="Ingestion Agentic AI progress"
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
            />
          </section>
          <RetrievalIndexProgress
            bookId={bookId}
            run={agentic.status === "done" ? agentic.run : null}
            index={agentic.status === "done" ? snapshot.retrievalIndex : null}
          />
        </div>
        <div className="mt-4 grid min-w-0 grid-cols-1 items-start gap-4 xl:grid-cols-[minmax(0,4fr)_minmax(0,4fr)_minmax(0,2fr)]">
          <AgenticPipelineOverview
            bookId={bookId}
            run={agentic.run}
            status={agentic.status}
            progress={agentic.progress}
            error={agentic.error}
            replayError={replayError}
            units={units}
            chapters={state.chapters.length > 0 ? state.chapters : manifest.chapters}
            onOpenUsage={() => setUsageOpen(true)}
            replayDisabled={
              !agentic.run || !["done", "failed", "cancelled"].includes(agentic.status ?? "")
            }
            replayPending={replayAgenticNode.isPending}
            replayPendingNodeId={replayPendingNodeId}
            onPlayNode={(nodeId) => void replayAgenticFromNode(nodeId)}
          />
          <RetrievalIndexPanel
            bookId={bookId}
            run={agentic.status === "done" ? agentic.run : null}
            index={agentic.status === "done" ? snapshot.retrievalIndex : null}
          />
          <AgenticPipelineUsage
            bookId={bookId}
            run={agentic.run}
            status={agentic.status}
            retrievalIndex={agentic.status === "done" ? snapshot.retrievalIndex : null}
          />
        </div>
      </section>
      <section
        aria-label="Pages"
        hidden={activeView !== "materials"}
        className={activeView === "materials" && reading ? "flex min-h-0 flex-1 flex-col" : "mt-4"}
      >
        {agentic.status === "done" && processedBook.isError ? (
          <p role="status" className="mb-3 text-sm text-warning-text">
            The processed page map is unavailable. Showing selected pages in original source order.
          </p>
        ) : null}
        {processedBook.data && processedBook.data.contents_status !== "complete" ? (
          <p role="status" className="mb-3 text-sm text-warning-text">
            {processedBook.data.contents_status === "unavailable"
              ? "No contents tree could be generated from retained-page headings. " +
                "Processed pages and source citations are preserved."
              : "Some generated contents links need review. Processed pages and source citations are preserved."}
          </p>
        ) : null}
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
            {showingContents ? (
              contentsPage
            ) : (
              <PageReader
                bookId={bookId}
                bookMetadata={{
                  status,
                  sizeBytes: manifest.size_bytes,
                  pageCount: displayedPageCount,
                }}
                page={page}
                row={row}
                nPages={displayedPageCount}
                processedPageNumber={
                  activeView === "materials" && agentic.status === "done" ? currentProcessedPage : undefined
                }
                processedPageCount={
                  activeView === "materials" && agentic.status === "done" ? displayedPageCount : undefined
                }
                units={units}
                content={content}
                toolbar={activeView === "materials" ? pageNavigationToolbar : toolbar}
                previewMode={previewMode}
                onPreviewModeChange={setPreviewMode}
                showViewControls={activeView !== "materials"}
              />
            )}

            {showOutlineColumn ? (
              <div className="hidden lg:block">
                <div className={STICKY_COLUMN}>{outlineView}</div>
              </div>
            ) : null}
          </div>
        ) : currentPosition !== null ? (
          <BookReadingMode
            title={title}
            position={position}
            positionKey={positionKey}
            hasPrevious={previousPosition !== null}
            hasNext={nextPosition !== null}
            navigation={navigation}
            outline={outlineView}
            layout={layout}
            onLayout={updateLayout}
            startId={reading.startId}
            onPrevious={goToPreviousPosition}
            onNext={goToNextPosition}
            onSearch={() => setSearchOpen(true)}
            onExit={exitReading}
            embedded={activeView === "materials"}
          >
            {showingContents ? (
              contentsPage
            ) : (
              <PageReader
                bookId={bookId}
                page={page}
                row={row}
                nPages={displayedPageCount}
                processedPageNumber={
                  activeView === "materials" && agentic.status === "done" ? currentProcessedPage : undefined
                }
                processedPageCount={
                  activeView === "materials" && agentic.status === "done" ? displayedPageCount : undefined
                }
                units={units}
                content={content}
                toolbar={activeView === "materials" ? pageNavigationToolbar : undefined}
                framed={false}
                previewMode={previewMode}
                onPreviewModeChange={setPreviewMode}
                showViewControls={activeView !== "materials"}
              />
            )}
          </BookReadingMode>
        ) : activeView === "materials" ? (
          <p
            role="status"
            className="flex min-h-0 flex-1 items-center justify-center p-6 text-sm text-muted-foreground"
          >
            No extracted pages are available to read yet.
          </p>
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
      </section>
    </section>
  );
}
