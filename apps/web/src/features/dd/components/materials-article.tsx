"use client";

/** One ingested page on the Materials tab: where it is in the list, its title and facts, how it is shown (rendered or as
 * source) and the way to the pages on either side, then the text (or why there is none). The page frames it as a card
 * between the side columns; the reading window shows it bare, on the whole screen. */

import { ChevronLeft, ChevronRight, ExternalLink } from "lucide-react";
import type { ReactNode, Ref } from "react";

import { pageFacts, pageTitle, sourceHref } from "../domain/materials";
import type { ManifestEntry } from "../entities";
import { ActionButton } from "./action-button";
import { ToggleGroup } from "./toggle-group";

const PREVIEW_MODES = [
  { id: "rendered", label: "Rendered" },
  { id: "source", label: "Source" },
] as const;
export type PreviewMode = (typeof PREVIEW_MODES)[number]["id"];

export function MaterialsArticle({
  ref,
  entry,
  place,
  framed,
  mode,
  onMode,
  onPrevious,
  onNext,
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
  onPrevious: (() => void) | null;
  onNext: (() => void) | null;
  /** The page's text, or the loading and error lines. */
  children: ReactNode;
}) {
  const href = entry ? sourceHref(entry) : null;
  return (
    <section
      ref={ref}
      aria-label="Page"
      className={`min-w-0 scroll-mt-20 ${framed ? "rounded-md border border-border bg-card p-5" : ""}`}
    >
      {entry ? (
        <>
          <header className="mb-5 border-b border-border pb-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                {place ? <p className="text-xs tabular-nums text-muted-foreground">Page {place}</p> : null}
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
              <div className="flex shrink-0 flex-wrap items-center justify-end gap-1.5">
                <ToggleGroup legend="Page shown as" options={PREVIEW_MODES} value={mode} onChange={onMode} />
                {/* The reading window has previous/next on its own bar. */}
                {framed ? (
                  <>
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
                  </>
                ) : null}
              </div>
            </div>
          </header>
          {children}
        </>
      ) : (
        <p className="text-sm text-muted-foreground">Select a page from the list.</p>
      )}
    </section>
  );
}
