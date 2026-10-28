"use client";

/** The Source stage: four modes on one row of tabs. Routing a selection from Search lands on the target
 * mode and re-mounts it (a nonce in the key), so the same batch can be routed twice. */

import type { JSX } from "react";
import { useState } from "react";

import type { SourceMode } from "../params";
import { ChannelTab } from "./channel-tab";
import { ModeTabs } from "./mode-tabs";
import { PlaylistTab } from "./playlist-tab";
import { type Routed, SearchTab } from "./search-tab";
import { VideosTab } from "./videos-tab";

type RoutedSeed = { nonce: number; videos: string[]; channel: string[]; playlist: string[] };

const ROUTE_LABELS: Record<Exclude<SourceMode, "search">, string> = {
  videos: "Videos",
  channel: "Channel",
  playlist: "Playlist",
};

export function SourceStage(): JSX.Element {
  const [mode, setMode] = useState<SourceMode>("search");
  const [routed, setRouted] = useState<RoutedSeed>({ nonce: 0, videos: [], channel: [], playlist: [] });

  const onRoute = (r: Routed) => {
    setMode(r.mode);
    setRouted((prev) => ({
      nonce: prev.nonce + 1,
      videos: r.mode === "videos" ? r.items : [],
      channel: r.mode === "channel" ? r.items : [],
      playlist: r.mode === "playlist" ? r.items : [],
    }));
  };

  const warnFor = (target: Exclude<SourceMode, "search">, items: string[]): string | null => {
    if (items.length <= 1) return null;
    return `Routed ${items.length} — one ${ROUTE_LABELS[target]} at a time: the first is kept, the rest dropped.`;
  };

  return (
    <div>
      <ModeTabs mode={mode} onChange={setMode} />
      <div className="mt-4">
        {mode === "search" ? <SearchTab onRoute={onRoute} /> : null}
        {mode === "videos" ? <VideosTab key={`videos-${routed.nonce}`} initialIds={routed.videos} /> : null}
        {mode === "playlist" ? (
          <PlaylistTab
            key={`playlist-${routed.nonce}`}
            initialIdentity={routed.playlist[0] ?? null}
            routedWarn={warnFor("playlist", routed.playlist)}
          />
        ) : null}
        {mode === "channel" ? (
          <ChannelTab
            key={`channel-${routed.nonce}`}
            initialIdentity={routed.channel[0] ?? null}
            routedWarn={warnFor("channel", routed.channel)}
          />
        ) : null}
      </div>
    </div>
  );
}
