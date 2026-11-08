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
import { useMemo, useState } from "react";
import { useScrollSpy } from "@/shared/lib/use-scroll-spy";
import type { StreamStatus } from "@/shared/sse";

import { ActionButton } from "../../components/action-button";
import { BookReadingMode } from "../../components/book-reading-mode";
import { BookSearch } from "../../components/book-search";
import { PageList } from "../../components/page-list";
import { PageOutline } from "../../components/page-outline";
import { PageReader } from "../../components/page-reader";
import { ToggleGroup } from "../../components/toggle-group";
import { bookUnits } from "../../domain/books";
import { mergePages, neighbourReviewPage, pageFlags } from "../../domain/pages";
import type { BookSnapshot, ContentSelection } from "../../entities";
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

/** The reader, reviewed input pages, and finished guide share the content side of the Distill workspace. */
export function DistillOutputPanel({
  bookId,
  book,
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
  const [sidebar, setSidebar] = useState<"contents" | "outline" | null>("contents");
  const [reading, setReading] = useState<{ startId: string | null } | null>(null);
  const [searchOpen, setSearchOpen] = useState(false);
  const { layout, update: updateLayout } = useReaderLayout();
  const rows = useMemo(() => mergePages(book.manifest.pages, {}), [book.manifest.pages]);
  const fallbackPage = selection?.pages.find((page) => page.disposition === "include")?.page_number;
  const [pickedPage, setPickedPage] = useState<number | null>(null);
  const page = rows.some((row) => row.page === pickedPage)
    ? pickedPage
    : (fallbackPage ?? rows[0]?.page ?? null);
  const row = rows.find((candidate) => candidate.page === page);
  const units = bookUnits(book.manifest.filename);
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
  const visibleMode = mode ?? (reportAvailable ? "guide" : selectionNeedsAttention ? "selection" : "pages");
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
  const visibleSidebar = sidebar === "outline" && !hasOutline ? null : sidebar;
  const title = book.manifest.title.trim() || book.manifest.filename;
  const unitLabel = `${units.singular[0]?.toUpperCase()}${units.singular.slice(1)}`;
  const position = `${page ?? 0} / ${book.manifest.n_pages || rows.length}`;
  const outline = hasOutline ? <PageOutline entries={content.outline} activeId={activeHeading} /> : null;
  const pageGridColumns =
    visibleSidebar === "contents"
      ? "xl:grid-cols-[minmax(10rem,0.36fr)_minmax(0,1fr)]"
      : visibleSidebar === "outline"
        ? "xl:grid-cols-[minmax(0,1fr)_minmax(10rem,0.3fr)]"
        : "grid-cols-1";
  const navigation = (
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
      onPick={setPickedPage}
    />
  );
  const toolbar = (
    <div className="flex max-w-full flex-wrap items-center justify-start gap-1 sm:justify-end">
      <ActionButton
        iconOnly
        aria-label={`Previous ${units.singular}`}
        title={`Previous ${units.singular}`}
        disabled={previousPage === null}
        onClick={() => previousPage !== null && setPickedPage(previousPage)}
      >
        <ChevronLeft aria-hidden />
      </ActionButton>
      <ActionButton
        iconOnly
        aria-label={`Next ${units.singular}`}
        title={`Next ${units.singular}`}
        disabled={nextPage === null}
        onClick={() => nextPage !== null && setPickedPage(nextPage)}
      >
        <ChevronRight aria-hidden />
      </ActionButton>
      {reviewPages.length > 0 ? (
        <ActionButton
          aria-label={`Next to review: the next ${units.singular} that needs review`}
          title={`Jump to the next ${units.singular} that needs review`}
          disabled={nextReview === null}
          onClick={() => nextReview !== null && setPickedPage(nextReview)}
        >
          <TriangleAlert aria-hidden />
          <span className="hidden sm:inline">Next to review</span>
        </ActionButton>
      ) : null}
      {sidebar !== "contents" ? (
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
        aria-expanded={sidebar === "contents"}
        aria-label={sidebar === "contents" ? "Hide contents" : "Show contents"}
        title={sidebar === "contents" ? "Hide contents" : "Show contents"}
        onClick={() => setSidebar(sidebar === "contents" ? null : "contents")}
      >
        {sidebar === "contents" ? <PanelLeftClose aria-hidden /> : <PanelLeftOpen aria-hidden />}
      </ActionButton>
      {hasOutline ? (
        <ActionButton
          iconOnly
          className="hidden lg:inline-flex"
          aria-expanded={sidebar === "outline"}
          aria-label={sidebar === "outline" ? "Hide outline" : "Show outline"}
          title={sidebar === "outline" ? "Hide outline" : "Show outline"}
          onClick={() => setSidebar(sidebar === "outline" ? null : "outline")}
        >
          {sidebar === "outline" ? <PanelRightClose aria-hidden /> : <PanelRightOpen aria-hidden />}
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
    <section
      aria-label="Distill book content"
      className="min-w-0 rounded-md border border-border bg-card p-3 sm:p-4"
    >
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
          <div className={`mt-3 grid min-w-0 grid-cols-1 items-start gap-3 ${pageGridColumns}`}>
            {visibleSidebar === "contents" ? (
              <aside className="min-w-0 rounded-sm border border-border bg-background p-3">
                {navigation}
              </aside>
            ) : null}
            <div className="min-w-0 rounded-sm border border-border bg-background p-3 sm:p-4">
              <PageReader
                bookId={bookId}
                bookTitle={title}
                filename={book.manifest.filename}
                page={page}
                row={row}
                nPages={book.manifest.n_pages}
                units={units}
                content={content}
                framed={false}
                toolbar={toolbar}
              />
            </div>
            {visibleSidebar === "outline" && outline ? (
              <aside className="min-w-0 rounded-sm border border-border bg-background p-3">{outline}</aside>
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
            onPage={setPickedPage}
            onSearch={() => setSearchOpen(true)}
            onExit={() => setReading(null)}
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
            setPickedPage(hit.page);
            setSearchOpen(false);
          }}
          onClose={() => setSearchOpen(false)}
        />
      ) : null}
    </section>
  );
}
