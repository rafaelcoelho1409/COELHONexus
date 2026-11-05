"use client";

/** The reading window: one page on the whole screen, in a modal of its own. A slim bar on top holds the page's position,
 * previous/next, search, the text width and the two side columns (closed on opening, so the text has the screen); the
 * text scrolls under it with its own progress line. Built on the native `<dialog>` — focus stays inside, the page behind is
 * inert, Escape leaves — and it is rendered only while the reader is in it. */

import {
  ChevronLeft,
  ChevronRight,
  Minimize2,
  MoveHorizontal,
  PanelLeftClose,
  PanelLeftOpen,
  PanelRightClose,
  PanelRightOpen,
  Search,
} from "lucide-react";
import { type ReactNode, useEffect, useId, useRef, useState } from "react";

import type { ReaderLayout } from "../entities";
import { useModalDialog } from "../hooks/use-modal-dialog";
import { ActionButton } from "./action-button";
import { BookReadingProgress } from "./book-reading-progress";

/** How far the text runs: a comfortable line for prose, or most of a wide screen for tables and code. */
const TEXT_WIDTH: Record<ReaderLayout["width"], string> = {
  comfortable: "max-w-4xl",
  wide: "max-w-7xl",
};

export function BookReadingMode({
  title,
  unitLabel,
  position,
  page,
  previousPage,
  nextPage,
  navigation,
  outline,
  layout,
  onLayout,
  startId,
  onPage,
  onSearch,
  onExit,
  children,
}: {
  title: string;
  unitLabel: string;
  /** Where the page is in the book: "3 / 275". */
  position: string;
  page: number;
  previousPage: number | null;
  nextPage: number | null;
  /** The chapters and the page list, for the left column. */
  navigation: ReactNode;
  /** "On this page", for the right column; null when the page has none. */
  outline: ReactNode | null;
  layout: ReaderLayout;
  onLayout: (patch: Partial<ReaderLayout>) => void;
  /** The heading the reader was at on the page: the window opens there. */
  startId: string | null;
  onPage: (page: number) => void;
  onSearch: () => void;
  onExit: () => void;
  children: ReactNode;
}) {
  const ref = useModalDialog();
  const titleId = useId();
  const [scroller, setScroller] = useState<HTMLElement | null>(null);
  const previousPageRef = useRef(page);
  const openedAt = useRef(startId);

  // Open where the reader was, with the text focused so the arrow and page keys scroll it.
  useEffect(() => {
    if (openedAt.current) document.getElementById(openedAt.current)?.scrollIntoView?.({ block: "start" });
    scroller?.focus({ preventScroll: true });
  }, [scroller]);

  // Another page starts at its top.
  useEffect(() => {
    if (previousPageRef.current === page) return;
    previousPageRef.current = page;
    scroller?.scrollTo?.({ top: 0 });
  }, [page, scroller]);

  const wide = layout.width === "wide";
  const hasOutline = outline !== null;

  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      onCancel={(event) => {
        event.preventDefault();
        onExit();
      }}
      className="fixed inset-0 m-0 h-dvh max-h-none w-dvw max-w-none overflow-hidden border-0 bg-background p-0 text-foreground"
    >
      <div className="flex h-full flex-col">
        <header className="relative flex shrink-0 items-center gap-1 border-b border-border px-2 py-1.5 sm:gap-2 sm:px-3">
          <BookReadingProgress scroller={scroller} className="absolute inset-x-0 -bottom-px z-10 h-0.5" />
          <ActionButton
            iconOnly
            className="hidden md:inline-flex"
            aria-expanded={layout.readingContents}
            aria-label={layout.readingContents ? "Hide contents" : "Show contents"}
            title={layout.readingContents ? "Hide contents" : "Show contents"}
            onClick={() => onLayout({ readingContents: !layout.readingContents })}
          >
            {layout.readingContents ? <PanelLeftClose aria-hidden /> : <PanelLeftOpen aria-hidden />}
          </ActionButton>
          <div className="min-w-0 flex-1 px-1">
            <h2 id={titleId} className="truncate text-sm font-medium">
              {title}
            </h2>
            <p className="text-xs text-muted-foreground">
              {unitLabel} {position}
            </p>
          </div>
          <ActionButton
            iconOnly
            aria-label="Previous page"
            title="Previous page"
            disabled={previousPage === null}
            onClick={() => previousPage !== null && onPage(previousPage)}
          >
            <ChevronLeft aria-hidden />
          </ActionButton>
          <ActionButton
            iconOnly
            aria-label="Next page"
            title="Next page"
            disabled={nextPage === null}
            onClick={() => nextPage !== null && onPage(nextPage)}
          >
            <ChevronRight aria-hidden />
          </ActionButton>
          <ActionButton iconOnly aria-label="Search this book" title="Search this book" onClick={onSearch}>
            <Search aria-hidden />
          </ActionButton>
          <ActionButton
            className="hidden sm:inline-flex"
            aria-pressed={wide}
            title="Let the text run across most of the screen"
            onClick={() => onLayout({ width: wide ? "comfortable" : "wide" })}
          >
            <MoveHorizontal aria-hidden />
            Wide
          </ActionButton>
          {hasOutline ? (
            <ActionButton
              iconOnly
              className="hidden lg:inline-flex"
              aria-expanded={layout.readingOutline}
              aria-label={layout.readingOutline ? "Hide outline" : "Show outline"}
              title={layout.readingOutline ? "Hide outline" : "Show outline"}
              onClick={() => onLayout({ readingOutline: !layout.readingOutline })}
            >
              {layout.readingOutline ? <PanelRightClose aria-hidden /> : <PanelRightOpen aria-hidden />}
            </ActionButton>
          ) : null}
          <ActionButton aria-label="Exit reading mode" onClick={onExit}>
            <Minimize2 aria-hidden />
            <span className="hidden sm:inline">Exit</span>
          </ActionButton>
        </header>

        <div className="flex min-h-0 flex-1">
          {layout.readingContents ? (
            <aside className="hidden w-72 shrink-0 overflow-y-auto border-r border-border p-3 md:block">
              {navigation}
            </aside>
          ) : null}
          <section
            ref={setScroller}
            // biome-ignore lint/a11y/noNoninteractiveTabindex: the reading area scrolls, so keyboard users must be able to focus it (WCAG 2.1.1)
            tabIndex={0}
            aria-label="Book text"
            className="min-w-0 flex-1 overflow-y-auto overscroll-contain focus-visible:-outline-offset-2 focus-visible:outline-2 focus-visible:outline-ring"
          >
            <div className={`mx-auto px-5 py-8 sm:px-8 ${TEXT_WIDTH[layout.width]}`}>{children}</div>
          </section>
          {layout.readingOutline && hasOutline ? (
            <aside className="hidden w-64 shrink-0 overflow-y-auto border-l border-border p-3 lg:block">
              {outline}
            </aside>
          ) : null}
        </div>
      </div>
    </dialog>
  );
}
