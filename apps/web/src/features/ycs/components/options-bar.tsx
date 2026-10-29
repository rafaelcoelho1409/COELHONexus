"use client";

/** The three pieces the Videos, Playlist and Channel tabs share besides the picker: the transcript options (in the input
 * card, where the reader decides before fetching), one status line, and the ingest bar — which exists only once videos are
 * listed and sticks to the bottom of the screen, so the primary action stays in reach while a long list scrolls. */

import { Loader2 } from "lucide-react";
import type { JSX } from "react";

import { Button } from "@/shared/ui/button";
import { Input } from "@/shared/ui/input";
import { Label } from "@/shared/ui/label";

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
    <fieldset className="m-0 flex flex-wrap items-end gap-x-4 gap-y-2 border-0 p-0">
      <legend className="sr-only">Transcript options</legend>
      <label
        htmlFor={`${idPrefix}-incl-trans`}
        className="flex min-h-9 cursor-pointer items-center gap-2 text-sm"
      >
        <input
          id={`${idPrefix}-incl-trans`}
          type="checkbox"
          checked={include}
          onChange={(event) => onInclude(event.currentTarget.checked)}
          className="size-4 cursor-pointer accent-primary"
        />
        Fetch transcripts
      </label>
      <div className="w-44">
        <Label
          htmlFor={`${idPrefix}-langs`}
          className="mb-1.5 block text-xs font-medium text-muted-foreground"
        >
          Languages
        </Label>
        <Input
          id={`${idPrefix}-langs`}
          value={langs}
          onChange={(event) => onLangs(event.currentTarget.value)}
          disabled={!include}
          placeholder="en, pt, es"
          autoComplete="off"
          className="h-9 text-sm"
        />
      </div>
    </fieldset>
  );
}

/** One line for what is happening or went wrong; an error is an alert, the rest a polite status. */
export function StatusLine({ text, error }: { text: string | null; error: boolean }): JSX.Element | null {
  if (!text) return null;
  return error ? (
    <p role="alert" className="text-sm text-destructive-text">
      {text}
    </p>
  ) : (
    <p role="status" aria-live="polite" className="text-sm text-muted-foreground">
      {text}
    </p>
  );
}

/** What the ingest will do with the transcript options, in words — the bar sits far from the options once the list scrolls. */
export function optionsSummary(include: boolean, langs: ReadonlyArray<string> | null): string {
  if (!include) return "Transcripts off — metadata only";
  return langs && langs.length > 0 ? `Transcripts on · ${langs.join(", ")}` : "Transcripts on · any language";
}

export function IngestBar({
  selectedCount,
  ingestAllLabel,
  summary,
  pending,
  canIngestAll,
  onStart,
  onIngestAll,
}: {
  selectedCount: number;
  ingestAllLabel: string;
  summary: string;
  pending: boolean;
  canIngestAll: boolean;
  onStart: () => void;
  onIngestAll: () => void;
}): JSX.Element {
  const startLabel =
    selectedCount > 0
      ? `Start Ingestion (${selectedCount} video${selectedCount === 1 ? "" : "s"})`
      : "Start Ingestion";
  return (
    <section
      aria-label="Ingestion"
      className="sticky bottom-4 z-20 mx-auto flex w-full max-w-full flex-wrap items-center justify-between gap-x-4 gap-y-2 rounded-md border border-neutral-8 bg-card px-3 py-2"
    >
      <p className="text-xs text-muted-foreground">{summary}</p>
      <div className="flex flex-wrap items-center gap-2">
        <Button
          type="button"
          variant="outline"
          size="lg"
          onClick={onIngestAll}
          disabled={!canIngestAll || pending}
        >
          {pending ? "Queuing…" : ingestAllLabel}
        </Button>
        <Button type="button" size="lg" onClick={onStart} disabled={selectedCount < 1 || pending}>
          {pending ? <Loader2 aria-hidden className="animate-spin" /> : null}
          {pending ? "Queuing…" : startLabel}
        </Button>
      </div>
    </section>
  );
}
