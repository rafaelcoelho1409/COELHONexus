"use client";

/** What the Agentic AI step found on this page, what it fixed, and which source text it retained where evidence was
 * incomplete. Shown only for a page the run processed. A page with findings opens the report in place. */

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
  /** Open the report when the page has retained findings. */
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

  return (
    <PageQualityDisplay
      report={quality.data}
      unit={unit}
      page={page}
      defaultOpen={defaultOpen}
      autonomous
    />
  );
}
