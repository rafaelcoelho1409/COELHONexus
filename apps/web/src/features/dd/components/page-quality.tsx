"use client";

import { PageQualityDisplay } from "@/shared/page-quality/display";

import { usePageQuality } from "../hooks/use-dd-data";

/** DD's finalized ingestion page report, rendered with the shared BD quality panel. */
export function PageQualityPanel({
  slug,
  page,
  agenticRunId,
  enabled,
  defaultOpen = false,
}: {
  slug: string;
  page: number;
  agenticRunId: string;
  enabled: boolean;
  defaultOpen?: boolean;
}) {
  const quality = usePageQuality(slug, page, agenticRunId, enabled);
  if (!enabled) return null;
  if (quality.isPending) {
    return (
      <p role="status" className="mt-3 text-xs text-muted-foreground">
        Loading the quality report…
      </p>
    );
  }
  if (quality.isError || !quality.data) return null;
  return <PageQualityDisplay report={quality.data} unit="Page" page={page} defaultOpen={defaultOpen} />;
}
