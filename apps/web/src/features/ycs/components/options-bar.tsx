"use client";

import type { JSX, ReactNode } from "react";

import { Button } from "@/shared/ui/button";
import { Input } from "@/shared/ui/input";
import { Label } from "@/shared/ui/label";

/** The sticky bottom bar of a Source tab: transcript options on the left, status in the middle, the
 * ingest-all and Start-ingestion actions on the right. It pins to the viewport bottom so the primary
 * action stays one click away while long picker lists scroll. */
export function OptionsBar({ children }: { children: ReactNode }): JSX.Element {
  return (
    <div className="sticky bottom-0 z-10 -mx-4 mt-4 border-t border-border bg-background px-4 py-3">
      <div className="flex flex-wrap items-end gap-x-4 gap-y-2">{children}</div>
    </div>
  );
}

export function TranscriptOptions({
  idPrefix,
  include,
  onInclude,
  langs,
  onLangs,
}: {
  idPrefix: string;
  include: boolean;
  onInclude: (include: boolean) => void;
  langs: string;
  onLangs: (langs: string) => void;
}): JSX.Element {
  return (
    <>
      <div className="flex items-center gap-2 pb-0.5">
        <input
          id={`${idPrefix}-incl-trans`}
          type="checkbox"
          checked={include}
          onChange={(e) => onInclude(e.currentTarget.checked)}
        />
        <Label htmlFor={`${idPrefix}-incl-trans`} className="normal-case">
          Fetch transcripts
        </Label>
      </div>
      <div className="w-44">
        <Label htmlFor={`${idPrefix}-langs`} className="mb-1 text-xs text-muted-foreground">
          Languages
        </Label>
        <Input
          id={`${idPrefix}-langs`}
          value={langs}
          onChange={(e) => onLangs(e.currentTarget.value)}
          placeholder="en, pt, es"
          autoComplete="off"
        />
      </div>
    </>
  );
}

export function StatusLine({ text }: { text: string | null }): JSX.Element {
  return (
    <p aria-live="polite" className="min-h-8 max-w-md flex-1 self-center text-sm text-muted-foreground">
      {text ?? ""}
    </p>
  );
}

export function IngestAllButton({
  label,
  pending,
  disabled,
  onClick,
}: {
  label: string;
  pending: boolean;
  disabled: boolean;
  onClick: () => void;
}): JSX.Element {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled || pending}
      className="inline-flex h-8 items-center rounded-sm border border-border bg-card px-2.5 text-sm font-medium hover:bg-muted disabled:opacity-50"
    >
      {pending ? "Queuing…" : label}
    </button>
  );
}

export function StartIngestionButton({
  selectedCount,
  pending,
  onClick,
}: {
  selectedCount: number;
  pending: boolean;
  onClick: () => void;
}): JSX.Element {
  const label =
    selectedCount > 0
      ? `Start Ingestion (${selectedCount} video${selectedCount === 1 ? "" : "s"})`
      : "Start Ingestion";
  return (
    <Button type="button" onClick={onClick} disabled={selectedCount < 1 || pending}>
      {pending ? "Queuing…" : label}
    </Button>
  );
}
