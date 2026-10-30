"use client";

/** The videos of a run, one row each, with where each stands in the four stores. Failed videos are first, a count says how many
 * are failed, in progress, waiting or done, and "Failed only" narrows the table to the ones that need a look. */

import type { JSX } from "react";
import { useState } from "react";

import {
  type PipelinePrefix,
  STORE_STATUS_LABELS,
  type StoreStatus,
  sortVideoRows,
  videoCounts,
} from "../domain";
import type { VideoRow } from "../hooks/use-video-rows";
import { SideSheet } from "./side-sheet";
import { type BadgeTone, StatusBadge } from "./status-badge";

const STORES: ReadonlyArray<{ key: PipelinePrefix; label: string; title: string }> = [
  { key: "playwright", label: "PW", title: "Playwright transcript fetch" },
  { key: "elasticsearch", label: "ES", title: "ElasticSearch chunk-commit" },
  { key: "qdrant", label: "Qdrant", title: "Qdrant upsert" },
  { key: "neo4j", label: "Neo4j", title: "Neo4j entity extraction" },
];

const TONE: Record<StoreStatus, BadgeTone> = {
  queued: "queued",
  running: "running",
  done: "done",
  failed: "failed",
  unavailable: "na",
};

export function PipelineVideosSheet({
  rows,
  onClose,
}: {
  rows: ReadonlyArray<VideoRow>;
  onClose: () => void;
}): JSX.Element {
  const [failedOnly, setFailedOnly] = useState(false);
  const counts = videoCounts(rows);
  const sorted = sortVideoRows(rows);
  const shown = failedOnly ? sorted.filter((row) => row.anyFailed) : sorted;

  return (
    <SideSheet
      title="Videos in this pipeline"
      closeLabel="Close the video list"
      onClose={onClose}
      subtitle={
        <span className="tabular">
          {rows.length} video{rows.length === 1 ? "" : "s"} · {counts.failed} failed · {counts.running}{" "}
          running · {counts.queued} queued · {counts.done} done
        </span>
      }
    >
      {rows.length === 0 ? (
        <p className="text-sm text-muted-foreground">Waiting for the metadata of the videos…</p>
      ) : (
        <>
          <label className="mb-3 flex min-h-9 cursor-pointer items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={failedOnly}
              onChange={(event) => setFailedOnly(event.currentTarget.checked)}
              className="size-4 cursor-pointer accent-primary"
            />
            Failed only ({counts.failed})
          </label>
          <section
            // biome-ignore lint/a11y/noNoninteractiveTabindex: a scrollable region must be reachable by keyboard (WCAG 2.1.1)
            tabIndex={0}
            aria-label="Per-video pipeline progress"
            className="overflow-x-auto rounded-sm border border-border"
          >
            <table className="w-full border-collapse text-left text-sm">
              <thead>
                <tr className="bg-card text-xs text-muted-foreground">
                  <th scope="col" className="px-2 py-2 font-medium">
                    Video
                  </th>
                  {STORES.map((store) => (
                    <th key={store.key} scope="col" className="px-2 py-2 font-medium" title={store.title}>
                      {store.label}
                    </th>
                  ))}
                  <th scope="col" className="px-2 py-2 text-right font-medium">
                    Time
                  </th>
                </tr>
              </thead>
              <tbody>
                {shown.map((row) => (
                  <tr key={row.videoId} className="border-t border-border align-top">
                    <th
                      scope="row"
                      className="min-w-40 px-2 py-2 font-normal"
                      title={`${row.title} · ${row.videoId}`}
                    >
                      <span className="line-clamp-2">{row.title}</span>
                      {row.channel ? (
                        <span className="block text-xs text-muted-foreground">{row.channel}</span>
                      ) : null}
                    </th>
                    {STORES.map((store) => (
                      <td key={store.key} className="px-2 py-2">
                        <StatusBadge
                          tone={TONE[row.cells[store.key]]}
                          label={STORE_STATUS_LABELS[row.cells[store.key]]}
                        />
                      </td>
                    ))}
                    <td className="tabular px-2 py-2 text-right font-mono text-xs">{row.duration}</td>
                  </tr>
                ))}
                {shown.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="px-2 py-4 text-sm text-muted-foreground">
                      No video has failed.
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </section>
        </>
      )}
    </SideSheet>
  );
}
