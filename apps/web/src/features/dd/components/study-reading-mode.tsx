"use client";

/** The reading window: one chapter on the whole screen, in a modal of its own. A slim bar on top holds the chapter's
 * position, previous/next, search, the text width and the two side columns (closed on opening, so the chapter has the screen);
 * the text scrolls under it with its own progress line. Built on the native `<dialog>` — focus stays inside, the page behind is
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
import Link from "next/link";
import { type ReactNode, useEffect, useId, useRef, useState } from "react";

import { Button } from "@/shared/ui/button";
import { buttonVariants } from "@/shared/ui/button-variants";

import type { StudyLayout } from "../entities";
import { useModalDialog } from "../hooks/use-modal-dialog";
import { ReadingProgress } from "./reading-progress";

/** How far the text runs: a comfortable line for prose, or most of a wide screen for tables and code. */
const TEXT_WIDTH: Record<StudyLayout["width"], string> = {
  comfortable: "max-w-4xl",
  wide: "max-w-7xl",
};

export function StudyReadingMode({
  title,
  position,
  previousHref,
  nextHref,
  layout,
  onLayout,
  onSearch,
  onExit,
  startId,
  chapterId,
  chapters,
  outline,
  children,
}: {
  title: string;
  /** Where the chapter is in the plan: "3 / 13". */
  position: string;
  previousHref: string | null;
  nextHref: string | null;
  layout: StudyLayout;
  onLayout: (patch: Partial<StudyLayout>) => void;
  onSearch: () => void;
  onExit: () => void;
  /** The heading the reader was at on the page: the window opens there. */
  startId: string | null;
  /** Another chapter shows from the top. */
  chapterId: string;
  /** The chapter list, for the left column. */
  chapters: ReactNode;
  /** "On this page", for the right column; null when the chapter has none. */
  outline: ReactNode | null;
  /** The chapter. */
  children: ReactNode;
}) {
  const ref = useModalDialog();
  const titleId = useId();
  const [scroller, setScroller] = useState<HTMLElement | null>(null);
  const shownChapter = useRef(chapterId);
  const openedAt = useRef(startId);

  // Open where the reader was, with the text focused so the arrow and page keys scroll it.
  useEffect(() => {
    if (openedAt.current) document.getElementById(openedAt.current)?.scrollIntoView?.({ block: "start" });
    scroller?.focus({ preventScroll: true });
  }, [scroller]);

  // Previous/next shows another chapter: it starts at its top.
  useEffect(() => {
    if (shownChapter.current === chapterId) return;
    shownChapter.current = chapterId;
    scroller?.scrollTo?.({ top: 0 });
  }, [chapterId, scroller]);

  const wide = layout.width === "wide";
  const hasOutline = outline !== null;

  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      onCancel={(event) => {
        event.preventDefault(); // Escape: leave through our state so the page stays the one source of truth
        onExit();
      }}
      className="fixed inset-0 m-0 h-dvh max-h-none w-dvw max-w-none overflow-hidden border-0 bg-background p-0 text-foreground"
    >
      <div className="flex h-full flex-col">
        <header className="relative flex shrink-0 items-center gap-1 border-b border-border px-2 py-1.5 sm:gap-2 sm:px-3">
          <ReadingProgress scroller={scroller} className="absolute inset-x-0 -bottom-px z-10 h-0.5" />
          <Button
            variant="ghost"
            size="icon-lg"
            className="hidden md:inline-flex"
            aria-expanded={layout.readingChapters}
            aria-label={layout.readingChapters ? "Hide chapters" : "Show chapters"}
            title={layout.readingChapters ? "Hide chapters" : "Show chapters"}
            onClick={() => onLayout({ readingChapters: !layout.readingChapters })}
          >
            {layout.readingChapters ? <PanelLeftClose aria-hidden /> : <PanelLeftOpen aria-hidden />}
          </Button>
          <div className="min-w-0 flex-1 px-1">
            <h2 id={titleId} className="truncate text-sm font-medium">
              {title}
            </h2>
            <p className="text-xs text-muted-foreground">Chapter {position}</p>
          </div>
          {previousHref ? (
            <Link
              href={previousHref}
              aria-label="Previous chapter"
              title="Previous chapter"
              className={buttonVariants({ variant: "ghost", size: "icon-lg" })}
            >
              <ChevronLeft aria-hidden />
            </Link>
          ) : (
            <Button variant="ghost" size="icon-lg" disabled aria-label="Previous chapter">
              <ChevronLeft aria-hidden />
            </Button>
          )}
          {nextHref ? (
            <Link
              href={nextHref}
              aria-label="Next chapter"
              title="Next chapter"
              className={buttonVariants({ variant: "ghost", size: "icon-lg" })}
            >
              <ChevronRight aria-hidden />
            </Link>
          ) : (
            <Button variant="ghost" size="icon-lg" disabled aria-label="Next chapter">
              <ChevronRight aria-hidden />
            </Button>
          )}
          <Button
            variant="ghost"
            size="icon-lg"
            aria-label="Search chapters"
            title="Search chapters"
            onClick={onSearch}
          >
            <Search aria-hidden />
          </Button>
          <Button
            variant={wide ? "secondary" : "ghost"}
            size="lg"
            className="hidden sm:inline-flex"
            aria-pressed={wide}
            title="Let the text run across most of the screen"
            onClick={() => onLayout({ width: wide ? "comfortable" : "wide" })}
          >
            <MoveHorizontal aria-hidden />
            Wide
          </Button>
          {hasOutline ? (
            <Button
              variant="ghost"
              size="icon-lg"
              className="hidden lg:inline-flex"
              aria-expanded={layout.readingOutline}
              aria-label={layout.readingOutline ? "Hide outline" : "Show outline"}
              title={layout.readingOutline ? "Hide outline" : "Show outline"}
              onClick={() => onLayout({ readingOutline: !layout.readingOutline })}
            >
              {layout.readingOutline ? <PanelRightClose aria-hidden /> : <PanelRightOpen aria-hidden />}
            </Button>
          ) : null}
          <Button variant="outline" size="lg" aria-label="Exit reading mode" onClick={onExit}>
            <Minimize2 aria-hidden />
            <span className="hidden sm:inline">Exit</span>
          </Button>
        </header>

        <div className="flex min-h-0 flex-1">
          {layout.readingChapters ? (
            <aside className="hidden w-72 shrink-0 overflow-y-auto border-r border-border p-3 md:block">
              {chapters}
            </aside>
          ) : null}
          <section
            ref={setScroller}
            // biome-ignore lint/a11y/noNoninteractiveTabindex: the text scrolls, so keyboard users must be able to focus it to scroll it (WCAG 2.1.1)
            tabIndex={0}
            aria-label="Chapter text"
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
