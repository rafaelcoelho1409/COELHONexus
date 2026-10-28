/** A node's Activity tab: its stream events as log lines, with the failures marked and the lines that arrived
 * since the reader last had this node open called out. */

import type { StageEvent } from "../entities";
import type { RunStage } from "../params";
import { fmtClock } from "./format";
import { eventSummary } from "./live-text";

/** The log keeps the newest lines only, like a terminal scrollback. */
export const MAX_ACTIVITY_LINES = 200;

/** The longest failure detail worth a log line; the full event is in the stream if anyone needs it. */
const MAX_DETAIL_CHARS = 160;

/** Event fields that carry a failure's reason, in the order they are tried. */
const DETAIL_KEYS = ["error", "message", "reason"] as const;

export type ActivityTone = "info" | "warn" | "error";

export type ActivityLine = {
  /** Position in the node's event list: stable while the list only grows. */
  id: number;
  time: string;
  text: string;
  tone: ActivityTone;
  /** Arrived after the reader last had this node open. */
  isNew: boolean;
};

export function activityTone(event: StageEvent): ActivityTone {
  if (event.kind === "error" || event.error) return "error";
  if (event.kind === "warning" || event.kind === "warn") return "warn";
  return "info";
}

/** Why an event failed or warned, when it says so in words. */
function detailOf(event: StageEvent): string {
  for (const key of DETAIL_KEYS) {
    const value = event[key];
    if (typeof value === "string" && value.trim()) return value.trim().slice(0, MAX_DETAIL_CHARS);
  }
  return "";
}

/** Log lines for one node's events, oldest first, capped at the newest `MAX_ACTIVITY_LINES`. `since` is the
 * time (ms since the epoch) the reader last had this node open; 0 when this is the first time. */
export function activityLines(stage: RunStage, events: readonly StageEvent[], since: number): ActivityLine[] {
  return events
    .map((event, id): ActivityLine => {
      const tone = activityTone(event);
      const detail = tone === "info" ? "" : detailOf(event);
      const summary = eventSummary(stage, event);
      return {
        id,
        time: fmtClock(event.ts),
        text: detail ? `${summary} — ${detail}` : summary,
        tone,
        isNew: since > 0 && typeof event.ts === "number" && event.ts * 1000 > since,
      };
    })
    .slice(-MAX_ACTIVITY_LINES);
}
