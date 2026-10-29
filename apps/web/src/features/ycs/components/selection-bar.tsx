"use client";

/** The bar that appears while rows are selected: how many, where they can go, and clear. It sticks to the bottom of the
 * screen (thumb reach on a phone, the same place as the list's pager), and the list leaves room under its last row. */

import { ListVideo, User, Video, X } from "lucide-react";
import type { JSX } from "react";

import { Button } from "@/shared/ui/button";

import { fmtCount } from "../domain";
import type { SourceMode } from "../params";

export type RouteMode = Exclude<SourceMode, "search">;

const ROUTES: ReadonlyArray<{ mode: RouteMode; label: string }> = [
  { mode: "videos", label: "Videos" },
  { mode: "channel", label: "Channel" },
  { mode: "playlist", label: "Playlist" },
];

const ICONS = { videos: Video, channel: User, playlist: ListVideo } as const;

export function SelectionBar({
  selected,
  counts,
  onRoute,
  onClear,
}: {
  selected: number;
  /** How many of the selected rows each destination can take. */
  counts: Record<RouteMode, number>;
  onRoute: (mode: RouteMode) => void;
  onClear: () => void;
}): JSX.Element {
  return (
    <section
      aria-label="Selection"
      className="sticky bottom-4 z-20 mx-auto flex w-full max-w-full flex-wrap items-center gap-x-3 gap-y-1 rounded-md border border-neutral-8 bg-card px-3 py-2 sm:w-fit"
    >
      <p role="status" className="text-sm">
        <strong className="tabular font-semibold">{selected}</strong> selected
      </p>
      {/* On a phone the count and Clear share the first row and the three destinations the second, each a third wide. */}
      <Button
        type="button"
        variant="ghost"
        size="icon-lg"
        className="order-2 ml-auto sm:order-3 sm:ml-0"
        aria-label="Clear selection"
        onClick={onClear}
      >
        <X aria-hidden />
      </Button>
      <div className="order-3 flex w-full gap-2 sm:order-2 sm:w-auto">
        {ROUTES.map(({ mode, label }) => {
          const Icon = ICONS[mode];
          return (
            <Button
              key={mode}
              type="button"
              variant="outline"
              size="lg"
              className="min-w-0 flex-1 px-2 sm:flex-none"
              disabled={counts[mode] === 0}
              onClick={() => onRoute(mode)}
            >
              <Icon aria-hidden />
              {`→ ${fmtCount(counts[mode])} ${label}`}
            </Button>
          );
        })}
      </div>
    </section>
  );
}
