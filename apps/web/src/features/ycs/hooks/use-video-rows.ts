"use client";

/** The per-video × per-store rows of a pipeline run, with how long each video has taken. The run's own panel owns this (not the
 * sheet that shows it), so timing keeps counting while the sheet is closed. */

import { useEffect, useRef, useState } from "react";

import {
  cellDuration,
  nextTiming,
  type PhaseSnapshot,
  type PipelinePrefix,
  type StoreStatus,
  storeStatus,
  type Timing,
  type TimingRow,
} from "../domain";

export type VideoMeta = { id: string; title?: string | null; channel?: string | null };

export type VideoRow = {
  videoId: string;
  title: string;
  channel: string | null;
  cells: Record<PipelinePrefix, StoreStatus>;
  anyRunning: boolean;
  allDone: boolean;
  anyFailed: boolean;
  duration: string;
};

const STORES = ["playwright", "elasticsearch", "qdrant", "neo4j"] as const;

export function useVideoRows({
  videoIds,
  items,
  snapshots,
  noTranscriptIds,
}: {
  videoIds: ReadonlyArray<string>;
  items: ReadonlyArray<VideoMeta>;
  snapshots: Record<PipelinePrefix, PhaseSnapshot>;
  noTranscriptIds: ReadonlySet<string>;
}): VideoRow[] {
  const metaById = new Map(items.filter((v) => v.id).map((v) => [v.id, v]));
  const ids = videoIds.length ? videoIds : items.map((v) => v.id).filter(Boolean);

  const rows: VideoRow[] = ids.map((videoId) => {
    const meta = metaById.get(videoId);
    const cells: Record<PipelinePrefix, StoreStatus> = {
      playwright: storeStatus(videoId, "playwright", snapshots.playwright, null),
      elasticsearch: storeStatus(videoId, "elasticsearch", snapshots.elasticsearch, noTranscriptIds),
      qdrant: storeStatus(videoId, "qdrant", snapshots.qdrant, noTranscriptIds),
      neo4j: storeStatus(videoId, "neo4j", snapshots.neo4j, noTranscriptIds),
    };
    const values = STORES.map((store) => cells[store]);
    return {
      videoId,
      title: meta?.title || videoId,
      channel: meta?.channel ?? null,
      cells,
      anyRunning: values.includes("running"),
      anyFailed: values.includes("failed"),
      allDone: values.every((s) => s === "done" || s === "unavailable"),
      duration: "",
    };
  });

  // Timing lives in a ref the render never reads: a dep-less effect snapshots the row summaries and the one-second ticker
  // stamps starts/finishes through the pure `nextTiming`. Reading either ref during render would break the compiler's purity rules.
  const [tick, setTick] = useState(() => Date.now());
  const [timing, setTiming] = useState<Timing>(() => ({ started: new Map(), finished: new Map() }));
  const rowsRef = useRef<ReadonlyArray<TimingRow>>([]);
  useEffect(() => {
    rowsRef.current = rows.map(({ videoId, anyRunning, allDone, anyFailed }) => ({
      videoId,
      anyRunning,
      allDone,
      anyFailed,
    }));
  });
  useEffect(() => {
    const timer = setInterval(() => {
      const stamp = Date.now();
      setTiming((prev) => nextTiming(prev, rowsRef.current, stamp));
      setTick(stamp);
    }, 1000);
    return () => clearInterval(timer);
  }, []);

  return rows.map((row) => {
    const started = timing.started.get(row.videoId);
    if (started == null) return { ...row, duration: "—" };
    const finished = timing.finished.get(row.videoId);
    return { ...row, duration: cellDuration((finished ?? tick) - started) };
  });
}
