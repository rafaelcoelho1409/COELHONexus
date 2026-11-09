"use client";

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
import { useEffect, useMemo, useRef, useState } from "react";
import { flushSync } from "react-dom";
import { useScrollSpy } from "@/shared/lib/use-scroll-spy";
import type { StreamStatus } from "@/shared/sse";

import { ActionButton } from "../../components/action-button";
import { BookReadingMode } from "../../components/book-reading-mode";
import { BookReadingProgress } from "../../components/book-reading-progress";
import { BookSearch } from "../../components/book-search";
import { ChapterTree } from "../../components/chapter-tree";
import { PageList } from "../../components/page-list";
import { PageOutline } from "../../components/page-outline";
import { PageReader } from "../../components/page-reader";
import { ToggleGroup } from "../../components/toggle-group";
import { bookUnits } from "../../domain/books";
import { chapterTree } from "../../domain/outline";
import { mergePages, neighbourReviewPage, pageFlags } from "../../domain/pages";
import { effectiveStatus } from "../../domain/run-state";
import type { BookSnapshot, ContentSelection } from "../../entities";
import { readRememberedPage, rememberPage } from "../../hooks/remembered-page";
import { usePageContent } from "../../hooks/use-page-content";
import { useReaderLayout } from "../../hooks/use-reader-layout";
import type { DistillRunViewState } from "../domain/run-state";
import type { DistillRun } from "../entities";
import { ContentSelectionReview } from "./content-selection-review";
import { DistillRunPanel } from "./distill-run-panel";

const OUTPUT_MODES = [
  { id: "pages", label: "Pages" },
  { id: "selection", label: "Selection" },
  { id: "guide", label: "Guide" },
] as const;
type OutputMode = (typeof OUTPUT_MODES)[number]["id"];

const COLUMNS = {
  both: "lg:grid-cols-[16rem_minmax(0,1fr)_14rem]",
  contentsOnly: "lg:grid-cols-[16rem_minmax(0,1fr)]",
  outlineOnly: "lg:grid-cols-[minmax(0,1fr)_14rem]",
  neither: "lg:grid-cols-[minmax(0,1fr)]",
} as const;

const STICKY_COLUMN =
  "lg:sticky lg:top-[calc(var(--chrome-top)+1.5rem)] lg:max-h-[calc(100dvh-var(--chrome-top)-2.5rem)] lg:self-start lg:overflow-y-auto";

/** Distill's source reader, reviewed input pages, and finished guide live in the Study stage. */
export function DistillOutputPanel({
  bookId,
  book,
  initialPage,
  agenticRunId,
  selection,
  selectionPending,
  selectionError,
  savingSelection,
  saveSelectionError,
  onRetrySelection,
  onSaveSelection,
  run,
  runState,
  streamStatus,
}: {
  bookId: string;
  book: BookSnapshot;
  initialPage: number | null;
  agenticRunId: string | null;
  selection: ContentSelection | null;
  selectionPending: boolean;
  selectionError: unknown;
  savingSelection: boolean;
  saveSelectionError: unknown;
  onRetrySelection: () => void;
  onSaveSelection: (
    expectedRevision: number,
    overrides: { page_number: number; disposition: "include" | "exclude" }[],
  ) => void;
  run: DistillRun | null;
  runState: DistillRunViewState | null;
  streamStatus: StreamStatus;
}) {
  const [mode, setMode] = useState<OutputMode | null>(null);
  const [reading, setReading] = useState<{ startId: string | null } | null>(null);
  const [searchOpen, setSearchOpen] = useState(false);
  const { layout, update: updateLayout } = useReaderLayout();
  const rows = useMemo(() => mergePages(book.manifest.pages, {}), [book.manifest.pages]);
  const fallbackPage = selection?.pages.find((page) => page.disposition === "include")?.page_number;
  const [pickedPage, setPickedPage] = useState<number | null>(() => {
    const cited = initialPage !== null && rows.some((row) => row.page === initialPage) ? initialPage : null;
    const remembered = readRememberedPage(bookId, book.manifest.n_pages);
    return (
      cited ??
      (remembered !== null && rows.some((row) => row.page === remembered) ? remembered : null) ??
      fallbackPage ??
      rows[0]?.page ??
      null
    );
  });
  const page = rows.some((row) => row.page === pickedPage)
    ? pickedPage
    : (fallbackPage ?? rows[0]?.page ?? null);
  const row = rows.find((candidate) => candidate.page === page);
  const units = bookUnits(book.manifest.filename);
  const tree = useMemo(
    () => chapterTree(book.manifest.chapters, book.manifest.outline),
    [book.manifest.chapters, book.manifest.outline],
  );
  const extracted = new Set(rows.map((row) => row.page));
  const content = usePageContent({
    bookId,
    page,
    row,
    livePage: undefined,
    agenticRunAvailable: book.agentic !== null,
  });
  const activeHeading = useScrollSpy(content.outlineIds, `${reading !== null}:${page}:${content.version}`);
  const reportAvailable = run !== null && runState?.status === "done";
  const selectionNeedsAttention = selectionError !== null && selectionError !== undefined;
  const availableModes = reportAvailable
    ? OUTPUT_MODES
    : OUTPUT_MODES.filter((option) => option.id !== "guide");
  const visibleMode =
    mode ??
    (initialPage !== null
      ? "pages"
      : reportAvailable
        ? "guide"
        : selectionNeedsAttention
          ? "selection"
          : "pages");
  const description =
    visibleMode === "selection"
      ? "Choose which classified pages Distill will use. Excluded pages stay in the original book."
      : visibleMode === "guide"
        ? "Review the condensed lessons and evidence citations from this run."
        : "Read Ingestion-processed pages, review Distill inputs, or open the finished Learning Guide.";
  const currentIndex = rows.findIndex((candidate) => candidate.page === page);
  const previousPage = currentIndex > 0 ? (rows[currentIndex - 1]?.page ?? null) : null;
  const nextPage = currentIndex >= 0 ? (rows[currentIndex + 1]?.page ?? null) : null;
  const reviewPages = book.agentic?.review_pages ?? [];
  const nextReview = neighbourReviewPage(reviewPages, page, 1);
  const flags = pageFlags(book.agentic);
  const hasOutline = content.outline.length >= 2;
  const showOutlineColumn = layout.outline && hasOutline;
  const title = book.manifest.title.trim() || book.manifest.filename;
  const unitLabel = `${units.singular[0]?.toUpperCase()}${units.singular.slice(1)}`;
  const position = `${page ?? 0} / ${book.manifest.n_pages || rows.length}`;
  const outline = hasOutline ? <PageOutline entries={content.outline} activeId={activeHeading} /> : null;
  const columns = layout.contents
    ? showOutlineColumn
      ? COLUMNS.both
      : COLUMNS.contentsOnly
    : showOutlineColumn
      ? COLUMNS.outlineOnly
      : COLUMNS.neither;
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

  const choosePage = (target: number) => {
    rememberPage(bookId, target);
    setPickedPage(target);
  };
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
          total={book.manifest.n_pages}
          units={units}
          shown={page}
          following={false}
          live={false}
          flags={flags}
          selection={selection}
          selectionPending={selectionPending}
          selectionError={agenticRunId !== null && Boolean(selectionError)}
          onFollow={() => undefined}
          onPick={choosePage}
        />
      </div>
    </>
  );
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
      {!layout.contents ? (
        <ActionButton
          iconOnly
          aria-label="Search this book"
          title="Search this book"
          onClick={() => setSearchOpen(true)}
        >
          <Search aria-hidden />
        </ActionButton>
      ) : null}
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
        onClick={() => setReading({ startId: activeHeading })}
        disabled={page === null}
      >
        <Maximize2 aria-hidden />
        Reading mode
      </ActionButton>
    </div>
  );

  return (
    <>
      <BookReadingProgress />
      <section aria-label="Distill book content" className="min-w-0">
        <header className="flex min-w-0 flex-wrap items-start justify-between gap-3 border-b border-border pb-3">
          <div className="min-w-0">
            <p className="mono-label">Distill · Book content</p>
            <h3 className="mt-1 text-base font-semibold">
              {visibleMode === "guide"
                ? "Learning Guide"
                : visibleMode === "selection"
                  ? "Page selection"
                  : "Processed pages"}
            </h3>
            <p className="mt-1 text-xs text-muted-foreground">{description}</p>
          </div>
          <ToggleGroup
            legend="Distill content view"
            options={availableModes}
            value={visibleMode}
            onChange={setMode}
          />
        </header>

        {visibleMode === "pages" ? (
          reading === null ? (
            <div className={`mt-3 flex min-w-0 flex-col gap-4 lg:grid ${columns}`}>
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

              <PageReader
                bookId={bookId}
                bookMetadata={{
                  title,
                  filename: book.manifest.filename,
                  status: effectiveStatus(book.manifest.status, book.progress?.status, null),
                  sizeBytes: book.manifest.size_bytes,
                  pageCount: book.manifest.n_pages,
                }}
                page={page}
                row={row}
                nPages={book.manifest.n_pages}
                units={units}
                content={content}
                toolbar={toolbar}
              />

              {showOutlineColumn && outline ? (
                <div className="hidden lg:block">
                  <div className={STICKY_COLUMN}>{outline}</div>
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
              outline={outline}
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
                nPages={book.manifest.n_pages}
                units={units}
                content={content}
                framed={false}
              />
            </BookReadingMode>
          ) : null
        ) : visibleMode === "selection" ? (
          <div className="mt-3 min-w-0">
            <ContentSelectionReview
              bookId={bookId}
              agenticRunId={agenticRunId}
              selection={selection}
              pending={selectionPending}
              error={selectionError}
              saving={savingSelection}
              saveError={saveSelectionError}
              onRetry={onRetrySelection}
              onSave={onSaveSelection}
            />
          </div>
        ) : reportAvailable && run && runState ? (
          <div className="mt-3 min-w-0">
            <DistillRunPanel
              key={run.run_id}
              bookId={bookId}
              run={run}
              state={runState}
              streamStatus={streamStatus}
            />
          </div>
        ) : (
          <p className="mt-3 rounded-sm border border-border p-3 text-sm text-muted-foreground">
            The Learning Guide appears here after a Distill run completes.
          </p>
        )}
        {visibleMode === "pages" && searchOpen ? (
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
    </>
  );
}
