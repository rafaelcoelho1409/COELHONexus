"use client";

/** "This looks like a playlist — switch?": a suggestion when what was pasted belongs to another Source tab. It is only ever a
 * suggestion — one click moves the paste there, or it can be dismissed — because silently re-routing erodes trust. */

import { X } from "lucide-react";
import type { JSX } from "react";
import { useState } from "react";

import { Button } from "@/shared/ui/button";

import { modeHint } from "../domain";
import type { SourceMode } from "../params";

const KIND_LABEL = { videos: "video", channel: "channel", playlist: "playlist" } as const;
const TAB_LABEL = { videos: "Videos", channel: "Channel", playlist: "Playlist" } as const;

type PasteMode = Exclude<SourceMode, "search">;

export function ModeHint({
  text,
  own,
  onSwitch,
}: {
  text: string;
  own: PasteMode;
  onSwitch: (mode: PasteMode, text: string) => void;
}): JSX.Element | null {
  // A dismissal holds for this text only; a different paste may be suggested about again.
  const [dismissed, setDismissed] = useState<string | null>(null);
  const suggested = modeHint(text, own);
  if (!suggested || dismissed === text) return null;
  return (
    <div
      role="status"
      className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-sm border border-border bg-muted px-3 py-1.5 text-sm"
    >
      <p className="min-w-0 flex-1">
        This looks like a {KIND_LABEL[suggested]}, not a {KIND_LABEL[own]}.
      </p>
      <Button type="button" variant="outline" size="lg" onClick={() => onSwitch(suggested, text.trim())}>
        Switch to {TAB_LABEL[suggested]}
      </Button>
      <Button
        type="button"
        variant="ghost"
        size="icon-lg"
        aria-label="Dismiss suggestion"
        onClick={() => setDismissed(text)}
      >
        <X aria-hidden />
      </Button>
    </div>
  );
}
