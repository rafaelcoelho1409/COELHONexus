"use client";

/** The pipeline panel: the run as four steps (Playwright, ElasticSearch, Qdrant, Neo4j) with one overall state, an elapsed clock and
 * the actions that belong to a run — Stop while it works, Start/Resume once it has stopped, and Wipe cache. The per-video × per-store
 * table and the LLM usage open in side sheets. Port of
 * `static/js/ycs/pipeline_panel.js` + `features/ycs/shared/pipeline_panel.py`.
 *
 * Playwright and ElasticSearch poll the SAME extract task (there is no separate ES task) and read different sub-phase targets out
 * of its `phase` field; Qdrant and Neo4j poll the per-store aggregator (the dispatch's ids for those phases are the extract id
 * reused as a lookup key, not task ids). */

import { useMutation, useQuery } from "@tanstack/react-query";
import { ListChecks } from "lucide-react";
import { useRouter } from "next/navigation";
import type { JSX } from "react";
import { useEffect, useRef, useState } from "react";

import {
  getYcsAdminPipelineExtractIdLlmCountersOptions,
  getYcsAdminPipelineExtractIdStreamPhaseOptions,
  getYcsAdminTaskTaskIdOptions,
} from "@/shared/api";
import { apiErrorMessage } from "@/shared/lib/api-error";
import { replayPipelineNode, rerunPipeline, stopPipeline, wipePipeline } from "../api";
import {
  extractBarState,
  extractSuccessLabel,
  fmtElapsed,
  isTerminal,
  type PhaseSnapshot,
  type PipelinePrefix,
  phaseAnnouncement,
  pipelineOverall,
  STATUS_WORD_LABEL,
  streamBarState,
  streamLabel,
  streamPct,
  subPhaseLabel,
  subPhasePct,
  successHint,
  wipeSummaryLine,
} from "../domain";
import { llmCountersSchema, taskSnapshotSchema } from "../entities";
import { useYcsOverlays } from "../hooks/use-overlays";
import type { PipelineTracking } from "../hooks/use-pipeline-tracking";
import { useVideoRows } from "../hooks/use-video-rows";
import {
  ingestionHref,
  LLM_USAGE_REFRESH_MS,
  PIPELINE_POLL_BACKOFF_AFTER_MS,
  PIPELINE_POLL_BACKOFF_MS,
  PIPELINE_POLL_MS,
} from "../params";
import { ActionButton } from "./action-button";
import { IngestionAgenticStructure } from "./ingestion-agentic-structure";
import type { Step, StepView } from "./pipeline-steps";
import { PipelineUsageSheet } from "./pipeline-usage-sheet";
import { PipelineVideosSheet } from "./pipeline-videos-sheet";
import { StatusBadge } from "./status-badge";

const PREFIXES: ReadonlyArray<PipelinePrefix> = ["playwright", "elasticsearch", "qdrant", "neo4j"];

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
  neo4j: "DeepAgent entity extraction, transcript-evidence validation, and Neo4j graph writes.",
};

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

function terminalProgressState(total: number, failedCount: number): string {
  if (failedCount === 0) return "SUCCESS";
  return total > 0 && failedCount >= total ? "FAILURE" : "PARTIAL";
}

function nodeSnapshot(snapshot: PhaseSnapshot, node: PipelinePrefix): PhaseSnapshot {
  const progress =
    snapshot.meta?.node_progress?.[node] ??
    (isTerminal(snapshot.state) ? snapshot.result?.node_progress?.[node] : null);
  if (!progress) return snapshot;
  const terminalState =
    progress.current >= progress.total
      ? terminalProgressState(progress.total, progress.failed_ids.length)
      : snapshot.state;
  const state = progress.state || terminalState;
  return {
    ...snapshot,
    state,
    meta: {
      ...(snapshot.meta ?? {}),
      node_state: state,
      phase: progress.phase,
      current: progress.current,
      total: progress.total,
      completed_ids: progress.completed_ids,
      failed_ids: progress.failed_ids,
      no_transcript_ids: progress.no_transcript_ids,
    },
  };
}

function finalStoreSnapshot(
  snapshot: PhaseSnapshot,
  extraction: PhaseSnapshot,
  node: "qdrant" | "neo4j",
): PhaseSnapshot {
  const progress = extraction.result?.node_progress?.[node];
  if (!isTerminal(extraction.state)) return snapshot;
  if (progress && isTerminal(progress.state)) {
    return {
      ...snapshot,
      state: progress.state,
      meta: {
        ...(snapshot.meta ?? {}),
        node_state: progress.state,
        phase: progress.phase,
        current: progress.current,
        total: progress.total,
        completed_ids: progress.completed_ids,
        failed_ids: progress.failed_ids,
      },
    };
  }
  const meta = snapshot.meta;
  if (meta?.total == null || meta.current == null || meta.current < meta.total) return snapshot;
  const state = terminalProgressState(meta.total, meta.failed_ids?.length ?? 0);
  return {
    ...snapshot,
    state,
    meta: {
      ...(snapshot.meta ?? {}),
      node_state: state,
    },
  };
}

export function PipelinePanel({
  tracking,
  onPhaseDone,
  onAllDone,
}: {
  tracking: PipelineTracking;
  onPhaseDone: () => void;
  onAllDone: () => void;
}): JSX.Element {
  const router = useRouter();
  const { confirm, toast } = useYcsOverlays();
  const [sheet, setSheet] = useState<"videos" | "usage" | null>(null);

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
  const qdrantSnap = finalStoreSnapshot(toSnapshot(qdrantQuery), extractSnap, "qdrant");
  const neo4jSnap = finalStoreSnapshot(toSnapshot(neo4jQuery), extractSnap, "neo4j");
  const snapshots: Record<PipelinePrefix, PhaseSnapshot> = {
    playwright: nodeSnapshot(extractSnap, "playwright"),
    elasticsearch: nodeSnapshot(extractSnap, "elasticsearch"),
    qdrant: qdrantSnap,
    neo4j: neo4jSnap,
  };
  const finalNodeProgress = extractSnap.result?.node_progress;
  const allTerminal =
    !extractQuery.isError &&
    (!qdrantQuery.isError || Boolean(finalNodeProgress?.qdrant)) &&
    (!neo4jQuery.isError || Boolean(finalNodeProgress?.neo4j)) &&
    PREFIXES.every((p) => isTerminal(snapshots[p].state));
  // Counters follow the run while it works, and are fetched once more whenever the sheet is opened after it has ended.
  const countersQuery = useQuery({
    ...getYcsAdminPipelineExtractIdLlmCountersOptions({ path: { extract_id: tracking.neo4j } }),
    retry: 0,
    enabled: !allTerminal || sheet === "usage",
    refetchInterval: allTerminal ? false : LLM_USAGE_REFRESH_MS,
  });
  const usage = llmCountersSchema.safeParse(countersQuery.data);

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
  const videoRows = useVideoRows({
    videoIds: tracking.videoIds,
    items: allItems,
    snapshots,
    noTranscriptIds,
  });

  const [stopped, setStopped] = useState(false);
  const [wipeLine, setWipeLine] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (allTerminal || stopped) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [allTerminal, stopped]);
  const elapsed = fmtElapsed((now - tracking.startedAtMs) / 1000);

  // One library refresh per phase as it first reaches SUCCESS, then a final sweep on the terminal
  // transition — the Library refreshes on these ticks instead of polling.
  const phaseDoneFired = useRef<Set<PipelinePrefix>>(new Set());
  const allDoneFired = useRef(false);
  useEffect(() => {
    const states: ReadonlyArray<readonly [PipelinePrefix, string]> = [
      ...PREFIXES.map((prefix) => [prefix, snapshots[prefix].state] as const),
    ];
    for (const [prefix, state] of states) {
      if (isTerminal(state) && !phaseDoneFired.current.has(prefix)) {
        phaseDoneFired.current.add(prefix);
        onPhaseDone();
      }
    }
  }, [snapshots, onPhaseDone]);
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
      else toast("Start/Resume returned no phase ids.");
    },
  });
  const replay = useMutation({
    mutationFn: (nodeId: string) => replayPipelineNode(tracking.extract, nodeId),
    onSuccess: (queued) => {
      if (queued) router.push(ingestionHref(queued.phases));
      else toast("Node replay returned no phase ids.");
    },
  });

  function stepView(prefix: PipelinePrefix): StepView {
    const snap = snapshots[prefix];
    let state: string;
    let pct: number;
    let label: string;
    const hint = successHint(snap, prefix);
    const nodeState = snap.meta?.node_state;
    if (nodeState === "SUCCESS") {
      state = "success";
      pct = 100;
      label = "Done";
    } else if (nodeState === "PARTIAL" || snap.state === "PARTIAL") {
      state = "partial";
      pct = 100;
      label =
        prefix === "playwright" || prefix === "elasticsearch"
          ? subPhaseLabel(snap, prefix)
          : streamLabel(snap, prefix);
    } else if (nodeState === "FAILURE") {
      state = "failure";
      pct = 100;
      label =
        prefix === "playwright" || prefix === "elasticsearch"
          ? subPhaseLabel({ ...snap, state: "FAILURE" }, prefix)
          : streamLabel({ ...snap, state: "FAILURE" }, prefix);
    } else if (nodeState === "CANCELLED") {
      state = "cancelled";
      pct = 100;
      label = "Cancelled";
    } else if (snap.state === "SUCCESS") {
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
      pct = subPhasePct(snap, prefix);
      label = subPhaseLabel(snap, prefix);
      // Playwright and ElasticSearch share one task: while it works on the other one's sub-phase this one is still waiting.
      state = label === "Queued" ? "pending" : snap.state.toLowerCase();
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

  const steps: Step[] = PREFIXES.map((prefix) => ({
    key: prefix,
    title: BAR_TITLES[prefix],
    defaultHint: BAR_HINTS[prefix],
    view: stepView(prefix),
  }));
  const overall = pipelineOverall(
    steps.map((step) => step.view.state),
    stopped,
  );
  const running = !allTerminal && !stopped;
  const resumable = overall === "failed" || overall === "partial" || overall === "cancelled";
  const announcement = phaseAnnouncement(
    steps.map((step) => ({ title: step.title, barState: step.view.state })),
    overall,
  );

  async function handleStop(): Promise<void> {
    const ok = await confirm({
      title: "Stop the pipeline?",
      message:
        "The currently-running phase gets SIGTERM; queued phases are cancelled. Completed phases keep their writes — use Start/Resume to run this batch again from the Source phase.",
      confirmLabel: "Stop",
    });
    if (!ok) return;
    stop.mutate();
  }

  async function handleReplay(nodeId: string): Promise<void> {
    if (running || replay.isPending) return;
    const ok = await confirm({
      title: `Replay from ${nodeId}?`,
      message: "This clears the selected stage's saved output and downstream results, then reruns from this node. Completed upstream stages remain available.",
      confirmLabel: "Replay",
      tone: "primary",
    });
    if (ok) replay.mutate(nodeId);
  }

  async function handleWipe(): Promise<void> {
    const ok = await confirm({
      title: "Wipe cache for these videos?",
      message: `Deletes every cached artifact for the ${videoCount || ""} video${videoCount === 1 ? "" : "s"} in this pipeline — ES metadata + transcripts, Qdrant hybrid points, Neo4j Document and Video nodes. The next Start/Resume will run the full chain from scratch (no Phase 1 cache hits, no Phase 3 skip-on-video_id). Entity nodes are left intact. This cannot be undone.`,
      confirmLabel: "Wipe",
    });
    if (!ok) return;
    wipe.mutate();
  }

  const videoCount = tracking.videoIds.length || allItems.length;
  return (
    <section aria-label="Agentic AI ingestion progress" className="flex min-w-0 flex-col gap-3">
      {/* The one place a screen reader hears the run: it changes when a phase changes state, not at every poll. */}
      <p role="status" className="sr-only">
        {announcement}
      </p>

      <header className="flex flex-wrap items-center gap-2">
        <div className="flex min-w-0 items-baseline gap-1.5">
          <h2 className="whitespace-nowrap text-base font-medium">Agentic AI</h2>
          <span className="whitespace-nowrap font-mono text-xs text-muted-foreground">
            {videoCount ? `· ${videoCount} video${videoCount === 1 ? "" : "s"}` : ""}
          </span>
        </div>
        <StatusBadge tone={overall} label={STATUS_WORD_LABEL[overall]} />
        <span className="whitespace-nowrap tabular font-mono text-xs text-muted-foreground">{elapsed}</span>
        <ActionButton
          type="button"
          aria-haspopup="dialog"
          onClick={() => setSheet("videos")}
          className="ml-auto whitespace-nowrap px-2"
        >
          <ListChecks aria-hidden />
          {`Videos (${videoCount})`}
        </ActionButton>
      </header>

      {wipeLine ? <p className="text-xs text-muted-foreground">{wipeLine}</p> : null}

      <IngestionAgenticStructure
        steps={steps}
        actions={{
          start: {
            label: resumable ? "Resume" : "Start",
            pendingLabel: resumable ? "Resuming…" : "Starting…",
            pending: rerun.isPending,
            disabled: running || rerun.isPending,
            title: running
              ? "An ingestion run is already active."
              : resumable
                ? "Resume by re-dispatching this batch from Source; completed cache entries may be reused."
                : "Start this batch again from Source.",
            onClick: () => rerun.mutate(),
          },
          stop: {
            label: "Stop",
            pendingLabel: "Stopping…",
            pending: stop.isPending,
            disabled: !running || stop.isPending,
            title: running ? undefined : "There is no active ingestion run to stop.",
            onClick: () => void handleStop(),
          },
          wipe: {
            label: "Wipe",
            pendingLabel: "Wiping…",
            pending: wipe.isPending,
            disabled: wipe.isPending,
            title: wipe.isPending ? "Ingestion cache wipe is in progress." : undefined,
            onClick: () => void handleWipe(),
          },
        }}
        onOpenUsage={() => setSheet("usage")}
        onPlayNode={allTerminal ? (nodeId) => void handleReplay(nodeId) : undefined}
        ariaLabel="Pipeline progress"
      />

      {stop.isError ? (
        <p role="alert" className="text-sm text-destructive-text">
          Stop failed — {apiErrorMessage(stop.error)}
        </p>
      ) : null}
      {wipe.isError ? (
        <p role="alert" className="text-sm text-destructive-text">
          Wipe failed — {apiErrorMessage(wipe.error)}
        </p>
      ) : null}
      {rerun.isError ? (
        <p role="alert" className="text-sm text-destructive-text">
          Start/Resume failed — {apiErrorMessage(rerun.error)}
        </p>
      ) : null}
      {replay.isError ? (
        <p role="alert" className="text-sm text-destructive-text">
          Node replay failed — {apiErrorMessage(replay.error)}
        </p>
      ) : null}

      {sheet === "videos" ? <PipelineVideosSheet rows={videoRows} onClose={() => setSheet(null)} /> : null}
      {sheet === "usage" ? (
        <PipelineUsageSheet usage={usage.success ? usage.data : null} onClose={() => setSheet(null)} />
      ) : null}
    </section>
  );
}
