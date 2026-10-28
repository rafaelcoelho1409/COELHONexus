import { Check, CircleAlert, X } from "lucide-react";

import type { UnfurlState } from "../domain";

/** The compact claim card shown in place of the input once a paste parses (Slack/Linear unfurl idiom).
 * `parsed` gets a clear button; `invalid` explains why the text was rejected. */
export function UrlPreview({ state, onReset }: { state: UnfurlState; onReset: () => void }) {
  if (state.state === "hidden") {
    return null;
  }
  if (state.state === "parsed") {
    const labels = { channel: "Channel", playlist: "Playlist", video: "Video" } as const;
    return (
      <div className="flex min-h-9 items-center gap-2 rounded-sm border border-border bg-muted px-2.5 py-1 text-sm">
        <Check className="size-4 text-success-text" aria-hidden />
        <span className="text-xs text-muted-foreground">{labels[state.parsed.kind]}</span>
        <span className="min-w-0 flex-1 truncate font-mono text-xs">{state.parsed.display}</span>
        <span className="hidden text-xs text-muted-foreground sm:inline">Metadata fetches on submit</span>
        <button
          type="button"
          onClick={onReset}
          aria-label="Clear"
          className="inline-flex size-6 shrink-0 items-center justify-center rounded-sm text-muted-foreground hover:text-foreground"
        >
          <X className="size-4" aria-hidden />
        </button>
      </div>
    );
  }
  return (
    <div className="flex min-h-9 items-center gap-2 rounded-sm border border-destructive/40 bg-card px-2.5 py-1 text-sm">
      <CircleAlert className="size-4 text-destructive-text" aria-hidden />
      <span className="text-xs text-muted-foreground">Unrecognized</span>
      <span className="min-w-0 flex-1 truncate font-mono text-xs">{state.raw.slice(0, 80)}</span>
      <span className="text-xs text-muted-foreground">Doesn&apos;t look like a YouTube URL or ID</span>
    </div>
  );
}
