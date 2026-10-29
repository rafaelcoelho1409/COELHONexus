"use client";

/** The filters of the Search tab, in two places: three quick ones that are always in view (kind, duration, newest first) and a
 * "More filters" panel for the rest. Every control is a native form field whose `name` is a `SearchRequest` field, so the
 * form's own data is the request (see `readSearchRequest`), and nothing searches until the reader presses Search or Enter —
 * each search asks YouTube again and takes seconds. The panel stays in the form while it is closed, so a filter set earlier
 * still counts. */

import { RotateCcw, Search, X } from "lucide-react";
import type { JSX, ReactNode } from "react";
import { useEffect, useRef } from "react";

import { Button } from "@/shared/ui/button";
import { Input } from "@/shared/ui/input";
import { Label } from "@/shared/ui/label";

/** A native `<select>` at the 36px target size, in the same hairline style as the inputs (width is the caller's). */
export const SELECT_CLASS =
  "h-9 rounded-sm border border-input bg-background px-2.5 text-sm transition-colors outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50";

const INPUT_CLASS = "h-9 text-sm";

const KINDS: ReadonlyArray<{ value: string; label: string }> = [
  { value: "", label: "All" },
  { value: "video", label: "Videos" },
  { value: "channel", label: "Channels" },
  { value: "playlist", label: "Playlists" },
];

function Field({
  label,
  htmlFor,
  className = "",
  children,
}: {
  label: string;
  htmlFor: string;
  className?: string;
  children: ReactNode;
}): JSX.Element {
  return (
    <div className={className}>
      <Label htmlFor={htmlFor} className="mb-1.5 block text-xs font-medium text-muted-foreground">
        {label}
      </Label>
      {children}
    </div>
  );
}

function Check({ name, label }: { name: string; label: string }): JSX.Element {
  return (
    <label className="flex min-h-9 cursor-pointer items-center gap-2 text-sm">
      <input type="checkbox" name={name} className="size-4 cursor-pointer accent-primary" />
      {label}
    </label>
  );
}

/** Kind, duration and newest-first: the three choices most searches start with, always visible. */
export function QuickFilters(): JSX.Element {
  return (
    <>
      <fieldset className="m-0 border-0 p-0">
        <legend className="mb-1.5 text-xs font-medium text-muted-foreground">Kind</legend>
        <div className="flex">
          {KINDS.map((kind) => (
            <label
              key={kind.value || "all"}
              className="relative -ml-px first:ml-0 first:[&>span]:rounded-l-sm last:[&>span]:rounded-r-sm"
            >
              <input
                type="radio"
                name="kind_filter"
                value={kind.value}
                defaultChecked={kind.value === ""}
                className="peer sr-only"
              />
              <span className="flex min-h-9 cursor-pointer items-center border border-border px-3 text-sm text-muted-foreground hover:text-foreground peer-checked:border-primary peer-checked:bg-primary/10 peer-checked:text-primary-text peer-focus-visible:ring-2 peer-focus-visible:ring-ring">
                {kind.label}
              </span>
            </label>
          ))}
        </div>
      </fieldset>
      <Field label="Duration" htmlFor="ycs-f-duration">
        <select id="ycs-f-duration" name="duration" defaultValue="" className={SELECT_CLASS}>
          <option value="">Any duration</option>
          <option value="Under 4 minutes">Under 4 minutes</option>
          <option value="4 - 20 minutes">4 – 20 minutes</option>
          <option value="Over 20 minutes">Over 20 minutes</option>
        </select>
      </Field>
      <Check name="sort_by_date" label="Newest first" />
    </>
  );
}

/** Opens the panel the way the screen needs: beside the results on a desktop (a plain, non-modal `<dialog>` laid out in the
 * page), and as a modal bottom sheet on a phone, where it covers the results for as long as the reader is choosing. The
 * element is always mounted, so its fields are part of the form whether it is open or not. */
function useFilterDialog(open: boolean) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (!open) {
      if (dialog.open) {
        if (typeof dialog.close === "function") dialog.close();
        else dialog.open = false;
      }
      return;
    }
    if (dialog.open) return;
    const wide = typeof window.matchMedia !== "function" || window.matchMedia("(min-width: 640px)").matches;
    if (!wide && typeof dialog.showModal === "function") dialog.showModal();
    else if (typeof dialog.show === "function") dialog.show();
    else dialog.open = true; // jsdom has neither
  }, [open]);
  return ref;
}

export function MoreFilters({
  id,
  open,
  onClose,
  onReset,
}: {
  id: string;
  open: boolean;
  onClose: () => void;
  onReset: () => void;
}): JSX.Element {
  const ref = useFilterDialog(open);
  return (
    <dialog
      ref={ref}
      id={id}
      aria-label="More filters"
      onCancel={(event) => {
        event.preventDefault(); // Escape on the phone sheet: close through our state
        onClose();
      }}
      className="fixed inset-x-0 top-auto bottom-0 m-0 max-h-[85dvh] w-full max-w-none overflow-y-auto rounded-t-md border border-border bg-card p-0 text-foreground backdrop:bg-background/80 sm:static sm:max-h-none sm:overflow-visible sm:rounded-md"
    >
      <div className="flex flex-col gap-3 p-3 sm:pb-3">
        <div className="flex items-center justify-between sm:hidden">
          <h2 className="text-sm font-medium">More filters</h2>
          <Button type="button" variant="ghost" size="icon-lg" aria-label="Close filters" onClick={onClose}>
            <X aria-hidden />
          </Button>
        </div>

        <div className="grid grid-cols-1 gap-x-4 gap-y-3 sm:grid-cols-2 lg:grid-cols-4">
          <Field label="Uploaded after" htmlFor="ycs-f-date_after">
            <Input type="date" id="ycs-f-date_after" name="date_after" className={INPUT_CLASS} />
          </Field>
          <Field label="Uploaded before" htmlFor="ycs-f-date_before">
            <Input type="date" id="ycs-f-date_before" name="date_before" className={INPUT_CLASS} />
          </Field>
          <Field label="Min views" htmlFor="ycs-f-min_views">
            <Input
              type="number"
              id="ycs-f-min_views"
              name="min_views"
              min={0}
              inputMode="numeric"
              placeholder="e.g. 10000"
              className={INPUT_CLASS}
            />
          </Field>
          <Field label="Max views" htmlFor="ycs-f-max_views">
            <Input
              type="number"
              id="ycs-f-max_views"
              name="max_views"
              min={0}
              inputMode="numeric"
              placeholder="e.g. 1000000"
              className={INPUT_CLASS}
            />
          </Field>
          <Field label="Min likes" htmlFor="ycs-f-min_likes">
            <Input
              type="number"
              id="ycs-f-min_likes"
              name="min_likes"
              min={0}
              inputMode="numeric"
              placeholder="e.g. 100"
              className={INPUT_CLASS}
            />
          </Field>
          <Field label="Title contains" htmlFor="ycs-f-title">
            <Input
              id="ycs-f-title"
              name="title_contains"
              placeholder="Plain text or *=op"
              autoComplete="off"
              className={INPUT_CLASS}
            />
          </Field>
          <Field label="Channel name" htmlFor="ycs-f-channel">
            <Input
              id="ycs-f-channel"
              name="channel_name"
              placeholder="Creator or channel"
              autoComplete="off"
              className={INPUT_CLASS}
            />
          </Field>
          <fieldset className="m-0 border-0 p-0">
            <legend className="mb-1.5 text-xs font-medium text-muted-foreground">Shorts</legend>
            <Check name="exclude_shorts" label="Exclude shorts" />
          </fieldset>
        </div>

        {/* A second way to search, at the foot of the panel: on a phone the Search button above is out of reach. */}
        <div className="sticky bottom-0 -mx-3 -mb-3 flex items-center justify-between gap-3 border-t border-border bg-card p-3 sm:static sm:mx-0 sm:mb-0 sm:px-0 sm:pb-0">
          <Button type="button" variant="ghost" size="lg" onClick={onReset}>
            <RotateCcw aria-hidden />
            Reset filters
          </Button>
          <Button type="submit" size="lg">
            <Search aria-hidden />
            Apply and search
          </Button>
        </div>
      </div>
    </dialog>
  );
}
