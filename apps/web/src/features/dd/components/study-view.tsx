"use client";

/** Study stage: the chapter list, the chapter itself with an outline beside it, search across chapters, and the
 * sources a chapter cites. Reading is passive — runs live on the synth stage. Both side columns fold away (and stay
 * folded, per browser), and the reading window gives the chapter the whole screen. */

import { useQuery } from "@tanstack/react-query";
import {
  Maximize2,
  PanelLeftClose,
  PanelLeftOpen,
  PanelRightClose,
  PanelRightOpen,
  Search,
} from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { flushSync } from "react-dom";

import {
  getDdSynthSlugStudyChapterIdArtifactArtifactNameQueryKey,
  getDdSynthSlugStudyChaptersQueryKey,
} from "@/shared/api";
import { useScrollSpy } from "@/shared/lib/use-scroll-spy";
import { Markdown, markdownToc } from "@/shared/markdown";
import { Button } from "@/shared/ui/button";
import { buttonVariants } from "@/shared/ui/button-variants";

import { fetchChapterMarkdown, fetchSynthChapters } from "../api";
import { CHAPTER_PLUGINS, unwrapChapterFence } from "../domain/chapter-markdown";
import { humanizeChapterId } from "../domain/chapters";
import type { SearchHit } from "../domain/study-search";
import { useSourceSheet } from "../hooks/use-source-sheet";
import { useStudyLayout } from "../hooks/use-study-layout";
import { CHAPTER_ARTIFACT, chapterStudyHref, stageHref } from "../params";
import { FileDrawer } from "./file-drawer";
import { ReadingProgress } from "./reading-progress";
import { NoFrameworkCard } from "./stage-parts";
import { StudyArticle } from "./study-article";
import { StudyChapterList } from "./study-chapter-list";
import { StudyOutline } from "./study-outline";
import { StudyReadingMode } from "./study-reading-mode";
import { StudySearch } from "./study-search";

const CHAPTER_STALE_MS = 60_000;

/** The page's grid from 1024px up for each way of folding the two side columns (whole class names, so Tailwind sees them). */
const COLUMNS = {
  both: "lg:grid-cols-[16rem_minmax(0,1fr)_14rem]",
  chaptersOnly: "lg:grid-cols-[16rem_minmax(0,1fr)]",
  outlineOnly: "lg:grid-cols-[minmax(0,1fr)_14rem]",
  neither: "lg:grid-cols-[minmax(0,1fr)]",
} as const;

/** A column that stays in view while the chapter scrolls, and scrolls on its own when it is taller than the screen. */
const STICKY_COLUMN = "lg:sticky lg:top-20 lg:max-h-[calc(100dvh-6rem)] lg:self-start lg:overflow-y-auto";

/** A chapter's source files are buttons that open the ingested page; the rest of the code renders as usual. */
function sourceComponents(onOpen: (basename: string) => void) {
  return {
    code({
      node,
      children,
      className,
    }: {
      node?: { properties?: Record<string, unknown> };
      children?: React.ReactNode;
      className?: string;
    }) {
      const basename = node?.properties?.["data-source"];
      if (typeof basename === "string") {
        return (
          <button
            type="button"
            className="md-source-file"
            title="Open this source page"
            onClick={() => onOpen(basename)}
          >
            {children}
          </button>
        );
      }
      return <code className={className}>{children}</code>;
    },
  };
}

function StudyReader({ slug }: { slug: string }) {
  const router = useRouter();
  const wanted = useSearchParams().get("chapter");
  const { layout, update } = useStudyLayout();
  // The reading window: null while closed, else the heading the reader was at on the page.
  const [reading, setReading] = useState<{ startId: string | null } | null>(null);

  const chapters = useQuery({
    queryKey: getDdSynthSlugStudyChaptersQueryKey({ path: { slug } }),
    retry: false,
    queryFn: () => fetchSynthChapters(slug),
  });
  const cells = (chapters.data?.chapters ?? []).slice().sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
  const current = cells.find((c) => c.id === wanted) ?? cells.find((c) => c.rendered) ?? cells[0] ?? null;
  const currentIndex = current ? cells.findIndex((cell) => cell.id === current.id) : -1;

  const artifact = useQuery({
    queryKey: getDdSynthSlugStudyChapterIdArtifactArtifactNameQueryKey({
      path: { slug, chapter_id: current?.id ?? "", artifact_name: CHAPTER_ARTIFACT },
    }),
    enabled: Boolean(current?.rendered),
    retry: false,
    staleTime: CHAPTER_STALE_MS,
    queryFn: () => fetchChapterMarkdown(slug, current?.id ?? ""),
  });
  const text = useMemo(() => unwrapChapterFence(artifact.data ?? ""), [artifact.data]);

  // Outline and scroll-spy. The text is drawn in one place at a time (the page, or the reading window), so the spy
  // watches the headings afresh whenever it moves.
  const outline = useMemo(() => markdownToc(text, CHAPTER_PLUGINS), [text]);
  const outlineIds = useMemo(() => outline.map((entry) => entry.id), [outline]);
  const activeId = useScrollSpy(outlineIds, reading !== null);
  const hasOutline = outline.length >= 2;

  // Leaving the reading window lands the page on the heading the reader reached in it (the page's text is drawn again first).
  const exitReading = () => {
    flushSync(() => setReading(null));
    if (activeId) document.getElementById(activeId)?.scrollIntoView?.({ block: "start" });
  };

  // After a search jumps to a heading in another chapter, scroll there once that chapter has rendered.
  const currentId = current?.id;
  const rendered = artifact.isSuccess;
  // biome-ignore lint/correctness/useExhaustiveDependencies: scrolls once per chapter that finishes rendering
  useEffect(() => {
    if (!rendered) return;
    const id = decodeURIComponent(window.location.hash.slice(1));
    if (id) document.getElementById(id)?.scrollIntoView({ block: "start" });
  }, [currentId, rendered]);

  // Search: Ctrl/Cmd+K anywhere on the page
  const [searchOpen, setSearchOpen] = useState(false);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setSearchOpen(true);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);
  const choose = useCallback(
    (hit: SearchHit) => {
      setSearchOpen(false);
      router.push(`${chapterStudyHref(slug, hit.chapterId)}${hit.headingId ? `#${hit.headingId}` : ""}`);
    },
    [router, slug],
  );

  // Sources: the page a citation names opens in a side sheet that steps through the whole manifest.
  const sources = useSourceSheet(slug);
  const components = useMemo(() => sourceComponents(sources.open), [sources.open]);

  const chapterList = (
    <StudyChapterList
      slug={slug}
      cells={cells}
      currentId={current?.id ?? null}
      totalWallMs={chapters.data?.study_total_wall_ms ?? 0}
    />
  );
  const outlineView = hasOutline ? <StudyOutline entries={outline} activeId={activeId} /> : null;

  // The chapter's text, or what stands in for it.
  const body = !current ? null : !current.rendered ? (
    <p className="text-sm text-muted-foreground">
      This chapter is pending synthesis. Run it on the synth stage, then reopen study.
    </p>
  ) : artifact.isPending ? (
    <p className="text-sm text-muted-foreground">Loading chapter…</p>
  ) : artifact.isError ? (
    <p role="alert" className="text-sm text-destructive-text">
      This chapter could not be loaded. Try reloading.
    </p>
  ) : (
    <>
      {sources.notInLibrary ? (
        <p role="status" className="mb-3 text-sm text-warning-text">
          That source page is not in the ingested library any more.
        </p>
      ) : null}
      <Markdown text={text} remarkPlugins={CHAPTER_PLUGINS} components={components} />
    </>
  );

  const showOutlineColumn = layout.outline && hasOutline;
  const columns = layout.chapters
    ? showOutlineColumn
      ? COLUMNS.both
      : COLUMNS.chaptersOnly
    : showOutlineColumn
      ? COLUMNS.outlineOnly
      : COLUMNS.neither;

  // On the right of the chapter's title: fold either side column, or read the chapter on the whole screen.
  const toolbar = (
    <div className="flex shrink-0 items-center gap-1">
      <Button
        variant="ghost"
        size="icon-lg"
        aria-expanded={layout.chapters}
        aria-label={layout.chapters ? "Hide chapters" : "Show chapters"}
        title={layout.chapters ? "Hide chapters" : "Show chapters"}
        onClick={() => update({ chapters: !layout.chapters })}
      >
        {layout.chapters ? <PanelLeftClose aria-hidden /> : <PanelLeftOpen aria-hidden />}
      </Button>
      {hasOutline ? (
        <Button
          variant="ghost"
          size="icon-lg"
          className="hidden lg:inline-flex"
          aria-expanded={layout.outline}
          aria-label={layout.outline ? "Hide outline" : "Show outline"}
          title={layout.outline ? "Hide outline" : "Show outline"}
          onClick={() => update({ outline: !layout.outline })}
        >
          {layout.outline ? <PanelRightClose aria-hidden /> : <PanelRightOpen aria-hidden />}
        </Button>
      ) : null}
      {current?.rendered ? (
        <Button
          variant="outline"
          size="lg"
          aria-haspopup="dialog"
          title="Read this chapter on the whole screen"
          onClick={() => setReading({ startId: activeId })}
        >
          <Maximize2 aria-hidden />
          Reading mode
        </Button>
      ) : null}
    </div>
  );

  return (
    <>
      <ReadingProgress />
      <div className={`flex flex-col gap-4 lg:grid ${columns}`}>
        {layout.chapters ? (
          <aside className={`flex flex-col gap-3 ${STICKY_COLUMN}`}>
            <Button variant="outline" size="lg" onClick={() => setSearchOpen(true)} aria-haspopup="dialog">
              <Search aria-hidden />
              Search chapters
              <kbd className="ml-auto font-mono text-xs text-muted-foreground">Ctrl K</kbd>
            </Button>
            {chapterList}
            <Link
              href={stageHref("pipeline", slug)}
              className={buttonVariants({ variant: "outline", size: "lg" })}
            >
              Pipeline
            </Link>
          </aside>
        ) : null}

        {/* In the reading window the text is drawn there, not here: one copy at a time, so a heading's id is never twice on the page. */}
        <StudyArticle chapter={current} hasChapters={cells.length > 0} framed toolbar={toolbar}>
          {reading ? null : body}
        </StudyArticle>

        {showOutlineColumn ? (
          <div className="hidden lg:block">
            <div className={STICKY_COLUMN}>{outlineView}</div>
          </div>
        ) : null}
      </div>

      {reading && current ? (
        <StudyReadingMode
          title={current.title || humanizeChapterId(current.id)}
          position={`${currentIndex + 1} / ${cells.length}`}
          previousHref={currentIndex > 0 ? chapterStudyHref(slug, cells[currentIndex - 1]?.id ?? "") : null}
          nextHref={
            currentIndex >= 0 && currentIndex < cells.length - 1
              ? chapterStudyHref(slug, cells[currentIndex + 1]?.id ?? "")
              : null
          }
          layout={layout}
          onLayout={update}
          onSearch={() => setSearchOpen(true)}
          onExit={exitReading}
          startId={reading.startId}
          chapterId={current.id}
          chapters={chapterList}
          outline={outlineView}
        >
          <StudyArticle chapter={current} hasChapters framed={false}>
            {body}
          </StudyArticle>
        </StudyReadingMode>
      ) : null}

      {searchOpen ? (
        <StudySearch
          slug={slug}
          chapters={cells.map((cell) => ({
            id: cell.id,
            title: cell.title || humanizeChapterId(cell.id),
            rendered: cell.rendered,
          }))}
          onChoose={choose}
          onClose={() => setSearchOpen(false)}
        />
      ) : null}
      {sources.isOpen ? (
        <FileDrawer
          slug={slug}
          entries={sources.entries}
          position={sources.position}
          onMove={sources.step}
          onClose={sources.close}
        />
      ) : null}
    </>
  );
}

export function StudyView({ slug }: { slug: string | null }) {
  return slug ? <StudyReader slug={slug} /> : <NoFrameworkCard />;
}
