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
import { type RefObject, useEffect, useMemo, useRef, useState } from "react";
import { flushSync } from "react-dom";
import { useScrollSpy } from "@/shared/lib/use-scroll-spy";
import { ActionButton } from "../../components/action-button";
import { BookReadingMode } from "../../components/book-reading-mode";
import { BookReadingProgress } from "../../components/book-reading-progress";
import { PageOutline } from "../../components/page-outline";
import { useReaderLayout } from "../../hooks/use-reader-layout";
import type { LearningGuide } from "../entities";
import { STUDY_LAST_SECTION_STORAGE_PREFIX } from "../params";
import { StudyPageArticle } from "./learning-guide-page";
import type { GuideQuality, StudyPage } from "./learning-guide-reader-domain";
import { createStudyPages, needsReview, pageOutline } from "./learning-guide-reader-domain";

const COLUMNS = {
  both: "lg:grid-cols-[16rem_minmax(0,1fr)_14rem]",
  contentsOnly: "lg:grid-cols-[16rem_minmax(0,1fr)]",
  outlineOnly: "lg:grid-cols-[minmax(0,1fr)_14rem]",
  neither: "lg:grid-cols-[minmax(0,1fr)]",
} as const;
const STICKY_COLUMN =
  "lg:sticky lg:top-[calc(var(--chrome-top)+1.5rem)] lg:max-h-[calc(100dvh-var(--chrome-top)-2.5rem)] lg:self-start lg:overflow-y-auto";
const EMPTY_STUDY_PAGE: StudyPage = {
  id: "guide-overview",
  title: "Guide overview",
  group: "Guide",
  kind: "overview",
};

/** Reads Distill's report as a section-by-section book, keeping its source citations connected to BD Pages. */
export function LearningGuideReader({ report, quality }: { report: LearningGuide; quality: GuideQuality }) {
  const pages = useMemo(() => createStudyPages(report), [report]);
  const storageKey = `${STUDY_LAST_SECTION_STORAGE_PREFIX}${report.book_id}:${report.distill_run_id}`;
  const [currentId, setCurrentId] = useState(pages[0]?.id ?? "guide-overview");
  const [restored, setRestored] = useState(false);
  const [search, setSearch] = useState("");
  const [reading, setReading] = useState<{ startId: string | null } | null>(null);
  const searchInput = useRef<HTMLInputElement | null>(null);
  const { layout, update: updateLayout } = useReaderLayout();
  const updateLayoutRef = useRef(updateLayout);
  updateLayoutRef.current = updateLayout;

  useEffect(() => {
    try {
      const remembered = window.localStorage.getItem(storageKey);
      if (remembered && pages.some((page) => page.id === remembered)) setCurrentId(remembered);
    } catch {
      // Storage may be blocked; the Guide still opens at its overview.
    }
    setRestored(true);
  }, [pages, storageKey]);

  useEffect(() => {
    if (!restored) return;
    try {
      window.localStorage.setItem(storageKey, currentId);
    } catch {
      // Storage may be blocked; keep the current section in memory for this visit.
    }
  }, [currentId, restored, storageKey]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        updateLayoutRef.current(reading ? { readingContents: true } : { contents: true });
        window.requestAnimationFrame(() => searchInput.current?.focus());
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [reading]);

  const currentIndex = Math.max(
    0,
    pages.findIndex((page) => page.id === currentId),
  );
  const page = pages[currentIndex] ?? EMPTY_STUDY_PAGE;

  const outline = useMemo(() => pageOutline(page, report), [page, report]);
  const outlineIds = useMemo(() => outline.map((entry) => entry.id), [outline]);
  const activeHeading = useScrollSpy(outlineIds, `${page.id}:${reading !== null}`);
  const hasOutline = outline.length >= 2;
  const showOutline = layout.outline && hasOutline;
  const columns = layout.contents
    ? showOutline
      ? COLUMNS.both
      : COLUMNS.contentsOnly
    : showOutline
      ? COLUMNS.outlineOnly
      : COLUMNS.neither;
  const position = `${currentIndex + 1} / ${pages.length}`;
  const previousPage = currentIndex > 0 ? currentIndex : null;
  const nextPage = currentIndex < pages.length - 1 ? currentIndex + 2 : null;
  const reviewPages = pages.flatMap((candidate, index) => (needsReview(candidate, report) ? [index] : []));
  const nextReviewIndex =
    reviewPages.find((index) => index > currentIndex) ??
    (needsReview(page, report) ? null : (reviewPages[0] ?? null));
  const navigation = (
    <StudyContents
      pages={pages}
      report={report}
      currentId={page.id}
      search={search}
      searchInput={searchInput}
      onSearch={setSearch}
      onChoose={(nextPageIndex) => choosePage(nextPageIndex)}
    />
  );
  const outlineView = hasOutline ? <PageOutline entries={outline} activeId={activeHeading} /> : null;
  const toolbar = (
    <div className="flex shrink-0 flex-wrap items-center justify-end gap-1">
      <ActionButton
        iconOnly
        aria-label="Previous Study section"
        title="Previous Study section"
        disabled={previousPage === null}
        onClick={() => previousPage !== null && choosePage(previousPage - 1)}
      >
        <ChevronLeft aria-hidden />
      </ActionButton>
      <ActionButton
        iconOnly
        aria-label="Next Study section"
        title="Next Study section"
        disabled={nextPage === null}
        onClick={() => nextPage !== null && choosePage(nextPage - 1)}
      >
        <ChevronRight aria-hidden />
      </ActionButton>
      {reviewPages.length > 0 ? (
        <ActionButton
          aria-label="Next section that needs review"
          title="Jump to the next section that needs review"
          disabled={nextReviewIndex === null}
          onClick={() => nextReviewIndex !== null && choosePage(nextReviewIndex)}
        >
          <TriangleAlert aria-hidden />
          <span className="hidden sm:inline">Next to review</span>
        </ActionButton>
      ) : null}
      {!layout.contents ? (
        <ActionButton
          iconOnly
          aria-label="Search Study contents"
          title="Search Study contents"
          onClick={() => {
            updateLayout({ contents: true });
            window.requestAnimationFrame(() => searchInput.current?.focus());
          }}
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
        title="Read this Study section on the whole screen"
        onClick={() => setReading({ startId: activeHeading })}
      >
        <Maximize2 aria-hidden />
        Reading mode
      </ActionButton>
    </div>
  );

  function choosePage(index: number) {
    const target = pages[index];
    if (target) setCurrentId(target.id);
  }

  function exitReading() {
    flushSync(() => setReading(null));
    if (activeHeading) document.getElementById(activeHeading)?.scrollIntoView?.({ block: "start" });
  }

  return (
    <section aria-label="Learning Guide reader" className="min-w-0">
      <BookReadingProgress />
      {reading ? (
        <BookReadingMode
          title={`Learning Guide · ${report.title}`}
          unitLabel="Study page"
          position={position}
          page={currentIndex + 1}
          previousPage={previousPage}
          nextPage={nextPage}
          navigation={navigation}
          outline={outlineView}
          layout={layout}
          onLayout={updateLayout}
          startId={reading.startId}
          onPage={(target) => choosePage(target - 1)}
          onSearch={() => {
            updateLayout({ readingContents: true });
            window.requestAnimationFrame(() => searchInput.current?.focus());
          }}
          onExit={exitReading}
        >
          <StudyPageArticle
            report={report}
            quality={quality}
            page={page}
            index={currentIndex}
            total={pages.length}
          />
        </BookReadingMode>
      ) : (
        <div className={`flex flex-col gap-4 lg:grid ${columns}`}>
          {layout.contents ? (
            <aside className={`flex min-w-0 flex-col gap-3 ${STICKY_COLUMN}`}>{navigation}</aside>
          ) : null}
          <StudyPageArticle
            report={report}
            quality={quality}
            page={page}
            index={currentIndex}
            total={pages.length}
            toolbar={toolbar}
          />
          {showOutline ? (
            <div className="hidden lg:block">
              <div className={STICKY_COLUMN}>{outlineView}</div>
            </div>
          ) : null}
        </div>
      )}
    </section>
  );
}

function StudyContents({
  pages,
  report,
  currentId,
  search,
  searchInput,
  onSearch,
  onChoose,
}: {
  pages: readonly StudyPage[];
  report: LearningGuide;
  currentId: string;
  search: string;
  searchInput: RefObject<HTMLInputElement | null>;
  onSearch: (value: string) => void;
  onChoose: (index: number) => void;
}) {
  const groups = useMemo(() => {
    const query = search.trim().toLocaleLowerCase();
    const visible = pages
      .map((page, index) => ({ page, index }))
      .filter(({ page }) => !query || `${page.group} ${page.title}`.toLocaleLowerCase().includes(query));
    return [...new Set(visible.map(({ page }) => page.group))].map((group) => ({
      group,
      pages: visible.filter((entry) => entry.page.group === group),
    }));
  }, [pages, search]);

  return (
    <section className="rounded-md border border-border bg-card p-3" aria-label="Learning Guide contents">
      <div className="flex items-center justify-between gap-2">
        <h3 className="mono-label">Guide contents · {pages.length} sections</h3>
        <span className="tabular text-xs text-muted-foreground">
          {groups.reduce((n, group) => n + group.pages.length, 0)} shown
        </span>
      </div>
      <label className="sr-only" htmlFor="study-section-search">
        Search Study sections
      </label>
      <input
        ref={searchInput}
        id="study-section-search"
        type="search"
        aria-label="Search Study sections"
        placeholder="Search sections"
        value={search}
        onChange={(event) => onSearch(event.currentTarget.value)}
        className="mt-2 h-9 w-full min-w-0 rounded-sm border border-border bg-background px-2 text-sm"
      />
      <nav aria-label="Learning Guide sections" className="mt-3 max-h-[min(65dvh,48rem)] overflow-auto">
        {groups.length === 0 ? (
          <p className="py-3 text-sm text-muted-foreground">No Study sections match that search.</p>
        ) : (
          groups.map(({ group, pages: groupPages }) => (
            <section key={group} className="mb-3 last:mb-0">
              <h4 className="mb-1 px-2 text-xs font-medium text-muted-foreground">{group}</h4>
              <ol>
                {groupPages.map(({ page, index }) => (
                  <li key={page.id}>
                    <button
                      type="button"
                      aria-current={page.id === currentId ? "page" : undefined}
                      onClick={() => onChoose(index)}
                      className={`flex min-h-10 w-full items-center gap-2 border-l-2 px-2 text-left text-sm ${
                        page.id === currentId
                          ? "border-primary bg-muted font-medium text-foreground"
                          : "border-transparent text-muted-foreground hover:bg-muted hover:text-foreground"
                      }`}
                    >
                      <span className="min-w-0 flex-1 truncate" title={page.title}>
                        {page.title}
                      </span>
                      {needsReview(page, report) ? (
                        <TriangleAlert aria-hidden className="size-3.5 shrink-0 text-warning-text" />
                      ) : null}
                      <span className="tabular shrink-0 text-xs">{index + 1}</span>
                    </button>
                  </li>
                ))}
              </ol>
            </section>
          ))
        )}
      </nav>
    </section>
  );
}
