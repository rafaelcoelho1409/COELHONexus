"use client";

/** The Pages view: ingested pages read the way a Study chapter is. The page list on the left (filter, tier
 * chips, sections in the order of the original documentation), the page itself in the middle with an outline beside it, search
 * across every page, and a reading window that gives the page the whole screen. Both side columns fold away (and stay folded,
 * per browser). While a run is on screen the pages are the run's own, so Agentic AI's output shows as it is published. */

import {
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  Maximize2,
  PanelLeftClose,
  PanelLeftOpen,
  PanelRightClose,
  PanelRightOpen,
  Search,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { flushSync } from "react-dom";

import { useScrollSpy } from "@/shared/lib/use-scroll-spy";
import { Markdown, markdownToc } from "@/shared/markdown";

import { explorerTierValues, filterEntries, groupEntries } from "../domain/ingestion";
import { dropLeadingTitle, pageNeighbours, pageTitle } from "../domain/materials";
import type { PageSearchHit } from "../domain/materials-search";
import type { Manifest, PageVersion } from "../entities";
import { usePageBody, useRunPageBody } from "../hooks/use-dd-data";
import { useMaterialsLayout } from "../hooks/use-study-layout";
import { ActionButton } from "./action-button";
import { MaterialsArticle, type PreviewMode } from "./materials-article";
import { MaterialsPageList } from "./materials-page-list";
import { MaterialsReadingMode } from "./materials-reading-mode";
import { MaterialsSearch } from "./materials-search";
import { ReadingProgress } from "./reading-progress";
import { StageBar } from "./stage-bar";
import { StudyOutline } from "./study-outline";

/** The page's grid from 1024px up for each way of folding the two side columns (whole class names, so Tailwind sees them). */
const COLUMNS = {
  both: "lg:grid-cols-[16rem_minmax(0,1fr)_14rem]",
  pagesOnly: "lg:grid-cols-[16rem_minmax(0,1fr)]",
  outlineOnly: "lg:grid-cols-[minmax(0,1fr)_14rem]",
  neither: "lg:grid-cols-[minmax(0,1fr)]",
} as const;

/** A column that stays in view while the page scrolls, and scrolls on its own when it is taller than the screen. */
const STICKY_COLUMN =
  "lg:sticky lg:top-[calc(var(--chrome-top)+1.5rem)] lg:max-h-[calc(100dvh-var(--chrome-top)-2.5rem)] lg:self-start lg:overflow-y-auto";

/** Arrow keys step through pages, unless the reader is typing somewhere. */
function isTyping(target: EventTarget | null): boolean {
  return (
    target instanceof HTMLElement &&
    (target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName))
  );
}

export function MaterialsView({
  slug,
  info,
  manifest,
  runId = null,
  loadError = false,
  active = true,
  controlsInNavbar = false,
  selectionPending = false,
}: {
  slug: string;
  info?: string;
  manifest: Manifest | null;
  runId?: string | null;
  /** The saved manifest request failed and no live run manifest is available. */
  loadError?: boolean;
  /** The Materials tab is the one on show: the shortcuts (Ctrl+K, the arrow keys) belong to it only then. */
  active?: boolean;
  /** The Pages stage supplies the third navbar slot; embedded readers keep controls in the page header. */
  controlsInNavbar?: boolean;
  /** Extraction or the selector is still running; only approved pages are shown as they arrive. */
  selectionPending?: boolean;
}) {
  const { layout, update } = useMaterialsLayout();
  const [mode, setMode] = useState<PreviewMode>("rendered");
  const [version, setVersion] = useState<PageVersion>("processed");
  const [query, setQuery] = useState("");
  const [tiers, setTiers] = useState<ReadonlySet<string>>(new Set());
  const [selectedIdx, setSelectedIdx] = useState<number | null>(null);
  // The reading window: null while closed, else the heading the reader was at on the page.
  const [reading, setReading] = useState<{ startId: string | null } | null>(null);
  const [searchOpen, setSearchOpen] = useState(false);
  // Counts the search hits chosen: a hit on the page already open still scrolls to its heading.
  const [jump, setJump] = useState(0);
  const filterRef = useRef<HTMLInputElement | null>(null);
  const articleRef = useRef<HTMLElement | null>(null);
  const pendingHeading = useRef<string | null>(null);

  const entries = useMemo(() => manifest?.entries ?? [], [manifest]);
  const selectedEntries = useMemo(
    () => entries.filter(
      (entry) => entry.page_selection?.status === "selected" && !entry.page_selection.needs_review,
    ),
    [entries],
  );
  const baseEntries = selectedEntries;
  const filtered = useMemo(
    () =>
      filterEntries(baseEntries, tiers, query)
        .map((index) => baseEntries[index])
        .filter((entry) => entry != null),
    [baseEntries, tiers, query],
  );
  const groups = useMemo(() => groupEntries(filtered), [filtered]);
  const selected = useMemo(() => {
    if (selectedIdx != null && baseEntries.some((entry) => entry.idx === selectedIdx)) return selectedIdx;
    return filtered[0]?.idx ?? null;
  }, [baseEntries, filtered, selectedIdx]);
  const selectedEntry = entries.find((entry) => entry.idx === selected) ?? null;
  const neighbours = pageNeighbours(filtered, selected);
  const goPrevious = neighbours.previous === null ? null : () => setSelectedIdx(neighbours.previous);
  const goNext = neighbours.next === null ? null : () => setSelectedIdx(neighbours.next);
  const place = neighbours.position < 0 ? null : `${neighbours.position + 1} of ${filtered.length}`;

  const originalKey = selectedEntry?.source_key || selectedEntry?.raw_key;
  const hasVersions = Boolean(
    selectedEntry &&
      originalKey &&
      selectedEntry.key &&
      selectedEntry.key !== originalKey &&
      (runId || manifest?.agentic_run_id),
  );
  const selectedVersion = hasVersions ? version : "processed";
  const canonicalPage = usePageBody(runId ? null : slug, selected, selectedVersion);
  const runPage = useRunPageBody(runId, selected, selectedEntry?.key ?? null, selectedVersion);
  const page = runId ? runPage : canonicalPage;
  const text = page.data ?? "";

  // Outline and scroll-spy. The text is drawn in one place at a time (the page, or the reading window), so the spy
  // watches the headings afresh whenever it moves.
  const title = selectedEntry ? pageTitle(selectedEntry) : "";
  const plugins = useMemo(() => [dropLeadingTitle(title)], [title]);
  const rendered = mode === "rendered";
  const outline = useMemo(() => (rendered ? markdownToc(text, plugins) : []), [rendered, text, plugins]);
  const outlineIds = useMemo(() => outline.map((entry) => entry.id), [outline]);
  const activeId = useScrollSpy(outlineIds, reading !== null);
  const hasOutline = outline.length >= 2;

  // Leaving the reading window lands the page on the heading the reader reached in it (the page's text is drawn again first).
  const exitReading = () => {
    flushSync(() => setReading(null));
    if (activeId) document.getElementById(activeId)?.scrollIntoView?.({ block: "start" });
  };

  // Another page starts at its top: when the reader is further down the document than the article begins, bring it back.
  const shown = useRef(selected);
  useEffect(() => {
    if (shown.current === selected) return;
    shown.current = selected;
    const article = articleRef.current;
    if (article && article.getBoundingClientRect().top < 0) article.scrollIntoView?.({ block: "start" });
  }, [selected]);

  // After a search hit jumps to a heading, scroll there once that page's text is drawn.
  // biome-ignore lint/correctness/useExhaustiveDependencies: scrolls once per hit chosen, when the text of its page is drawn
  useEffect(() => {
    const id = pendingHeading.current;
    if (!id || !rendered || text === "") return;
    const heading = document.getElementById(id);
    if (!heading) return;
    pendingHeading.current = null;
    heading.scrollIntoView?.({ block: "start" });
  }, [jump, text, rendered]);

  const chooseHit = (hit: PageSearchHit) => {
    setSearchOpen(false);
    // A filter that hides the page would leave it open but out of the list and out of reach of previous/next.
    setQuery("");
    setTiers(new Set());
    setMode("rendered");
    setSelectedIdx(hit.idx);
    pendingHeading.current = hit.headingId ?? null;
    setJump((count) => count + 1);
  };

  const step = (delta: 1 | -1) => {
    const target = delta > 0 ? neighbours.next : neighbours.previous;
    if (target !== null) setSelectedIdx(target);
  };

  const firstPage = filtered[0]?.idx ?? null;
  const lastPage = filtered[filtered.length - 1]?.idx ?? null;
  const panelToggles = (
    <>
      <ActionButton
        iconOnly
        aria-expanded={layout.chapters}
        aria-label={layout.chapters ? "Hide pages" : "Show pages"}
        title={layout.chapters ? "Hide pages" : "Show pages"}
        onClick={() => update({ chapters: !layout.chapters })}
      >
        {layout.chapters ? <PanelLeftClose aria-hidden /> : <PanelLeftOpen aria-hidden />}
      </ActionButton>
      <ActionButton
        iconOnly
        aria-expanded={layout.outline}
        aria-label={layout.outline ? "Hide outline" : "Show outline"}
        title={layout.outline ? "Hide outline" : "Show outline"}
        onClick={() => update({ outline: !layout.outline })}
      >
        {layout.outline ? <PanelRightClose aria-hidden /> : <PanelRightOpen aria-hidden />}
      </ActionButton>
    </>
  );
  const pageControls = (
    <div className="flex shrink-0 flex-wrap items-center justify-end gap-1">
      {controlsInNavbar ? panelToggles : null}
      <span className="mr-1 text-xs tabular-nums text-muted-foreground">
        {neighbours.position < 0 ? "0 / 0" : `${neighbours.position + 1} / ${filtered.length}`}
      </span>
      <ActionButton
        iconOnly
        aria-label="First page"
        title="First page"
        disabled={firstPage === null || selected === firstPage}
        onClick={() => firstPage !== null && setSelectedIdx(firstPage)}
      >
        <ChevronsLeft aria-hidden />
      </ActionButton>
      <ActionButton
        iconOnly
        aria-label="Previous page"
        title="Previous page"
        disabled={goPrevious === null}
        onClick={() => goPrevious?.()}
      >
        <ChevronLeft aria-hidden />
      </ActionButton>
      <ActionButton
        iconOnly
        aria-label="Next page"
        title="Next page"
        disabled={goNext === null}
        onClick={() => goNext?.()}
      >
        <ChevronRight aria-hidden />
      </ActionButton>
      <ActionButton
        iconOnly
        aria-label="Last page"
        title="Last page"
        disabled={lastPage === null || selected === lastPage}
        onClick={() => lastPage !== null && setSelectedIdx(lastPage)}
      >
        <ChevronsRight aria-hidden />
      </ActionButton>
      <ActionButton
        aria-haspopup="dialog"
        title="Read this page on the whole screen"
        disabled={selectedEntry === null || reading !== null}
        onClick={() => setReading({ startId: activeId })}
      >
        <Maximize2 aria-hidden />
        Reading mode
      </ActionButton>
    </div>
  );
  const stageBar = controlsInNavbar ? (
    <StageBar stage="pages" slug={slug} info={info} end={pageControls} />
  ) : null;

  // Ctrl/Cmd+K opens the search; the left and right arrow keys turn pages. Only while this tab is the one on show.
  useEffect(() => {
    if (!active) return;
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setSearchOpen(true);
        return;
      }
      if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
      if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
      if (isTyping(event.target)) return;
      const target = event.key === "ArrowRight" ? neighbours.next : neighbours.previous;
      if (target !== null) setSelectedIdx(target);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [active, neighbours.next, neighbours.previous]);

  if (entries.length === 0 || selectedEntries.length === 0) {
    return (
      <>
        {stageBar}
        <section
          aria-label="Pages"
          className="rounded-md border border-border bg-card p-8 text-center text-sm text-muted-foreground"
        >
          {loadError ? (
            <p role="alert">Could not load the ingested pages. Refresh the page to try again.</p>
          ) : manifest ? (
            entries.length === 0
              ? "This extraction has no pages."
              : selectionPending
                ? "The AI Agent is assessing extracted pages. Approved pages will appear here."
                : "The AI Agent has not approved any pages for display. The extracted source remains saved."
          ) : (
            "No extracted pages yet."
          )}
        </section>
      </>
    );
  }

  const pageList = (
    <MaterialsPageList
      groups={groups}
      selected={selected}
      shown={filtered.length}
      total={selectedEntries.length}
      query={query}
      onQuery={setQuery}
      tierValues={explorerTierValues(selectedEntries)}
      tiers={tiers}
      onToggleTier={(tier) =>
        setTiers((current) => {
          const next = new Set(current);
          if (!next.delete(tier)) next.add(tier);
          return next;
        })
      }
      onPick={setSelectedIdx}
      onMove={step}
      filterRef={filterRef}
      emptyHint="No approved pages match the current filters."
    />
  );
  const outlineView = hasOutline ? <StudyOutline entries={outline} activeId={activeId} /> : null;

  // The page's text, or what stands in for it.
  const body =
    selected === null ? null : page.isPending ? (
      <p className="text-sm text-muted-foreground">Loading page…</p>
    ) : page.isError ? (
      <p role="alert" className="text-sm text-destructive-text">
        Page unavailable.
      </p>
    ) : rendered ? (
      <section aria-label="Rendered page">
        {/* Crawled pages carry raw HTML (tables, mostly): allowed, and sanitised before anything else. */}
        <Markdown text={text} allowHtml remarkPlugins={plugins} />
      </section>
    ) : (
      <section aria-label="Page source">
        <pre className="font-mono text-xs break-words whitespace-pre-wrap text-foreground">{text}</pre>
      </section>
    );

  const showOutlineColumn = layout.outline && hasOutline;
  const columns = layout.chapters
    ? showOutlineColumn
      ? COLUMNS.both
      : COLUMNS.pagesOnly
    : showOutlineColumn
      ? COLUMNS.outlineOnly
      : COLUMNS.neither;

  const pageToolbar = (
    <div className="flex shrink-0 flex-wrap items-center justify-end gap-1">
      {controlsInNavbar ? null : pageControls}
      {layout.chapters ? null : (
        <ActionButton
          iconOnly
          aria-label="Search pages"
          title="Search pages"
          onClick={() => setSearchOpen(true)}
        >
          <Search aria-hidden />
        </ActionButton>
      )}
      {controlsInNavbar ? null : panelToggles}
    </div>
  );

  return (
    <>
      {stageBar}
      <section
        aria-label="Pages"
        onKeyDown={(event) => {
          if (event.key === "/" && !isTyping(event.target)) {
            event.preventDefault();
            filterRef.current?.focus();
          }
        }}
      >
        <ReadingProgress />
        <div className={`flex flex-col gap-4 lg:grid ${columns}`}>
          {layout.chapters ? (
            <aside className={`flex min-w-0 flex-col gap-3 ${STICKY_COLUMN}`}>
              <ActionButton
                className="w-full justify-start"
                onClick={() => setSearchOpen(true)}
                aria-haspopup="dialog"
                disabled={entries.length === 0}
              >
                <Search aria-hidden />
                Search pages
                <kbd className="ml-auto font-mono text-xs text-muted-foreground">Ctrl K</kbd>
              </ActionButton>
              <div className="rounded-md border border-border bg-card p-3">{pageList}</div>
            </aside>
          ) : null}

          {/* In the reading window the text is drawn there, not here: one copy at a time, so a heading's id is never twice on the page. */}
          <MaterialsArticle
            ref={articleRef}
            entry={selectedEntry}
            place={place}
            framed
            mode={mode}
            onMode={setMode}
            version={selectedVersion}
            onVersion={setVersion}
            hasVersions={hasVersions}
            toolbar={pageToolbar}
          >
            {reading ? null : body}
          </MaterialsArticle>

          {showOutlineColumn ? (
            <div className="hidden lg:block">
              <div className={`${STICKY_COLUMN} overflow-y-auto`}>{outlineView}</div>
            </div>
          ) : null}
        </div>

        {reading && selectedEntry ? (
          <MaterialsReadingMode
            title={title}
            position={`${neighbours.position + 1} / ${filtered.length}`}
            onPrevious={goPrevious}
            onNext={goNext}
            layout={layout}
            onLayout={update}
            onSearch={() => setSearchOpen(true)}
            onExit={exitReading}
            startId={reading.startId}
            pageIdx={selectedEntry.idx}
            pages={pageList}
            outline={outlineView}
          >
            <MaterialsArticle
              entry={selectedEntry}
              place={place}
              framed={false}
              mode={mode}
              onMode={setMode}
              version={selectedVersion}
              onVersion={setVersion}
              hasVersions={hasVersions}
            >
              {body}
            </MaterialsArticle>
          </MaterialsReadingMode>
        ) : null}

        {searchOpen ? (
          <MaterialsSearch
            slug={slug}
            runId={runId}
            entries={selectedEntries}
            onChoose={chooseHit}
            onClose={() => setSearchOpen(false)}
          />
        ) : null}
      </section>
    </>
  );
}
