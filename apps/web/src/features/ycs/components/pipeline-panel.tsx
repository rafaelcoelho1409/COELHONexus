"use client";

/** The pipeline panel: the run as four steps (Playwright, ElasticSearch, Qdrant, Neo4j) with one overall state, an elapsed clock and
 * the actions that belong to a run — Stop while it works, Retry once it has stopped — and, apart from them, the two that end it for
 * good (Dismiss, Wipe cache). The per-video × per-store table and the LLM usage open in side sheets. Port of
 * `static/js/ycs/pipeline_panel.js` + `features/ycs/shared/pipeline_panel.py`.
 *
 * Playwright and ElasticSearch poll the SAME extract task (there is no separate ES task) and read different sub-phase targets out
 * of its `phase` field; Qdrant and Neo4j poll the per-store aggregator (the dispatch's ids for those phases are the extract id
 * reused as a lookup key, not task ids). */

import { useMutation, useQuery } from "@tanstack/react-query";
import { ChartColumn, ListChecks } from "lucide-react";
import { useRouter } from "next/navigation";
import type { JSX } from "react";
import { useEffect, useRef, useState } from "react";

import {
  getYcsAdminPipelineExtractIdLlmCountersOptions,
  getYcsAdminPipelineExtractIdStreamPhaseOptions,
  getYcsAdminTaskTaskIdOptions,
} from "@/shared/api";
import { apiErrorMessage } from "@/shared/lib/api-error";
import { Button } from "@/shared/ui/button";

import { rerunPipeline, stopPipeline, wipePipeline } from "../api";
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
import { PipelineSteps, type Step, type StepView } from "./pipeline-steps";
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
  neo4j: "Full-transcript LLM entity extraction.",
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
  const qdrantSnap = toSnapshot(qdrantQuery);
  const neo4jSnap = toSnapshot(neo4jQuery);
  const snapshots: Record<PipelinePrefix, PhaseSnapshot> = {
    playwright: extractSnap,
    elasticsearch: extractSnap,
    qdrant: qdrantSnap,
    neo4j: neo4jSnap,
  };
  const allTerminal = PREFIXES.every((p) => isTerminal(snapshots[p].state));
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

  function stepView(prefix: PipelinePrefix): StepView {
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
  const announcement = phaseAnnouncement(
    steps.map((step) => ({ title: step.title, barState: step.view.state })),
    overall,
  );

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
      message: `Deletes every cached artifact for the ${videoCount || ""} video${videoCount === 1 ? "" : "s"} in this pipeline — ES metadata + transcripts, Qdrant hybrid points, Neo4j Document and Video nodes. The next Retry will re-run the full chain from scratch (no Phase 1 cache hits, no Phase 3 skip-on-video_id). Entity nodes are left intact. This cannot be undone.`,
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
  const retryIsPrimary = !running && overall !== "done";

  return (
    <section aria-label="Pipeline progress" className="mb-6 flex flex-col gap-3">
      {/* The one place a screen reader hears the run: it changes when a phase changes state, not at every poll. */}
      <p role="status" className="sr-only">
        {announcement}
      </p>

      <header className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <h2 className="text-base font-medium">Pipeline</h2>
        <span className="font-mono text-xs text-muted-foreground">
          {videoCount ? `· ${videoCount} video${videoCount === 1 ? "" : "s"}` : ""}
        </span>
        <StatusBadge tone={overall} label={STATUS_WORD_LABEL[overall]} />
        <span className="tabular font-mono text-xs text-muted-foreground">{elapsed}</span>
        <div className="ml-auto flex items-center gap-2">
          {running ? (
            <Button
              type="button"
              variant="outline"
              size="lg"
              onClick={() => void handleStop()}
              disabled={stop.isPending}
            >
              {stop.isPending ? "Stopping…" : "Stop"}
            </Button>
          ) : null}
          <Button
            type="button"
            variant={retryIsPrimary ? "default" : "outline"}
            size="lg"
            onClick={() => rerun.mutate()}
            disabled={running || rerun.isPending}
          >
            {rerun.isPending ? "Queuing retry…" : "Retry"}
          </Button>
        </div>
      </header>

      {wipeLine ? <p className="text-xs text-muted-foreground">{wipeLine}</p> : null}

      <PipelineSteps steps={steps} />

      <footer className="flex flex-wrap items-center gap-2 border-t border-border pt-3">
        <Button
          type="button"
          variant="outline"
          size="lg"
          aria-haspopup="dialog"
          onClick={() => setSheet("videos")}
        >
          <ListChecks aria-hidden />
          {`Videos (${videoCount})`}
        </Button>
        <Button
          type="button"
          variant="outline"
          size="lg"
          aria-haspopup="dialog"
          onClick={() => setSheet("usage")}
        >
          <ChartColumn aria-hidden />
          LLM usage
        </Button>
        {/* Apart from the routine actions: what ends a run's data or its panel for good. */}
        <div className="ml-auto flex items-center gap-2">
          <Button type="button" variant="ghost" size="lg" onClick={() => void handleDismiss()}>
            Dismiss
          </Button>
          <Button
            type="button"
            variant="destructive"
            size="lg"
            onClick={() => void handleWipe()}
            disabled={wipe.isPending}
          >
            {wipe.isPending ? "Wiping…" : "Wipe cache"}
          </Button>
        </div>
      </footer>

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
          Retry failed — {apiErrorMessage(rerun.error)}
        </p>
      ) : null}

      {sheet === "videos" ? <PipelineVideosSheet rows={videoRows} onClose={() => setSheet(null)} /> : null}
      {sheet === "usage" ? (
        <PipelineUsageSheet usage={usage.success ? usage.data : null} onClose={() => setSheet(null)} />
      ) : null}
    </section>
  );
}
