"use client";

/** What the Agentic AI step found on this page and what it did about it: issues with the text that shows them, what was
 * fixed automatically, what still needs a reviewer and the fix it would suggest. Shown only for a page the run processed.
 * A page that needs review opens it (on the page, not in the reading window); any other page keeps it to one line. */

import { PageQualityDisplay } from "@/shared/page-quality/display";

import { usePageQuality } from "../hooks/use-bd-data";

export function PageQualityPanel({
  bookId,
  page,
  unit,
  enabled,
  defaultOpen,
}: {
  bookId: string;
  page: number;
  unit: string;
  enabled: boolean;
  /** Open the report when the page needs review. */
  defaultOpen: boolean;
}) {
  const quality = usePageQuality(bookId, page, enabled);
  if (!enabled) return null;
  if (quality.isPending) {
    return (
      <p role="status" className="mt-3 text-xs text-muted-foreground">
        Loading the quality report…
      </p>
    );
  }
  if (quality.isError || !quality.data) return null;

  return <PageQualityDisplay report={quality.data} unit={unit} page={page} defaultOpen={defaultOpen} />;
}
