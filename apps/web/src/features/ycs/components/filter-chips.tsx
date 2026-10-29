"use client";

/** The filters a search ran with, as chips under the query: each removes itself and searches again. */

import { X } from "lucide-react";
import type { JSX } from "react";

import type { FilterChip } from "../domain";

export function FilterChips({
  chips,
  onRemove,
  onClear,
}: {
  chips: ReadonlyArray<FilterChip>;
  onRemove: (key: string) => void;
  onClear: () => void;
}): JSX.Element | null {
  if (chips.length === 0) return null;
  return (
    <ul aria-label="Active filters" className="flex flex-wrap items-center gap-2">
      {chips.map((chip) => (
        <li key={chip.key}>
          <button
            type="button"
            onClick={() => onRemove(chip.key)}
            aria-label={`Remove filter: ${chip.label}`}
            className="inline-flex min-h-9 items-center gap-1.5 rounded-sm border border-border bg-muted px-2.5 text-xs hover:border-neutral-8"
          >
            {chip.label}
            <X aria-hidden className="size-3.5 text-muted-foreground" />
          </button>
        </li>
      ))}
      <li>
        <button
          type="button"
          onClick={onClear}
          className="min-h-9 px-2 text-xs text-muted-foreground underline underline-offset-2 hover:text-foreground"
        >
          Clear all
        </button>
      </li>
    </ul>
  );
}
