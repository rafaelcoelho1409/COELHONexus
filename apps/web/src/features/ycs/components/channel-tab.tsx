"use client";

import { enumerateChannelVideos, queueChannelPipeline, queueVideosPipeline } from "../api";
import { parseChannel } from "../domain";
import type { SourceMode } from "../params";
import { SingleSourceTab } from "./single-source-tab";

/** Source · Channel — paste ONE channel, pick a subset of its videos, dispatch them through the videos
 * pipeline; or hand the whole channel to the server's one-call walk with Ingest all. */
export function ChannelTab({
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
      mode="channel"
      idPrefix="ycs-channel"
      inputLabel="Channel"
      placeholder="@handle, /channel/UC… or a channel URL"
      parser={parseChannel}
      emptyMessage="Paste a channel URL/handle first."
      fetchVideos={(identity) => (offset, limit) => enumerateChannelVideos(identity, offset, limit)}
      queueSubset={(ids, include, langs) =>
        queueVideosPipeline({
          video_ids: ids,
          include_transcription: include,
          transcription_languages: langs,
        })
      }
      queueAll={(identity, include, langs) =>
        queueChannelPipeline({
          channel_id: identity,
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
