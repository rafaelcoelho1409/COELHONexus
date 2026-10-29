"use client";

import { enumeratePlaylistVideos, queuePlaylistPipeline, queueVideosPipeline } from "../api";
import { parsePlaylist } from "../domain";
import type { SourceMode } from "../params";
import { SingleSourceTab } from "./single-source-tab";

/** Source · Playlist — mirror of the Channel tab: one playlist, pick a subset, or Ingest all. */
export function PlaylistTab({
  initialIdentity,
  routedWarn,
  onSwitch,
}: {
  initialIdentity: string | null;
  routedWarn: string | null;
  onSwitch: (mode: Exclude<SourceMode, "search">, text: string) => void;
}) {
  return (
    <SingleSourceTab
      mode="playlist"
      idPrefix="ycs-playlist"
      inputLabel="Playlist"
      placeholder="Playlist URL or PL… ID"
      parser={parsePlaylist}
      emptyMessage="Paste a playlist URL/ID first."
      fetchVideos={(identity) => (offset, limit) => enumeratePlaylistVideos(identity, offset, limit)}
      queueSubset={(ids, include, langs) =>
        queueVideosPipeline({
          video_ids: ids,
          include_transcription: include,
          transcription_languages: langs,
        })
      }
      queueAll={(identity, include, langs) =>
        queuePlaylistPipeline({
          playlist_id: identity,
          include_transcription: include,
          transcription_languages: langs,
        })
      }
      initialIdentity={initialIdentity}
      routedWarn={routedWarn}
      onSwitch={onSwitch}
    />
  );
}
