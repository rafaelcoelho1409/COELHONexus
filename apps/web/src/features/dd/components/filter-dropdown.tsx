"use client";

/** A toolbar filter: a button reading "CATEGORY: All ▾" that opens a short list of options with their counts.
 * One component for both kinds the catalog needs — pick one (category) or toggle several (tier, status) with a
 * Reset entry. Closes on Escape (focus returns to the button) and on a click elsewhere. */

import { cn } from "cn";
import { ChevronDown } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";

export type FilterOption = { value: string; label: string; count: number; active: boolean };

export function FilterDropdown({
  label,
  summary,
  ariaLabel,
  options,
  multiple,
  onToggle,
  onReset,
}: {
  /** The small caps prefix ("Category"). */
  label: string;
  /** What is chosen right now ("All", "1, 2"). */
  summary: string;
  ariaLabel: string;
  options: readonly FilterOption[];
  /** Several options may be on at once, and a Reset entry clears them. */
  multiple: boolean;
  onToggle: (value: string) => void;
  onReset: () => void;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const listId = useId();

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: MouseEvent) => {
      if (rootRef.current && event.target instanceof Node && !rootRef.current.contains(event.target)) {
        setOpen(false);
      }
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        triggerRef.current?.focus();
      }
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const choose = (value: string) => {
    onToggle(value);
    if (!multiple) setOpen(false);
  };

  return (
    // `sm:relative`: from `sm` the list hangs under its own button; on a phone it spans the toolbar instead, so a
    // button near the right edge cannot push the list off screen.
    <div ref={rootRef} className="sm:relative">
      <button
        ref={triggerRef}
        type="button"
        aria-haspopup="true"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        aria-label={`${ariaLabel}: ${summary}`}
        onClick={() => setOpen((value) => !value)}
        className={cn(
          "inline-flex h-9 items-center gap-2 rounded-sm border bg-card px-3 text-sm hover:border-foreground/40",
          open ? "border-primary" : "border-border",
        )}
      >
        <span aria-hidden="true" className="font-mono text-xs uppercase tracking-wide text-muted-foreground">
          {label}:
        </span>
        <span aria-hidden="true" className="max-w-28 truncate">
          {summary}
        </span>
        <ChevronDown aria-hidden className={cn("size-4 text-muted-foreground", open && "rotate-180")} />
      </button>
      {open ? (
        <fieldset
          id={listId}
          className="absolute inset-x-0 top-full z-40 m-0 mt-1 max-h-80 min-w-0 overflow-auto rounded-md border border-border bg-card p-1 sm:inset-x-auto sm:left-0 sm:min-w-52"
        >
          <legend className="sr-only">{ariaLabel}</legend>
          {multiple ? (
            <button
              type="button"
              onClick={() => onReset()}
              className="flex min-h-9 w-full items-center rounded-sm px-3 text-left text-sm text-muted-foreground hover:bg-muted hover:text-foreground"
            >
              Reset
            </button>
          ) : null}
          {options.map((option) => (
            <button
              key={option.value}
              type="button"
              aria-pressed={option.active}
              onClick={() => choose(option.value)}
              className={cn(
                "flex min-h-9 w-full items-center justify-between gap-4 rounded-sm px-3 text-left text-sm hover:bg-muted",
                option.active && "bg-primary/10 text-primary-text",
              )}
            >
              <span className="truncate">{option.label}</span>
              <span className="shrink-0 font-mono text-xs text-muted-foreground">{option.count}</span>
            </button>
          ))}
        </fieldset>
      ) : null}
    </div>
  );
}
