"use client";

import {
  ChevronLeft,
  ChevronRight,
  Minimize2,
  MoveHorizontal,
  PanelLeftClose,
  PanelLeftOpen,
  Search,
} from "lucide-react";
import { type ReactNode, useEffect, useId, useRef, useState } from "react";

import { useModalDialog } from "../hooks/use-modal-dialog";
import { ActionButton } from "./action-button";
import { BookReadingProgress } from "./book-reading-progress";

export function BookReadingMode({
  title,
  unitLabel,
  position,
  page,
  previousPage,
  nextPage,
  navigation,
  onPage,
  onSearch,
  onExit,
  children,
}: {
  title: string;
  unitLabel: string;
  position: string;
  page: number;
  previousPage: number | null;
  nextPage: number | null;
  navigation: ReactNode;
  onPage: (page: number) => void;
  onSearch: () => void;
  onExit: () => void;
  children: ReactNode;
}) {
  const ref = useModalDialog();
  const titleId = useId();
  const [scroller, setScroller] = useState<HTMLElement | null>(null);
  const [navigationOpen, setNavigationOpen] = useState(false);
  const [wide, setWide] = useState(false);
  const previousPageRef = useRef(page);

  useEffect(() => {
    if (previousPageRef.current === page) return;
    previousPageRef.current = page;
    scroller?.scrollTo?.({ top: 0 });
  }, [page, scroller]);

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
            aria-expanded={navigationOpen}
            aria-label={navigationOpen ? "Hide contents" : "Show contents"}
            title={navigationOpen ? "Hide contents" : "Show contents"}
            onClick={() => setNavigationOpen(!navigationOpen)}
          >
            {navigationOpen ? <PanelLeftClose aria-hidden /> : <PanelLeftOpen aria-hidden />}
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
            onClick={() => setWide(!wide)}
          >
            <MoveHorizontal aria-hidden />
            Wide
          </ActionButton>
          <ActionButton aria-label="Exit reading mode" onClick={onExit}>
            <Minimize2 aria-hidden />
            <span className="hidden sm:inline">Exit</span>
          </ActionButton>
        </header>

        <div className="flex min-h-0 flex-1">
          {navigationOpen ? (
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
            <div className={`mx-auto px-5 py-8 sm:px-8 ${wide ? "max-w-7xl" : "max-w-4xl"}`}>{children}</div>
          </section>
        </div>
      </div>
    </dialog>
  );
}
