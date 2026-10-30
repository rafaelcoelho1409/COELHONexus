"use client";

/** The bar that appears while Library rows are selected: how many, Re-ingest (starts work again), and Delete — set apart in
 * danger styling, since it cannot be undone — and Clear. It sticks to the bottom of the screen. */

import { RefreshCw, Trash2, X } from "lucide-react";
import type { JSX } from "react";

import { Button } from "@/shared/ui/button";

export function LibraryBar({
  count,
  reingesting,
  deleting,
  onReingest,
  onDelete,
  onClear,
}: {
  count: number;
  reingesting: boolean;
  deleting: boolean;
  onReingest: () => void;
  onDelete: () => void;
  onClear: () => void;
}): JSX.Element {
  return (
    <section
      aria-label="Selected videos"
      className="sticky bottom-4 z-20 mx-auto flex w-full max-w-full flex-wrap items-center gap-x-3 gap-y-2 rounded-md border border-neutral-8 bg-card px-3 py-2 sm:w-fit"
    >
      <p role="status" className="tabular text-sm">
        {count} selected
      </p>
      <Button type="button" variant="outline" size="lg" onClick={onReingest} disabled={reingesting}>
        <RefreshCw aria-hidden />
        {reingesting ? "Queuing…" : "Re-ingest"}
      </Button>
      <Button type="button" variant="destructive" size="lg" onClick={onDelete} disabled={deleting}>
        <Trash2 aria-hidden />
        {deleting ? "Deleting…" : "Delete"}
      </Button>
      <Button type="button" variant="ghost" size="icon-lg" aria-label="Clear selection" onClick={onClear}>
        <X aria-hidden />
      </Button>
    </section>
  );
}
