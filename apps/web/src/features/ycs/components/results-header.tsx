"use client";

/** The bar over the results: the master checkbox, one live status line, and the density, page-size and paging controls.
 * It sticks under the app header, so the select-all box and the pager stay in reach while a long page scrolls. */

import { ChevronLeft, ChevronRight } from "lucide-react";
import type { JSX } from "react";

import { Button } from "@/shared/ui/button";

import { PAGE_SIZES } from "../params";
import type { Density } from "./result-list";
import { SELECT_CLASS } from "./search-filters";

const DENSITIES: ReadonlyArray<{ id: Density; label: string }> = [
  { id: "compact", label: "Compact" },
  { id: "comfortable", label: "Comfortable" },
];

export function ResultsHeader({
  master,
  visibleCount,
  status,
  failed,
  density,
  onDensity,
  size,
  onSize,
  canPrev,
  canNext,
  onPrev,
  onNext,
  onToggleMaster,
}: {
  master: "all" | "some" | "none";
  visibleCount: number;
  /** The one status line: the range, "Searching…", or the error. */
  status: string;
  failed: boolean;
  density: Density;
  onDensity: (density: Density) => void;
  size: number;
  onSize: (size: string) => void;
  canPrev: boolean;
  canNext: boolean;
  onPrev: () => void;
  onNext: () => void;
  onToggleMaster: () => void;
}): JSX.Element {
  return (
    <div className="z-10 flex sm:sticky sm:top-14 flex-wrap items-center gap-x-4 gap-y-2 rounded-t-md border-b border-border bg-card px-3 py-2">
      <label className="flex min-h-9 cursor-pointer items-center gap-3 text-sm">
        <span className="flex size-9 items-center justify-center">
          <input
            type="checkbox"
            ref={(element) => {
              if (element) element.indeterminate = master === "some";
            }}
            checked={master === "all"}
            disabled={visibleCount === 0}
            onChange={onToggleMaster}
            aria-label="Select all visible results"
            className="size-4 cursor-pointer accent-primary"
          />
        </span>
        <span>Select all</span>
        {visibleCount > 0 ? (
          <span className="text-xs text-muted-foreground">({visibleCount} on page)</span>
        ) : null}
      </label>

      <p
        role="status"
        aria-live="polite"
        className={`tabular text-xs ${failed ? "text-destructive-text" : "text-muted-foreground"}`}
      >
        {status}
      </p>

      <div className="ml-auto flex flex-wrap items-center gap-2">
        <fieldset className="m-0 flex min-w-0 border-0 p-0">
          <legend className="sr-only">Result density</legend>
          {DENSITIES.map((option) => (
            <button
              key={option.id}
              type="button"
              aria-pressed={density === option.id}
              onClick={() => onDensity(option.id)}
              className={`min-h-9 border px-3 text-xs first:rounded-l-sm last:rounded-r-sm ${
                density === option.id
                  ? "border-primary bg-primary/10 text-primary-text"
                  : "-ml-px border-border text-muted-foreground hover:text-foreground"
              }`}
            >
              {option.label}
            </button>
          ))}
        </fieldset>
        <select
          aria-label="Results per page"
          value={String(size)}
          onChange={(event) => onSize(event.currentTarget.value)}
          className={SELECT_CLASS}
        >
          {PAGE_SIZES.map((n) => (
            <option key={n} value={String(n)}>
              {n} per page
            </option>
          ))}
        </select>
        <div className="flex gap-1">
          <Button
            variant="outline"
            size="icon-lg"
            aria-label="Previous page"
            disabled={!canPrev}
            onClick={onPrev}
          >
            <ChevronLeft aria-hidden />
          </Button>
          <Button
            variant="outline"
            size="icon-lg"
            aria-label="Next page"
            disabled={!canNext}
            onClick={onNext}
          >
            <ChevronRight aria-hidden />
          </Button>
        </div>
      </div>
    </div>
  );
}
