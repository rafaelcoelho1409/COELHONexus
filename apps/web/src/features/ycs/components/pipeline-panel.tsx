"use client";

/** The pipeline panel: four live progress bars (Playwright, ElasticSearch, Qdrant, Neo4j), an elapsed
 * clock, the Stop / Retry / Wipe cache / Dismiss actions and the per-video × per-store table. Port of
 * `static/js/ycs/pipeline_panel.js` + `features/ycs/shared/pipeline_panel.py`.
 *
 * Playwright and ElasticSearch poll the SAME extract task (there is no separate ES task) and read
 * different sub-phase targets out of its `phase` field; Qdrant and Neo4j poll the per-store aggregator
 * (the dispatch's ids for those phases are the extract id reused as a lookup key, not task ids). */

import { useMutation, useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import type { JSX } from "react";
import { useEffect, useRef, useState } from "react";

import {
  getYcsAdminPipelineExtractIdLlmCountersOptions,
  getYcsAdminPipelineExtractIdStreamPhaseOptions,
  getYcsAdminTaskTaskIdOptions,
} from "@/shared/api";
import { apiErrorMessage } from "@/shared/lib/api-error";

import { rerunPipeline, stopPipeline, wipePipeline } from "../api";
import {
  cellDuration,
  extractBarState,
  extractSuccessLabel,
  fmtElapsed,
  fmtInt,
  isTerminal,
  nextTiming,
  type PhaseSnapshot,
  type PipelinePrefix,
  STORE_STATUS_LABELS,
  type StoreStatus,
  storeStatus,
  streamBarState,
  streamLabel,
  streamPct,
  subPhaseLabel,
  subPhasePct,
  successHint,
  type Timing,
  type TimingRow,
  wipeSummaryLine,
} from "../domain";
import { llmCountersSchema, taskSnapshotSchema } from "../entities";
import { useYcsOverlays } from "../hooks/use-overlays";
import type { PipelineTracking } from "../hooks/use-pipeline-tracking";
import {
  ingestionHref,
  LLM_USAGE_REFRESH_MS,
  PIPELINE_POLL_BACKOFF_AFTER_MS,
  PIPELINE_POLL_BACKOFF_MS,
  PIPELINE_POLL_MS,
} from "../params";

const BAR_TITLES: Record<PipelinePrefix, string> = {
  playwright: "Phase 1 · Playwright",
  elasticsearch: "Phase 2 · ElasticSearch",
  qdrant: "Phase 3 · Qdrant",
  neo4j: "Phase 4 · Neo4j",
};

const BAR_HINTS: Record<PipelinePrefix, string> = {
  playwright: "yt-dlp metadata + Playwright transcript scrape.",
  elasticsearch: "Bulk-index fetched transcripts (chunk-grained).",
  qdrant: "Hybrid dense + BM25 upsert.",
  neo4j: "Full-transcript LLM entity extraction.",
};

const FILL_CLASS: Record<string, string> = {
  success: "bg-success",
  partial: "bg-warning",
  failure: "bg-destructive",
  error: "bg-destructive",
  cancelled: "bg-neutral-8",
  revoked: "bg-neutral-8",
};
const DEFAULT_FILL = "bg-primary";

const LABEL_CLASS: Record<string, string> = {
  success: "text-success-text",
  partial: "text-warning-text",
  failure: "text-destructive-text",
  error: "text-destructive-text",
};
const DEFAULT_LABEL_CLASS = "text-muted-foreground";

const CELL_CLASS: Record<StoreStatus, string> = {
  queued: "text-muted-foreground",
  running: "text-running-text",
  done: "text-success-text",
  failed: "text-destructive-text",
  unavailable: "text-muted-foreground",
};

type BarView = { state: string; pct: number; label: string; hint: string | null };

type AllItem = { id: string; title?: string | null; channel?: string | null };

/** Poll cadence with the legacy backoff: constant while the run is young, eased off once it is
 * clearly a long one. Returns false to stop the poll once a snapshot is terminal. */
function pollInterval(data: unknown, startedAtMs: number): number | false {
  const parsed = taskSnapshotSchema.safeParse(data);
  if (parsed.success && isTerminal(parsed.data.state)) return false;
  const elapsed = Date.now() - startedAtMs;
  return elapsed > PIPELINE_POLL_BACKOFF_AFTER_MS ? PIPELINE_POLL_BACKOFF_MS : PIPELINE_POLL_MS;
}

/** A failed poll reads as an ERROR snapshot (a transient blip shows "Failed" briefly instead of
 * tearing the panel down); a pending poll reads as PENDING. */
function toSnapshot(query: { data: unknown; isError: boolean; error: unknown }): PhaseSnapshot {
  if (query.isError) {
    return { state: "ERROR", meta: null, result: null, error: apiErrorMessage(query.error) };
  }
  return taskSnapshotSchema.parse(query.data ?? {});
}

export function PipelinePanel({
  tracking,
  onPhaseDone,
  onAllDone,
  onDismiss,
}: {
  tracking: PipelineTracking;
  onPhaseDone: () => void;
  onAllDone: () => void;
  onDismiss: () => void;
}): JSX.Element {
  const router = useRouter();
  const { confirm, toast } = useYcsOverlays();

  const extractQuery = useQuery({
    ...getYcsAdminTaskTaskIdOptions({ path: { task_id: tracking.extract } }),
    retry: 0,
    refetchInterval: (query) => pollInterval(query.state.data, tracking.startedAtMs),
  });
  const qdrantQuery = useQuery({
    ...getYcsAdminPipelineExtractIdStreamPhaseOptions({
      path: { extract_id: tracking.qdrant, phase: "qdrant" },
    }),
    retry: 0,
    refetchInterval: (query) => pollInterval(query.state.data, tracking.startedAtMs),
  });
  const neo4jQuery = useQuery({
    ...getYcsAdminPipelineExtractIdStreamPhaseOptions({
      path: { extract_id: tracking.neo4j, phase: "neo4j" },
    }),
    retry: 0,
    refetchInterval: (query) => pollInterval(query.state.data, tracking.startedAtMs),
  });
  const extractSnap = toSnapshot(extractQuery);
  const qdrantSnap = toSnapshot(qdrantQuery);
  const neo4jSnap = toSnapshot(neo4jQuery);
  const snapshots: Record<PipelinePrefix, PhaseSnapshot> = {
    playwright: extractSnap,
    elasticsearch: extractSnap,
    qdrant: qdrantSnap,
    neo4j: neo4jSnap,
  };
  const allTerminal = (Object.keys(snapshots) as PipelinePrefix[]).every((p) =>
    isTerminal(snapshots[p].state),
  );
  const countersQuery = useQuery({
    ...getYcsAdminPipelineExtractIdLlmCountersOptions({ path: { extract_id: tracking.neo4j } }),
    retry: 0,
    enabled: !allTerminal,
    refetchInterval: LLM_USAGE_REFRESH_MS,
  });

  // Phase 1 emits a one-shot all_items payload once yt-dlp metadata is done; later polls lack it, so
  // the latest non-empty list is kept for the per-video table. The guard makes this the sanctioned
  // "adjust state when new render data arrives" pattern — the list is set once and never shrinks.
  const [allItems, setAllItems] = useState<ReadonlyArray<AllItem>>([]);
  const incomingItems = extractSnap.meta?.all_items;
  if (incomingItems?.length && incomingItems.length !== allItems.length) {
    setAllItems(incomingItems);
  }

  const noTranscriptIds = new Set<string>([
    ...(extractSnap.meta?.no_transcript_ids ?? []),
    ...(extractSnap.result?.no_transcript_ids ?? []),
  ]);

  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  const elapsed = fmtElapsed((now - tracking.startedAtMs) / 1000);

  const [stopped, setStopped] = useState(false);
  const [wipeLine, setWipeLine] = useState<string | null>(null);

  // One library refresh per phase as it first reaches SUCCESS, then a final sweep on the terminal
  // transition — the Library refreshes on these ticks instead of polling.
  const phaseDoneFired = useRef<Set<PipelinePrefix>>(new Set());
  const allDoneFired = useRef(false);
  useEffect(() => {
    const states: ReadonlyArray<readonly [PipelinePrefix, string]> = [
      ["playwright", extractSnap.state],
      ["elasticsearch", extractSnap.state],
      ["qdrant", qdrantSnap.state],
      ["neo4j", neo4jSnap.state],
    ];
    for (const [prefix, state] of states) {
      if (state === "SUCCESS" && !phaseDoneFired.current.has(prefix)) {
        phaseDoneFired.current.add(prefix);
        onPhaseDone();
      }
    }
  }, [extractSnap, qdrantSnap, neo4jSnap, onPhaseDone]);
  useEffect(() => {
    if (!allTerminal || allDoneFired.current) return;
    allDoneFired.current = true;
    onAllDone();
  }, [allTerminal, onAllDone]);

  const stop = useMutation({
    mutationFn: () => stopPipeline(tracking.extract),
    onSuccess: () => setStopped(true),
  });
  const wipe = useMutation({
    mutationFn: () => wipePipeline(tracking.extract),
    onSuccess: (result) => {
      setWipeLine(wipeSummaryLine(result));
      onAllDone();
    },
  });
  const rerun = useMutation({
    mutationFn: () => rerunPipeline(tracking.extract),
    onSuccess: (queued) => {
      if (queued) router.push(ingestionHref(queued.phases));
      else toast("Retry returned no phase ids.");
    },
  });

  function barView(prefix: PipelinePrefix): BarView {
    const snap = snapshots[prefix];
    let state: string;
    let pct: number;
    let label: string;
    const hint = successHint(snap, prefix);
    if (snap.state === "SUCCESS") {
      if (prefix === "qdrant" || prefix === "neo4j") {
        state = streamBarState(snap);
        label = streamLabel(snap, prefix);
      } else {
        state = extractBarState(snap, prefix);
        label = extractSuccessLabel(snap, prefix);
      }
      pct = 100;
    } else if (prefix === "qdrant" || prefix === "neo4j") {
      state = snap.state.toLowerCase();
      pct = streamPct(snap, prefix);
      label = streamLabel(snap, prefix);
    } else {
      state = snap.state.toLowerCase();
      pct = subPhasePct(snap, prefix);
      label = subPhaseLabel(snap, prefix);
    }
    if (stopped && !isTerminal(snap.state)) {
      return {
        state: "cancelled",
        pct: 100,
        label: "Cancelled",
        hint: "Stopped by user — partial writes preserved.",
      };
    }
    return { state, pct, label, hint };
  }

  async function handleStop(): Promise<void> {
    const ok = await confirm({
      title: "Stop the pipeline?",
      message:
        "The currently-running phase gets SIGTERM; queued phases are cancelled. Completed phases keep their writes — re-run with the Retry button to resume.",
      confirmLabel: "Stop",
    });
    if (!ok) return;
    stop.mutate();
  }

  async function handleWipe(): Promise<void> {
    const ok = await confirm({
      title: "Wipe cache for these videos?",
      message:
        "Deletes every cached artifact for the videos in this pipeline — ES metadata + transcripts, Qdrant hybrid points, Neo4j Document and Video nodes. The next Retry will re-run the full chain from scratch (no Phase 1 cache hits, no Phase 3 skip-on-video_id). Entity nodes are left intact. This cannot be undone.",
      confirmLabel: "Wipe",
    });
    if (!ok) return;
    wipe.mutate();
  }

  async function handleDismiss(): Promise<void> {
    const ok = await confirm({
      title: "Dismiss this pipeline panel?",
      message:
        "Removes the panel from view. ES, Qdrant and Neo4j are UNAFFECTED — use Wipe cache for that. Useful to clear a completed or stale tracking entry from the page chrome.",
      confirmLabel: "Dismiss",
    });
    if (!ok) return;
    onDismiss();
  }

  const videoCount = tracking.videoIds.length || allItems.length;

  return (
    <section
      aria-label="Pipeline progress"
      aria-live="polite"
      className="mb-4 rounded-md border border-border bg-card p-3"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-base font-medium">
          Pipeline{" "}
          <span className="font-mono text-xs text-muted-foreground">
            {videoCount ? `· ${videoCount} video${videoCount === 1 ? "" : "s"}` : ""}
          </span>
        </p>
        <p className="font-mono text-xs text-muted-foreground">{elapsed}</p>
      </div>
      {wipeLine ? <p className="mt-1 text-xs text-muted-foreground">{wipeLine}</p> : null}
      <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
        {(["playwright", "elasticsearch", "qdrant", "neo4j"] as const).map((prefix) => {
          const view = barView(prefix);
          return (
            <div key={prefix}>
              <div className="flex items-baseline justify-between gap-2">
                <span className="text-sm font-medium">{BAR_TITLES[prefix]}</span>
                <span className={`font-mono text-xs ${LABEL_CLASS[view.state] ?? DEFAULT_LABEL_CLASS}`}>
                  {view.label}
                </span>
              </div>
              <div className="mt-1 h-1 w-full overflow-hidden rounded-sm bg-muted">
                <div
                  className={`h-full ${FILL_CLASS[view.state] ?? DEFAULT_FILL}`}
                  style={{ width: `${Math.max(0, Math.min(100, view.pct))}%` }}
                />
              </div>
              <p className="mt-1 text-xs text-muted-foreground">{view.hint ?? BAR_HINTS[prefix]}</p>
            </div>
          );
        })}
      </div>
      <UsageLine data={countersQuery.data} />
      <div className="mt-3 flex flex-wrap items-center gap-2">
        {!allTerminal && !stopped ? (
          <button
            type="button"
            onClick={() => void handleStop()}
            disabled={stop.isPending}
            className="min-h-9 rounded-sm border border-border px-3 text-sm text-muted-foreground hover:text-foreground disabled:opacity-50"
          >
            {stop.isPending ? "Stopping…" : "Stop"}
          </button>
        ) : null}
        <button
          type="button"
          onClick={() => rerun.mutate()}
          disabled={!(allTerminal || stopped) || rerun.isPending}
          className="min-h-9 rounded-sm border border-border px-3 text-sm text-muted-foreground hover:text-foreground disabled:opacity-50"
        >
          {rerun.isPending ? "Queuing retry…" : "Retry"}
        </button>
        <button
          type="button"
          onClick={() => void handleWipe()}
          disabled={wipe.isPending}
          className="min-h-9 rounded-sm border border-border px-3 text-sm text-muted-foreground hover:text-foreground disabled:opacity-50"
        >
          {wipe.isPending ? "Wiping…" : "Wipe cache"}
        </button>
        <button
          type="button"
          onClick={() => void handleDismiss()}
          className="min-h-9 rounded-sm border border-border px-3 text-sm text-muted-foreground hover:text-foreground"
        >
          Dismiss
        </button>
      </div>
      {stop.isError ? (
        <p role="alert" className="mt-2 text-sm text-destructive-text">
          Stop failed — {apiErrorMessage(stop.error)}
        </p>
      ) : null}
      {wipe.isError ? (
        <p role="alert" className="mt-2 text-sm text-destructive-text">
          Wipe failed — {apiErrorMessage(wipe.error)}
        </p>
      ) : null}
      {rerun.isError ? (
        <p role="alert" className="mt-2 text-sm text-destructive-text">
          Retry failed — {apiErrorMessage(rerun.error)}
        </p>
      ) : null}
      <VideoTable
        videoIds={tracking.videoIds}
        allItems={allItems}
        snapshots={snapshots}
        noTranscriptIds={noTranscriptIds}
      />
    </section>
  );
}

function UsageLine({ data }: { data: unknown }): JSX.Element | null {
  const parsed = llmCountersSchema.safeParse(data);
  if (!parsed.success) return null;
  const total = parsed.data.total ?? null;
  if (!total || total.calls <= 0) return null;
  const reasoning = total.reasoning_tokens > 0 ? ` · ${fmtInt(total.reasoning_tokens)} reasoning tokens` : "";
  return (
    <p className="mt-3 text-xs text-muted-foreground">
      {fmtInt(total.calls)} calls · {fmtInt(total.tokens_in)} input tokens · {fmtInt(total.tokens_out)} output
      tokens{reasoning}
    </p>
  );
}

type VideoTableRow = {
  videoId: string;
  title: string;
  channel: string | null;
  cells: Record<PipelinePrefix, StoreStatus>;
  anyRunning: boolean;
  allDone: boolean;
  anyFailed: boolean;
  duration: string;
};

function VideoTable({
  videoIds,
  allItems,
  snapshots,
  noTranscriptIds,
}: {
  videoIds: ReadonlyArray<string>;
  allItems: ReadonlyArray<AllItem>;
  snapshots: Record<PipelinePrefix, PhaseSnapshot>;
  noTranscriptIds: ReadonlySet<string>;
}): JSX.Element | null {
  const metaById = new Map(allItems.filter((v) => v.id).map((v) => [v.id, v]));
  const ids = videoIds.length ? videoIds : allItems.map((v) => v.id).filter(Boolean);

  const rows: VideoTableRow[] = ids.map((videoId) => {
    const meta = metaById.get(videoId);
    const cells: Record<PipelinePrefix, StoreStatus> = {
      playwright: storeStatus(videoId, "playwright", snapshots.playwright, null),
      elasticsearch: storeStatus(videoId, "elasticsearch", snapshots.elasticsearch, noTranscriptIds),
      qdrant: storeStatus(videoId, "qdrant", snapshots.qdrant, noTranscriptIds),
      neo4j: storeStatus(videoId, "neo4j", snapshots.neo4j, noTranscriptIds),
    };
    const values = Object.values(cells);
    const anyRunning = values.includes("running");
    const anyFailed = values.includes("failed");
    const allDone = values.every((s) => s === "done" || s === "unavailable");
    return {
      videoId,
      title: meta?.title || videoId,
      channel: meta?.channel ?? null,
      cells,
      anyRunning,
      allDone,
      anyFailed,
      duration: "",
    };
  });

  // Timing lives in a ref the render never reads: a dep-less effect snapshots the row summaries and
  // the one-second ticker stamps starts/finishes through the pure `nextTiming`. Reading either ref
  // during render would violate the React compiler's purity rules.
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

  const withDurations = rows.map((row) => {
    const started = timing.started.get(row.videoId);
    if (started == null) return { ...row, duration: "—" };
    const finished = timing.finished.get(row.videoId);
    return { ...row, duration: cellDuration((finished ?? tick) - started) };
  });

  if (!ids.length) return null;

  return (
    <section
      // biome-ignore lint/a11y/noNoninteractiveTabindex: a scrollable region must be reachable by keyboard (WCAG 2.1.1)
      tabIndex={0}
      aria-label="Per-video pipeline progress"
      className="mt-3 max-h-64 overflow-y-auto rounded-sm border border-border"
    >
      <table className="w-full border-collapse text-left text-xs">
        <thead>
          <tr className="bg-card">
            <th scope="col" className="px-2 py-1 font-medium">
              Video
            </th>
            <th scope="col" className="px-2 py-1 font-medium" title="Playwright transcript fetch">
              PW
            </th>
            <th scope="col" className="px-2 py-1 font-medium" title="ElasticSearch chunk-commit">
              ES
            </th>
            <th scope="col" className="px-2 py-1 font-medium">
              Qdrant
            </th>
            <th scope="col" className="px-2 py-1 font-medium">
              Neo4j
            </th>
            <th scope="col" className="px-2 py-1 text-right font-medium">
              Time
            </th>
          </tr>
        </thead>
        <tbody>
          {withDurations.map((row) => (
            <tr key={row.videoId} className="border-t border-border">
              <td className="max-w-60 truncate px-2 py-1" title={`${row.title} · ${row.videoId}`}>
                {row.title}
                {row.channel ? <span className="text-muted-foreground"> · {row.channel}</span> : null}
              </td>
              {(["playwright", "elasticsearch", "qdrant", "neo4j"] as const).map((prefix) => (
                <td key={prefix} className={`px-2 py-1 ${CELL_CLASS[row.cells[prefix]]}`}>
                  {STORE_STATUS_LABELS[row.cells[prefix]]}
                </td>
              ))}
              <td className="px-2 py-1 text-right font-mono">{row.duration}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
