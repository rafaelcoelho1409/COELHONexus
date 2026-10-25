"use client";

/** An ingested page, rendered, in a side sheet that steps through the manifest with the arrow keys. Opened from
 * the ingestion explorer and from a chapter's "Sources for this section". */

import { ChevronLeft, ChevronRight } from "lucide-react";
import { useEffect } from "react";

import { Markdown } from "@/shared/markdown";
import { Button } from "@/shared/ui/button";

import { fmtBytes } from "../domain/format";
import type { ManifestEntry } from "../entities";
import { usePageBody } from "../hooks/use-dd-data";
import { SideSheet } from "./side-sheet";

/** Arrow keys step through pages, unless the reader is typing somewhere. */
function isTyping(target: EventTarget | null): boolean {
  return (
    target instanceof HTMLElement &&
    (target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName))
  );
}

export function FileDrawer({
  slug,
  entries,
  position,
  onMove,
  onClose,
}: {
  slug: string;
  entries: readonly ManifestEntry[];
  /** Index in `entries` of the page shown. */
  position: number;
  onMove: (position: number) => void;
  onClose: () => void;
}) {
  const entry = entries[position];
  const page = usePageBody(slug, entry?.idx ?? null);
  const last = entries.length - 1;

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (isTyping(event.target)) return;
      if (event.key === "ArrowLeft" && position > 0) onMove(position - 1);
      else if (event.key === "ArrowRight" && position < last) onMove(position + 1);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [position, last, onMove]);

  if (!entry) return null;
  return (
    <SideSheet
      title={entry.title || entry.slug || `Page ${entry.idx}`}
      closeLabel="Close page"
      scrollKey={entry.idx}
      onClose={onClose}
      subtitle={
        <span className="font-mono text-xs text-muted-foreground">
          {[entry.tier, fmtBytes(entry.bytes ?? 0), `${position + 1} of ${entries.length}`]
            .filter(Boolean)
            .join(" · ")}
        </span>
      }
    >
      <div className="mb-4 flex items-center gap-2">
        <Button variant="outline" size="lg" onClick={() => onMove(position - 1)} disabled={position <= 0}>
          <ChevronLeft aria-hidden />
          Previous
        </Button>
        <Button variant="outline" size="lg" onClick={() => onMove(position + 1)} disabled={position >= last}>
          Next
          <ChevronRight aria-hidden />
        </Button>
      </div>
      {page.isPending ? (
        <p className="text-sm text-muted-foreground">Loading page…</p>
      ) : page.isError || page.data === null ? (
        <p role="alert" className="text-sm text-destructive-text">
          This page could not be loaded.
        </p>
      ) : (
        // Crawled pages carry raw HTML (tables, mostly): allowed, and sanitised before anything else.
        <Markdown text={page.data} allowHtml />
      )}
    </SideSheet>
  );
}
