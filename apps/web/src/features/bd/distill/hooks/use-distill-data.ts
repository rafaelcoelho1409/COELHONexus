"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import {
  getBdDistillBookIdRunsOptions,
  getBdDistillBookIdRunsQueryKey,
  getBdDistillBookIdRunsRunIdQueryKey,
  getBdDistillBookIdRunsRunIdReportOptions,
  getBdDistillBookIdRunsRunIdReportQueryKey,
  getBdDistillCapabilitiesOptions,
  getBdIngestionBookIdAgenticRunsAgenticRunIdContentSelectionOptions,
  getBdIngestionBookIdAgenticRunsAgenticRunIdContentSelectionQueryKey,
} from "@/shared/api";
import { useEventStream } from "@/shared/sse";

import {
  cancelDistillRun,
  distillEventsUrl,
  replayDistillRunNode,
  resumeDistillRun,
  saveContentSelection,
  startDistillRun,
  wipeDistillRun,
} from "../api";
import {
  initialDistillRunState,
  isDistillLive,
  isDistillTerminalEvent,
  reduceDistillRunState,
} from "../domain/run-state";
import {
  contentSelectionSchema,
  type DistillRun,
  distillCapabilitiesSchema,
  distillEventSchema,
  distillRunListSchema,
  learningGuideSchema,
} from "../entities";
import { DISTILL_REFRESH_MS } from "../params";

export function useDistillCapabilities() {
  return useQuery({
    ...getBdDistillCapabilitiesOptions(),
    retry: false,
    select: (data) => distillCapabilitiesSchema.parse(data),
  });
}

export function useDistillRuns(bookId: string) {
  return useQuery({
    ...getBdDistillBookIdRunsOptions({ path: { book_id: bookId } }),
    retry: false,
    select: (data) => distillRunListSchema.parse(data),
    refetchInterval: (query) => {
      const parsed = distillRunListSchema.safeParse(query.state.data);
      return parsed.success && parsed.data.some((run) => isDistillLive(run.status))
        ? DISTILL_REFRESH_MS
        : false;
    },
  });
}

export function useLearningGuide(bookId: string, runId: string, enabled: boolean) {
  return useQuery({
    ...getBdDistillBookIdRunsRunIdReportOptions({ path: { book_id: bookId, run_id: runId } }),
    enabled,
    retry: false,
    select: (data) => learningGuideSchema.parse(data),
  });
}

export function useContentSelection(bookId: string, agenticRunId: string | null) {
  const options = getBdIngestionBookIdAgenticRunsAgenticRunIdContentSelectionOptions({
    path: { book_id: bookId, agentic_run_id: agenticRunId ?? "" },
  });
  return useQuery({
    ...options,
    enabled: agenticRunId !== null,
    retry: false,
    select: (data) => contentSelectionSchema.parse(data),
  });
}

export function useSaveContentSelection(bookId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      agenticRunId,
      expectedRevision,
      overrides,
    }: {
      agenticRunId: string;
      expectedRevision: number;
      overrides: { page_number: number; disposition: "include" | "exclude" }[];
    }) => saveContentSelection(bookId, agenticRunId, expectedRevision, overrides),
    onSuccess: (selection) => {
      const queryKey = getBdIngestionBookIdAgenticRunsAgenticRunIdContentSelectionQueryKey({
        path: { book_id: bookId, agentic_run_id: selection.agentic_run_id },
      });
      queryClient.setQueryData(queryKey, selection);
      void queryClient.invalidateQueries({ queryKey }, { cancelRefetch: false });
      void queryClient.invalidateQueries(
        {
          predicate: (query) =>
            query.queryKey[0] === "bd" &&
            query.queryKey[1] === "processed-book" &&
            query.queryKey[2] === bookId &&
            query.queryKey[3] === selection.agentic_run_id,
        },
        { cancelRefetch: false },
      );
    },
    onError: (_error, variables) => {
      const queryKey = getBdIngestionBookIdAgenticRunsAgenticRunIdContentSelectionQueryKey({
        path: { book_id: bookId, agentic_run_id: variables.agenticRunId },
      });
      void queryClient.invalidateQueries({ queryKey });
      void queryClient.invalidateQueries({
        predicate: (query) =>
          query.queryKey[0] === "bd" &&
          query.queryKey[1] === "processed-book" &&
          query.queryKey[2] === bookId &&
          query.queryKey[3] === variables.agenticRunId,
      });
    },
  });
}

export function useDistillRunStream(bookId: string, run: DistillRun | null) {
  const queryClient = useQueryClient();
  const [eventState, setEventState] = useState<ReturnType<typeof initialDistillRunState> | null>(null);
  const current = run
    ? eventState?.runId === run.run_id
      ? reconcileRunState(run, eventState)
      : initialDistillRunState(run)
    : null;
  const runId = run?.run_id ?? null;
  const runStatus = current?.status ?? null;
  const options = useMemo(
    () =>
      runId && runStatus && isDistillLive(runStatus)
        ? {
            url: distillEventsUrl(bookId, runId),
            parse: (data: unknown) => distillEventSchema.parse(data),
            isTerminal: isDistillTerminalEvent,
          }
        : null,
    [bookId, runId, runStatus],
  );
  const stream = useEventStream(options, (event) => {
    if (!run) return;
    setEventState((previous) => {
      const state = previous?.runId === run.run_id ? previous : initialDistillRunState(run);
      const next = reduceDistillRunState(state, event);
      const observedWork =
        event.kind !== "run_queued" && (event.node !== undefined || event.progress !== undefined);
      return next.status === "queued" && observedWork ? { ...next, status: "running" } : next;
    });
    if (isDistillTerminalEvent(event)) {
      void queryClient.invalidateQueries({
        queryKey: getBdDistillBookIdRunsQueryKey({ path: { book_id: bookId } }),
      });
      void queryClient.invalidateQueries({
        queryKey: getBdDistillBookIdRunsRunIdQueryKey({ path: { book_id: bookId, run_id: run.run_id } }),
      });
      void queryClient.invalidateQueries({
        queryKey: getBdDistillBookIdRunsRunIdReportQueryKey({
          path: { book_id: bookId, run_id: run.run_id },
        }),
      });
    }
  });
  return { ...stream, state: current };
}

function reconcileRunState(
  snapshot: DistillRun,
  streamed: ReturnType<typeof initialDistillRunState>,
): ReturnType<typeof initialDistillRunState> {
  const snapshotIsLive = isDistillLive(snapshot.status);
  const streamedIsLive = isDistillLive(streamed.status);

  // A Resume reuses the run id. When the server moves that run from a
  // terminal state back to queued/running, discard the old stream state so
  // it cannot keep the page stuck on the previous failure.
  if (snapshotIsLive && !streamedIsLive) {
    return initialDistillRunState(snapshot);
  }

  const currentNode = snapshot.progress.current_node ?? streamed.progress.current_node;
  const currentTask =
    snapshot.progress.current_task ??
    (streamed.progress.current_node === currentNode ? streamed.progress.current_task : null);
  const progress = {
    ...streamed.progress,
    ...snapshot.progress,
    current_node: currentNode,
    current_task: currentTask,
    active_tasks: {
      ...streamed.progress.active_tasks,
      ...snapshot.progress.active_tasks,
    },
    node_status: {
      ...streamed.progress.node_status,
      ...snapshot.progress.node_status,
    },
    node_progress: mergeNodeProgress(streamed.progress.node_progress, snapshot.progress.node_progress),
    completed_units: Math.max(streamed.progress.completed_units, snapshot.progress.completed_units),
    completed_extractions: Math.max(
      streamed.progress.completed_extractions,
      snapshot.progress.completed_extractions,
    ),
    total_units: Math.max(streamed.progress.total_units, snapshot.progress.total_units),
    completed_chapter_guides: Math.max(
      streamed.progress.completed_chapter_guides,
      snapshot.progress.completed_chapter_guides,
    ),
    completed_chapter_visuals: Math.max(
      streamed.progress.completed_chapter_visuals,
      snapshot.progress.completed_chapter_visuals,
    ),
    completed_chapters: Math.max(streamed.progress.completed_chapters, snapshot.progress.completed_chapters),
    total_chapters: Math.max(streamed.progress.total_chapters, snapshot.progress.total_chapters),
  };

  // The run manifest is authoritative after a terminal transition. SSE may
  // have missed its last progress event, so prefer its final node statuses
  // and error while keeping the greatest persisted progress counters.
  if (!snapshotIsLive) {
    return {
      ...streamed,
      status: snapshot.status,
      progress: {
        ...snapshot.progress,
        current_task: snapshot.progress.current_task,
        node_progress: mergeNodeProgress(
          streamed.progress.node_progress,
          snapshot.progress.node_progress,
          true,
        ),
        completed_units: progress.completed_units,
        completed_extractions: progress.completed_extractions,
        total_units: progress.total_units,
        completed_chapter_guides: progress.completed_chapter_guides,
        completed_chapter_visuals: progress.completed_chapter_visuals,
        completed_chapters: progress.completed_chapters,
        total_chapters: progress.total_chapters,
      },
      error: snapshot.error ?? (snapshot.status === "failed" ? streamed.error : null),
    };
  }

  const snapshotAdvancesStatus = streamed.status === "queued" && snapshot.status === "running";
  return {
    ...streamed,
    status: snapshotAdvancesStatus ? snapshot.status : streamed.status,
    progress,
    error: snapshot.error,
  };
}

function mergeNodeProgress(
  streamed: DistillRun["progress"]["node_progress"],
  snapshot: DistillRun["progress"]["node_progress"],
  terminal = false,
): DistillRun["progress"]["node_progress"] {
  const merged: NonNullable<DistillRun["progress"]["node_progress"]> = {};
  for (const nodeId of new Set([...Object.keys(streamed ?? {}), ...Object.keys(snapshot ?? {})])) {
    const streamedNode = streamed?.[nodeId];
    const snapshotNode = snapshot?.[nodeId];
    const latest = snapshotNode ?? streamedNode;
    if (!latest) continue;
    merged[nodeId] = {
      completed_count: Math.max(streamedNode?.completed_count ?? 0, snapshotNode?.completed_count ?? 0),
      total_count: Math.max(streamedNode?.total_count ?? 0, snapshotNode?.total_count ?? 0),
      active_count: terminal ? 0 : (latest.active_count ?? 0),
    };
  }
  return merged;
}

export function useDistillActions(bookId: string) {
  const queryClient = useQueryClient();
  const refresh = () => {
    void queryClient.invalidateQueries(
      { queryKey: getBdDistillBookIdRunsQueryKey({ path: { book_id: bookId } }) },
      { cancelRefetch: false },
    );
  };
  const start = useMutation({
    mutationFn: (ingestionAiRunId: string) => startDistillRun(bookId, ingestionAiRunId),
    onSuccess: refresh,
    onError: refresh,
  });
  const resume = useMutation({
    mutationFn: (runId: string) => resumeDistillRun(bookId, runId),
    onSuccess: refresh,
    onError: refresh,
  });
  const replay = useMutation({
    mutationFn: ({ runId, nodeId }: { runId: string; nodeId: string }) =>
      replayDistillRunNode(bookId, runId, nodeId),
    onSuccess: refresh,
    onError: refresh,
  });
  const cancel = useMutation({
    mutationFn: (runId: string) => cancelDistillRun(bookId, runId),
    onSuccess: refresh,
    onError: refresh,
  });
  const wipe = useMutation({
    mutationFn: (runId: string) => wipeDistillRun(bookId, runId),
    onSuccess: (_result, runId) => {
      queryClient.setQueryData(
        getBdDistillBookIdRunsQueryKey({ path: { book_id: bookId } }),
        (current: unknown) => {
          const parsed = distillRunListSchema.safeParse(current);
          return parsed.success ? parsed.data.filter((run) => run.run_id !== runId) : undefined;
        },
      );
      queryClient.removeQueries({
        queryKey: getBdDistillBookIdRunsRunIdQueryKey({ path: { book_id: bookId, run_id: runId } }),
      });
      queryClient.removeQueries({
        queryKey: getBdDistillBookIdRunsRunIdReportQueryKey({ path: { book_id: bookId, run_id: runId } }),
      });
      refresh();
    },
    onError: refresh,
  });
  return { start, resume, replay, cancel, wipe };
}
