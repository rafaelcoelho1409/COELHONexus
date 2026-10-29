"use client";

/** What the Videos textarea understood, live under it: how many videos, how many repeats were folded together, and the lines that
 * were not recognised (so they can be fixed or dropped before fetching, not found missing afterwards). */

import type { JSX } from "react";

import type { PasteSummary as Summary } from "../domain";

const SHOWN_INVALID = 20;

export function PasteSummary({
  id,
  summary,
  empty,
}: {
  id: string;
  summary: Summary;
  empty: boolean;
}): JSX.Element {
  if (empty) {
    return (
      <p id={id} className="text-xs text-muted-foreground">
        One ID or URL per line, or comma-separated. Watch links, youtu.be links and bare 11-character IDs
        work.
      </p>
    );
  }
  const parts = [
    `${summary.ids.length} video${summary.ids.length === 1 ? "" : "s"}`,
    summary.duplicates > 0
      ? `${summary.duplicates} duplicate${summary.duplicates === 1 ? "" : "s"} skipped`
      : null,
    summary.invalid.length > 0 ? `${summary.invalid.length} not recognized` : null,
  ].filter(Boolean);
  return (
    <div id={id} className="flex flex-col gap-1">
      <p role="status" aria-live="polite" className="tabular text-xs text-muted-foreground">
        {parts.join(" · ")}
      </p>
      {summary.invalid.length > 0 ? (
        <details className="text-xs">
          <summary className="min-h-9 cursor-pointer py-2 text-warning-text">
            Show the {summary.invalid.length} line{summary.invalid.length === 1 ? "" : "s"} that were not
            recognized
          </summary>
          <ul className="mt-1 flex flex-col gap-0.5 font-mono text-muted-foreground">
            {summary.invalid.slice(0, SHOWN_INVALID).map((item) => (
              <li key={`${item.line}-${item.text}`} className="break-all">
                Line {item.line}: {item.text}
              </li>
            ))}
            {summary.invalid.length > SHOWN_INVALID ? (
              <li>…and {summary.invalid.length - SHOWN_INVALID} more</li>
            ) : null}
          </ul>
        </details>
      ) : null}
    </div>
  );
}
