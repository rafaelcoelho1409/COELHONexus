"use client";

/** Which channels a question searches. Open until the thread's first message; after that the thread keeps the scope it began
 * with (the server enforces it), so the control turns into a plain "locked" label that says why. */

import { useQuery } from "@tanstack/react-query";
import { Library, Lock } from "lucide-react";
import type { JSX } from "react";

import { fetchChannels } from "../api";
import { scopeLabel } from "../ask";
import { ToolbarPanel } from "./toolbar-panel";

export function AskScope({
  selected,
  locked,
  onChange,
}: {
  selected: readonly string[];
  locked: boolean;
  onChange: (ids: string[]) => void;
}): JSX.Element {
  const channels = useQuery({
    queryKey: ["ycs", "ask", "channels"],
    queryFn: fetchChannels,
    staleTime: 60_000,
  });
  const names = new Map((channels.data ?? []).map((channel) => [channel.channel_id, channel.channel]));
  const label = scopeLabel(selected, (id) => names.get(id) ?? "Unknown channel", channels.isSuccess);

  if (locked) {
    return (
      <p
        title="A thread keeps the channels it started with. Start a new thread to search others."
        className="m-0 flex min-h-9 w-full max-w-full items-center gap-2 rounded-sm border border-border px-2.5 text-sm text-muted-foreground sm:inline-flex sm:w-auto"
      >
        <Lock aria-hidden className="size-4 shrink-0" />
        <span className="min-w-0 truncate">{label}</span>
        {/* On a phone the lock icon says it and the words stay for screen readers: the row has no room for them. */}
        <span className="sr-only shrink-0 text-xs sm:not-sr-only">· locked to this thread</span>
      </p>
    );
  }

  const toggle = (id: string, on: boolean) =>
    onChange(on ? [...selected, id] : selected.filter((existing) => existing !== id));

  return (
    <ToolbarPanel icon={<Library aria-hidden />} label={`Channels: ${label}`}>
      {() => (
        <div className="flex flex-col gap-1">
          <label className="flex min-h-9 cursor-pointer items-center gap-2 rounded-sm px-2 text-sm hover:bg-muted">
            <input
              type="checkbox"
              checked={selected.length === 0}
              onChange={() => onChange([])}
              className="size-4 cursor-pointer accent-primary"
            />
            All channels
          </label>
          <div className="my-1 border-t border-border" />
          {channels.isPending ? (
            <p className="px-2 py-1 text-xs text-muted-foreground">Loading channels…</p>
          ) : null}
          {channels.isError ? (
            <p role="alert" className="px-2 py-1 text-xs text-destructive-text">
              Could not load the channels.
            </p>
          ) : null}
          {channels.isSuccess && channels.data.length === 0 ? (
            <p className="px-2 py-1 text-xs text-muted-foreground">Nothing is ingested yet.</p>
          ) : null}
          <ul className="m-0 flex max-h-72 list-none flex-col gap-0.5 overflow-y-auto p-0">
            {(channels.data ?? []).map((channel) => (
              <li key={channel.channel_id}>
                <label className="flex min-h-9 cursor-pointer items-center gap-2 rounded-sm px-2 text-sm hover:bg-muted">
                  <input
                    type="checkbox"
                    checked={selected.includes(channel.channel_id)}
                    onChange={(event) => toggle(channel.channel_id, event.target.checked)}
                    className="size-4 shrink-0 cursor-pointer accent-primary"
                  />
                  <span className="min-w-0 flex-1 break-words">{channel.channel}</span>
                  <span className="tabular shrink-0 text-xs text-muted-foreground">
                    {channel.video_count}
                  </span>
                </label>
              </li>
            ))}
          </ul>
        </div>
      )}
    </ToolbarPanel>
  );
}
