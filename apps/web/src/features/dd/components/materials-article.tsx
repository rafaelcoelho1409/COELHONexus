"use client";

/** One ingested page on the Materials tab: where it is in the list, its title and facts, how it is shown (rendered or as
 * source) and the way to the pages on either side, then the text (or why there is none). The page frames it as a card
 * between the side columns; the reading window shows it bare, on the whole screen. */

import { ChevronLeft, ChevronRight, ExternalLink } from "lucide-react";
import type { ReactNode, Ref } from "react";

import { pageFacts, pageTitle, sourceHref } from "../domain/materials";
import type { ManifestEntry, PageVersion } from "../entities";
import { ActionButton } from "./action-button";
import { ToggleGroup } from "./toggle-group";

const PREVIEW_MODES = [
  { id: "rendered", label: "Rendered" },
  { id: "source", label: "Source" },
] as const;
const PAGE_VERSIONS = [
  { id: "processed", label: "AI processed" },
  { id: "original", label: "Original" },
] as const;
export type PreviewMode = (typeof PREVIEW_MODES)[number]["id"];

export function MaterialsArticle({
  ref,
  entry,
  place,
  framed,
  mode,
  onMode,
  version,
  onVersion,
  hasVersions,
  onPrevious,
  onNext,
  toolbar,
  quality,
  children,
}: {
  ref?: Ref<HTMLElement>;
  /** The page on show; null when there is none to pick. */
  entry: ManifestEntry | null;
  /** "3 of 24": where it is among the pages on show. */
  place: string | null;
  framed: boolean;
  mode: PreviewMode;
  onMode: (mode: PreviewMode) => void;
  version: PageVersion;
  onVersion: (version: PageVersion) => void;
  hasVersions: boolean;
  onPrevious: (() => void) | null;
  onNext: (() => void) | null;
  /** Page navigation and layout controls, shown in the framed page header. */
  toolbar?: ReactNode;
  /** Quality report for finalized Agentic AI pages. */
  quality?: ReactNode;
  /** The page's text, or the loading and error lines. */
  children: ReactNode;
}) {
  const href = entry ? sourceHref(entry) : null;
  return (
    <section
      ref={ref}
      aria-label="Page"
      className={`min-w-0 scroll-mt-[calc(var(--chrome-top)+1.5rem)] ${framed ? "rounded-md border border-border bg-card p-4 sm:p-5" : ""}`}
    >
      {entry ? (
        <>
          <header className="mb-4 border-b border-border pb-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                {place ? <p className="mono-label">Page {place}</p> : null}
                <h2
                  data-testid="materials-title"
                  className="mt-1 break-words text-2xl font-semibold tracking-tight"
                >
                  {pageTitle(entry)}
                </h2>
                <p className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
                  {pageFacts(entry).map((fact) => (
                    <span key={fact}>{fact}</span>
                  ))}
                  {href ? (
                    <a
                      href={href}
                      target="_blank"
                      rel="noreferrer"
                      className="inline-flex items-center gap-1 text-primary-text underline-offset-2 hover:underline"
                    >
                      Open original
                      <ExternalLink className="size-3.5" aria-hidden />
                      <span className="sr-only">(opens in a new tab)</span>
                    </a>
                  ) : null}
                </p>
              </div>
              {framed ? (
                <div className="flex shrink-0 flex-wrap items-center justify-end gap-1">
                  <ActionButton
                    iconOnly
                    aria-label="Previous page"
                    title="Previous page"
                    disabled={onPrevious === null}
                    onClick={() => onPrevious?.()}
                  >
                    <ChevronLeft aria-hidden />
                  </ActionButton>
                  <ActionButton
                    iconOnly
                    aria-label="Next page"
                    title="Next page"
                    disabled={onNext === null}
                    onClick={() => onNext?.()}
                  >
                    <ChevronRight aria-hidden />
                  </ActionButton>
                  {toolbar}
                </div>
              ) : null}
            </div>
            {quality}
          </header>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <ToggleGroup legend="Page shown as" options={PREVIEW_MODES} value={mode} onChange={onMode} />
            {hasVersions ? (
              <ToggleGroup
                legend="Page version"
                options={PAGE_VERSIONS}
                value={version}
                onChange={onVersion}
              />
            ) : null}
          </div>
          <div className="mt-4">{children}</div>
        </>
      ) : (
        <p className="text-sm text-muted-foreground">Select a page from the list.</p>
      )}
    </section>
  );
}
