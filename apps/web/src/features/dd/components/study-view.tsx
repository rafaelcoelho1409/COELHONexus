"use client";

/** Study stage: the chapter list, the chapter itself with an outline beside it, search across chapters, and the
 * sources a chapter cites. Reading is passive — runs live on the synth stage. */

import { useQuery } from "@tanstack/react-query";
import { Search } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  getDdSynthSlugStudyChapterIdArtifactArtifactNameQueryKey,
  getDdSynthSlugStudyChaptersQueryKey,
} from "@/shared/api";
import { useScrollSpy } from "@/shared/lib/use-scroll-spy";
import { Markdown, markdownToc } from "@/shared/markdown";
import { Badge } from "@/shared/ui/badge";
import { Button } from "@/shared/ui/button";
import { buttonVariants } from "@/shared/ui/button-variants";

import { fetchChapterMarkdown, fetchSynthChapters } from "../api";
import { CHAPTER_PLUGINS, unwrapChapterFence } from "../domain/chapter-markdown";
import { chapterFacts, humanizeChapterId } from "../domain/chapters";
import { fmtMs } from "../domain/format";
import type { SearchHit } from "../domain/study-search";
import { useSourceSheet } from "../hooks/use-source-sheet";
import { CHAPTER_ARTIFACT, chapterStudyHref, stageHref } from "../params";
import { FileDrawer } from "./file-drawer";
import { ReadingProgress } from "./reading-progress";
import { NoFrameworkCard } from "./stage-parts";
import { StudyOutline } from "./study-outline";
import { StudySearch } from "./study-search";

const CHAPTER_STALE_MS = 60_000;

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

  const chapters = useQuery({
    queryKey: getDdSynthSlugStudyChaptersQueryKey({ path: { slug } }),
    retry: false,
    queryFn: () => fetchSynthChapters(slug),
  });
  const cells = (chapters.data?.chapters ?? []).slice().sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
  const current = cells.find((c) => c.id === wanted) ?? cells.find((c) => c.rendered) ?? cells[0] ?? null;

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

  // Outline and scroll-spy
  const outline = useMemo(() => markdownToc(text, CHAPTER_PLUGINS), [text]);
  const outlineIds = useMemo(() => outline.map((entry) => entry.id), [outline]);
  const activeId = useScrollSpy(outlineIds);

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

  return (
    <>
      <ReadingProgress />
      <div className="flex flex-col gap-4 lg:grid lg:grid-cols-[16rem_minmax(0,1fr)_14rem]">
        <aside className="flex flex-col gap-3">
          <Button variant="outline" size="lg" onClick={() => setSearchOpen(true)} aria-haspopup="dialog">
            <Search aria-hidden />
            Search chapters
            <kbd className="ml-auto font-mono text-xs text-muted-foreground">Ctrl K</kbd>
          </Button>
          <nav aria-label="Chapters" className="rounded-md border border-border bg-card">
            <p className="flex items-center justify-between border-b border-border px-3 py-2 text-xs font-medium text-muted-foreground">
              <span>Chapters</span>
              {chapters.data && chapters.data.study_total_wall_ms > 0 ? (
                <span className="font-mono">{fmtMs(chapters.data.study_total_wall_ms)}</span>
              ) : null}
            </p>
            <ul>
              {cells.map((cell, index) => (
                <li key={cell.id}>
                  <Link
                    href={chapterStudyHref(slug, cell.id)}
                    aria-current={current?.id === cell.id ? "page" : undefined}
                    className={`flex min-h-9 items-center gap-2 px-3 text-sm ${
                      current?.id === cell.id
                        ? "bg-muted text-foreground"
                        : "text-muted-foreground hover:text-foreground"
                    }`}
                  >
                    <span className="font-mono text-xs">{index + 1}</span>
                    <span className="min-w-0 flex-1 truncate">
                      {cell.title || humanizeChapterId(cell.id)}
                    </span>
                    {cell.rendered ? (
                      cell.wall_ms ? (
                        <span className="font-mono text-xs">{fmtMs(cell.wall_ms)}</span>
                      ) : null
                    ) : (
                      <span className="text-xs">pending</span>
                    )}
                  </Link>
                </li>
              ))}
            </ul>
            {cells.length === 0 ? (
              <p className="px-3 py-3 text-sm text-muted-foreground">No chapters yet — run synth first.</p>
            ) : null}
          </nav>
          <Link
            href={stageHref("pipeline", slug)}
            className={buttonVariants({ variant: "outline", size: "lg" })}
          >
            Pipeline
          </Link>
        </aside>

        <section aria-label="Chapter" className="min-w-0 rounded-md border border-border bg-card p-5">
          {current ? (
            <>
              <header className="mb-5 border-b border-border pb-4">
                <h2 data-testid="study-title" className="text-2xl font-semibold tracking-tight">
                  {current.title || humanizeChapterId(current.id)}
                </h2>
                {current.rendered ? (
                  <p className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
                    {current.audit_passed ? (
                      <Badge className="bg-success/10 text-success-text">Audit passed</Badge>
                    ) : null}
                    {chapterFacts(current).map((fact) => (
                      <span key={fact}>{fact}</span>
                    ))}
                  </p>
                ) : null}
              </header>
              {!current.rendered ? (
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
              )}
            </>
          ) : cells.length > 0 ? (
            <p className="text-sm text-muted-foreground">Pick a chapter from the list.</p>
          ) : (
            <p className="text-sm text-muted-foreground">The plan is pending — nothing to study yet.</p>
          )}
        </section>

        <div className="hidden lg:block">
          <div className="sticky top-20">
            <StudyOutline entries={outline} activeId={activeId} />
          </div>
        </div>
      </div>

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
